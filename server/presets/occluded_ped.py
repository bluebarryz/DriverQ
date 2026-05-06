"""Ego-vehicle pedestrian crossings where the pedestrian sweeps from one
front-side camera to the other through CAM_FRONT, with at least N
low-visibility frames in the entry camera."""

from collections import defaultdict

from .request import QueryRequest
from .shared.visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

_FRONT_CAMS = ("CAM_FRONT_LEFT", "CAM_FRONT", "CAM_FRONT_RIGHT")
_LOW_VISIBILITY = {"1", "2", "v0-40", "v40-60"}


### sql

_CROSSINGS_SQL = """
    SELECT pc.*, s.scene_name
    FROM ped_vehicle_crossings pc
    JOIN scenes s ON s.scene_token = pc.scene_token
    WHERE pc.vehicle_token = 'ego' {where}
    ORDER BY s.scene_name, pc.ped_frame
"""

_PED_VISIBILITY_SQL = """
    SELECT frame_idx, camera, visibility_level
    FROM visibility
    WHERE scene_token = ?
      AND instance_token = ?
      AND camera IN ('CAM_FRONT_LEFT', 'CAM_FRONT', 'CAM_FRONT_RIGHT')
    ORDER BY frame_idx
"""


### helpers
def _load_crossings(conn, location):
    where = ""
    params = []
    if location:
        where = "AND s.location = ?"
        params.append(location)
    return conn.execute(_CROSSINGS_SQL.format(where=where), params).fetchall()


def _load_ped_front_visibility(conn, scene_token, ped_token):
    rows = conn.execute(_PED_VISIBILITY_SQL, (scene_token, ped_token)).fetchall()
    if not rows:
        return None

    frames_by_cam = {c: [] for c in _FRONT_CAMS}
    cameras_by_frame = defaultdict(set)
    level_by_cam_frame = {}
    for r in rows:
        cam, fi = r["camera"], r["frame_idx"]
        frames_by_cam[cam].append(fi)
        cameras_by_frame[fi].add(cam)
        level_by_cam_frame[(cam, fi)] = r["visibility_level"]
    return frames_by_cam, cameras_by_frame, level_by_cam_frame


def _direction_candidates(first_left, first_right):
    # Whichever side saw the pedestrian first is treated as the entry camera.
    if first_right < first_left:
        return [
            ("CAM_FRONT_RIGHT", "CAM_FRONT_LEFT", "right_to_left"),
            ("CAM_FRONT_LEFT", "CAM_FRONT_RIGHT", "left_to_right"),
        ]
    return [
        ("CAM_FRONT_LEFT", "CAM_FRONT_RIGHT", "left_to_right"),
        ("CAM_FRONT_RIGHT", "CAM_FRONT_LEFT", "right_to_left"),
    ]


def _evaluate_direction(
    entry_cam,
    exit_cam,
    direction,
    frames_by_cam,
    cameras_by_frame,
    level_by_cam_frame,
    first_left,
    first_right,
):
    entry_frames = frames_by_cam.get(entry_cam, [])
    exit_frames = frames_by_cam.get(exit_cam, [])
    if not entry_frames or not exit_frames:
        return None

    j, l = entry_frames[0], exit_frames[-1]
    if l < j:
        return None
    if not _window_is_continuous(j, l, cameras_by_frame):
        return None
    if not _stage_progression_valid(j, l, entry_cam, exit_cam, cameras_by_frame):
        return None

    entry_in_window = [fi for fi in entry_frames if j <= fi <= l]
    if not entry_in_window:
        return None
    k = entry_in_window[-1]

    low_frames = sum(
        1
        for fi in range(j, k + 1)
        if level_by_cam_frame.get((entry_cam, fi)) in _LOW_VISIBILITY
    )
    return {
        "J": j,
        "K": k,
        "L": l,
        "low_frames": low_frames,
        "first_left": first_left,
        "first_right": first_right,
        "direction": direction,
        "entry_camera": entry_cam,
        "exit_camera": exit_cam,
    }


def _window_is_continuous(j, l, cameras_by_frame):
    visible = sorted(
        fi
        for fi, cams in cameras_by_frame.items()
        if j <= fi <= l and cams & set(_FRONT_CAMS)
    )
    return (
        bool(visible)
        and visible[0] == j
        and visible[-1] == l
        and len(visible) == (l - j + 1)
    )


def _stage_progression_valid(j, l, entry_cam, exit_cam, cameras_by_frame):
    # Stages: 0 = entry, 1 = CAM_FRONT, 2 = exit. Reachable stages may only
    # advance frame-to-frame; must start at 0 on j and reach 2 by l.
    stage_by_cam = {entry_cam: 0, "CAM_FRONT": 1, exit_cam: 2}
    if entry_cam not in cameras_by_frame.get(j, set()):
        return False
    if exit_cam not in cameras_by_frame.get(l, set()):
        return False

    reachable = set()
    for fi in range(j, l + 1):
        stages = {
            stage_by_cam[c]
            for c in cameras_by_frame.get(fi, set())
            if c in stage_by_cam
        }
        if not stages:
            return False
        if fi == j:
            reachable = {s for s in stages if s == 0}
        else:
            reachable = {s for s in stages if any(prev <= s for prev in reachable)}
        if not reachable:
            return False
    return 2 in reachable


def _compute_occluded_window(conn, scene_token, ped_token):
    loaded = _load_ped_front_visibility(conn, scene_token, ped_token)
    if loaded is None:
        return None
    frames_by_cam, cameras_by_frame, level_by_cam_frame = loaded
    if not all(frames_by_cam[c] for c in _FRONT_CAMS):
        return None

    first_left = frames_by_cam["CAM_FRONT_LEFT"][0]
    first_right = frames_by_cam["CAM_FRONT_RIGHT"][0]
    for entry, exit_cam, direction in _direction_candidates(first_left, first_right):
        match = _evaluate_direction(
            entry,
            exit_cam,
            direction,
            frames_by_cam,
            cameras_by_frame,
            level_by_cam_frame,
            first_left,
            first_right,
        )
        if match is not None:
            return match
    return None


def _passes_visibility_filters(conn, r, occ, visible_cams, hidden_cams):
    f0 = min(r["ped_frame"], r["veh_frame"], occ["J"])
    f1 = max(r["ped_frame"], r["veh_frame"], occ["L"])
    targets = [t for t in (r["ped_token"], r["vehicle_token"]) if t != "ego"]
    if visible_cams and not appears_in_visible_cameras(
        conn, r["scene_token"], targets, visible_cams, f0, f1
    ):
        return False
    if hidden_cams and any_visibility_in_hidden_cameras(
        conn, r["scene_token"], targets, hidden_cams, f0, f1
    ):
        return False
    return True


### post-processing
def _format_match(r, occ):
    stopped = "stopped " if r["vehicle_stopped"] else ""
    ped_tok, veh_tok = r["ped_token"], r["vehicle_token"]
    return {
        "scene_name": r["scene_name"],
        "scene_token": r["scene_token"],
        "start_frame": occ["J"],
        "end_frame": occ["K"],
        "objects": [
            {
                "token": ped_tok,
                "category": "human.pedestrian",
                "label": "occluded pedestrian",
            },
            {"token": veh_tok, "category": "vehicle", "label": f"{stopped}vehicle"},
        ],
        "summary": (
            f"ped {ped_tok[:8]} occluded({occ['direction']}, "
            f"entry={occ['entry_camera']}, low={occ['low_frames']}, "
            f"J={occ['J']},K={occ['K']},L={occ['L']}) "
            f"crosses {stopped}ego ped@f{r['ped_frame']} veh@f{r['veh_frame']}"
        ),
    }


### entrypoint
def run(conn, q: QueryRequest) -> list[dict]:
    min_low = q.occluded_frames_min if q.occluded_frames_min is not None else 1
    visible_cams = q.visible_cameras or []
    hidden_cams = q.hidden_cameras or []

    results = []
    for r in _load_crossings(conn, q.location):
        occ = _compute_occluded_window(conn, r["scene_token"], r["ped_token"])
        if occ is None or occ["low_frames"] < min_low:
            continue
        if not _passes_visibility_filters(conn, r, occ, visible_cams, hidden_cams):
            continue
        results.append(_format_match(r, occ))
    return results
