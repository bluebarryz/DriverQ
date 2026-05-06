"""Pair vehicles at the same intersection by maneuver role + approach geometry.
Shared by CCCscp and CCFtap."""

from collections import defaultdict
from itertools import combinations

from .geometry import effective_approach_yaw
from .visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

SINGAPORE_LOCATIONS = {
    "singapore-hollandvillage",
    "singapore-onenorth",
    "singapore-queenstown",
}


### sql

_CANDIDATES_SQL = """
    SELECT it.scene_token, s.scene_name, s.location, it.intersection_token,
           it.vehicle_token, it.maneuver, it.start_frame, it.end_frame,
           it.intersection_approach_heading,
           itgd.connector_1_start_yaw AS connector_start_yaw
    FROM intersection_traversals it
    JOIN scenes s ON s.scene_token = it.scene_token
    JOIN intersection_traversals_geometric_data itgd
      ON itgd.scene_token = it.scene_token
     AND itgd.vehicle_token = it.vehicle_token
     AND itgd.intersection_token = it.intersection_token
    WHERE {where}
      AND itgd.connector_1_start_yaw IS NOT NULL
"""


### entrypoint


def find_multi_vehicle_matches(
    conn, role_a, role_b, geometry_check,
    *, location=None, ego_only=False, non_ego_only=False,
    visible_cameras=None, hidden_cameras=None,
):
    grouped = defaultdict(list)
    for c in _load_candidates(conn, role_a | role_b, location):
        grouped[(c["scene_token"], c["intersection_token"])].append(c)

    results = []
    for (scene_token, inter_token), group in grouped.items():
        if len(group) < 2:
            continue
        if ego_only and not any(c["vehicle_token"] == "ego" for c in group):
            continue
        for x, y in combinations(group, 2):
            if x["vehicle_token"] == y["vehicle_token"]:
                continue
            match = _try_pair(
                conn, x, y, role_a, role_b, geometry_check,
                scene_token, inter_token,
                ego_only, non_ego_only, visible_cameras, hidden_cameras,
            )
            if match is not None:
                results.append(match)
    return results


### helpers


def _load_candidates(conn, allowed, location):
    where = ["it.maneuver IN (%s)" % ",".join("?" * len(allowed))]
    params = list(allowed)
    if location:
        where.append("s.location = ?")
        params.append(location)
    rows = conn.execute(
        _CANDIDATES_SQL.format(where=" AND ".join(where)), params
    ).fetchall()
    out = []
    for r in rows:
        c = dict(r)
        c["effective_yaw"] = effective_approach_yaw(
            r["connector_start_yaw"], r["intersection_approach_heading"]
        )
        out.append(c)
    return out


def _try_pair(conn, x, y, role_a, role_b, geometry_check,
              scene_token, inter_token,
              ego_only, non_ego_only, visible_cameras, hidden_cameras):
    for a, b in ((x, y), (y, x)):
        if a["maneuver"] not in role_a or b["maneuver"] not in role_b:
            continue
        if a["effective_yaw"] is None or b["effective_yaw"] is None:
            continue
        if not geometry_check(a["effective_yaw"], b["effective_yaw"]):
            continue
        tokens = (a["vehicle_token"], b["vehicle_token"])
        if ego_only and "ego" not in tokens:
            continue
        if non_ego_only and "ego" in tokens:
            continue

        start = min(a["start_frame"], b["start_frame"])
        end = max(a["end_frame"], b["end_frame"])
        if not _passes_visibility(
            conn, scene_token, a, b, start, end,
            visible_cameras, hidden_cameras,
        ):
            continue
        return _format_match(a, b, scene_token, inter_token, start, end)
    return None


def _passes_visibility(conn, scene_token, a, b, start, end,
                       visible_cameras, hidden_cameras):
    # Camera filters apply to the non-ego vehicles in the match: the single
    # non-ego partner with ego_only on, otherwise all non-ego vehicles.
    targets = [t for t in (a["vehicle_token"], b["vehicle_token"]) if t != "ego"]
    if visible_cameras and not appears_in_visible_cameras(
        conn, scene_token, targets, visible_cameras, start, end
    ):
        return False
    if hidden_cameras and any_visibility_in_hidden_cameras(
        conn, scene_token, targets, hidden_cameras, start, end
    ):
        return False
    return True


### post-processing


def _format_match(a, b, scene_token, inter_token, start, end):
    va, vb = a["vehicle_token"], b["vehicle_token"]
    return {
        "scene_name": a["scene_name"],
        "scene_token": scene_token,
        "start_frame": start,
        "end_frame": end,
        "intersection_token": inter_token,
        "objects": [
            {"token": m["vehicle_token"], "category": "vehicle", "label": m["maneuver"]}
            for m in (a, b)
        ],
        "summary": (
            f"{va[:8]}:{a['maneuver']} + "
            f"{vb[:8]}:{b['maneuver']} at {inter_token[:8]}"
        ),
    }
