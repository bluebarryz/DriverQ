"""Braking events: a sustained low-speed run preceded by a big speed drop."""

import sqlite3

from .request import QueryRequest
from .shared.visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

### sql

# (frame_idx - ROW_NUMBER) groups consecutive sub-1m/s frames into runs.
_EGO_STOPPED_RUNS_SQL = """
    WITH below_stop AS (
        SELECT f.scene_token, s.scene_name, f.frame_idx, f.ego_speed AS speed,
               f.frame_idx - ROW_NUMBER() OVER (
                   PARTITION BY f.scene_token ORDER BY f.frame_idx
               ) AS run_group
        FROM ego_poses f
        JOIN scenes s ON s.scene_token = f.scene_token
        WHERE {where}
    )
    SELECT scene_token, scene_name,
           'ego' AS instance_token,
           'vehicle.ego' AS category,
           MIN(frame_idx) AS start_frame,
           MAX(frame_idx) AS end_frame,
           COUNT(*) AS n_frames
    FROM below_stop
    GROUP BY scene_token, scene_name, run_group
    HAVING COUNT(*) >= 3
    ORDER BY scene_name, start_frame
"""

_OBJ_STOPPED_RUNS_SQL = """
    WITH below_stop AS (
        SELECT op.scene_token, s.scene_name, op.instance_token, op.category,
               op.frame_idx, op.speed,
               op.frame_idx - ROW_NUMBER() OVER (
                   PARTITION BY op.scene_token, op.instance_token ORDER BY op.frame_idx
               ) AS run_group
        FROM object_poses op
        JOIN scenes s ON s.scene_token = op.scene_token
        WHERE {where}
    )
    SELECT scene_token, scene_name, instance_token,
           MAX(category) AS category,
           MIN(frame_idx) AS start_frame,
           MAX(frame_idx) AS end_frame,
           COUNT(*) AS n_frames
    FROM below_stop
    GROUP BY scene_token, scene_name, instance_token, run_group
    HAVING COUNT(*) >= 3
    ORDER BY scene_name, start_frame, instance_token
"""

_EGO_PEAK_SQL = """
    SELECT frame_idx, ego_speed AS speed FROM ego_poses
    WHERE scene_token = ? AND frame_idx <= ?
    ORDER BY ego_speed DESC, frame_idx DESC LIMIT 1
"""

_OBJ_PEAK_SQL = """
    SELECT frame_idx, speed FROM object_poses
    WHERE scene_token = ? AND instance_token = ? AND frame_idx <= ?
    ORDER BY speed DESC, frame_idx DESC LIMIT 1
"""

_EGO_SPEED_AT_FRAME_SQL = """
    SELECT ego_speed AS speed FROM ego_poses
    WHERE scene_token = ? AND frame_idx = ? LIMIT 1
"""

_OBJ_SPEED_AT_FRAME_SQL = """
    SELECT speed FROM object_poses
    WHERE scene_token = ? AND instance_token = ? AND frame_idx = ? LIMIT 1
"""

_EGO_DECEL_START_SQL = """
    SELECT frame_idx FROM ego_poses
    WHERE scene_token = ?
      AND frame_idx >= ? AND frame_idx <= ?
      AND ego_accel <= -0.5
    ORDER BY frame_idx ASC LIMIT 1
"""

_OBJ_DECEL_START_SQL = """
    SELECT frame_idx FROM object_poses
    WHERE scene_token = ? AND instance_token = ?
      AND frame_idx >= ? AND frame_idx <= ?
      AND accel <= -0.5
    ORDER BY frame_idx ASC LIMIT 1
"""


## helpers
def _stopped_runs(conn, source_ego_only, location):
    speed_col = "f.ego_speed" if source_ego_only else "op.speed"
    where = [f"{speed_col} < 1.0"]
    params = []
    if location:
        where.append("s.location = ?")
        params.append(location)
    template = _EGO_STOPPED_RUNS_SQL if source_ego_only else _OBJ_STOPPED_RUNS_SQL
    return conn.execute(template.format(where=" AND ".join(where)), params).fetchall()


def _peak_before(conn, source_ego_only, scene_token, instance_token, upto_frame):
    if source_ego_only:
        return conn.execute(_EGO_PEAK_SQL, (scene_token, upto_frame)).fetchone()
    return conn.execute(
        _OBJ_PEAK_SQL, (scene_token, instance_token, upto_frame)
    ).fetchone()


def _speed_at(conn, source_ego_only, scene_token, instance_token, frame):
    if source_ego_only:
        return conn.execute(_EGO_SPEED_AT_FRAME_SQL, (scene_token, frame)).fetchone()
    return conn.execute(
        _OBJ_SPEED_AT_FRAME_SQL, (scene_token, instance_token, frame)
    ).fetchone()


def _decel_start(conn, source_ego_only, scene_token, instance_token, f_lo, f_hi):
    if source_ego_only:
        return conn.execute(_EGO_DECEL_START_SQL, (scene_token, f_lo, f_hi)).fetchone()
    return conn.execute(
        _OBJ_DECEL_START_SQL, (scene_token, instance_token, f_lo, f_hi)
    ).fetchone()


def _passes_visibility(
    conn,
    source_ego_only,
    scene_token,
    instance_token,
    visible_cams,
    hidden_cams,
    f0,
    f1,
):
    if source_ego_only:
        return True
    if visible_cams and not appears_in_visible_cameras(
        conn, scene_token, [instance_token], visible_cams, f0, f1
    ):
        return False
    if hidden_cams and any_visibility_in_hidden_cameras(
        conn, scene_token, [instance_token], hidden_cams, f0, f1
    ):
        return False
    return True


def _build_event(conn, source_ego_only, run, visible_cams, hidden_cams):
    scene_token = run["scene_token"]
    instance_token = run["instance_token"]
    stopped_start = run["start_frame"]
    stopped_end = run["end_frame"]

    peak = _peak_before(
        conn, source_ego_only, scene_token, instance_token, stopped_start
    )
    if peak is None or peak["speed"] is None:
        return None

    stop = _speed_at(conn, source_ego_only, scene_token, instance_token, stopped_end)
    if stop is None or stop["speed"] is None:
        return None

    speed_drop = peak["speed"] - stop["speed"]
    if speed_drop < 4.5:
        return None

    decel = _decel_start(
        conn,
        source_ego_only,
        scene_token,
        instance_token,
        peak["frame_idx"],
        stopped_start,
    )
    event_start = decel["frame_idx"] if decel else peak["frame_idx"]
    event_end = stopped_end

    if not _passes_visibility(
        conn,
        source_ego_only,
        scene_token,
        instance_token,
        visible_cams,
        hidden_cams,
        event_start,
        event_end,
    ):
        return None

    return _format_event(
        run, event_start, event_end, peak["speed"], stop["speed"], speed_drop
    )


### post-processing
def _format_event(run, event_start, event_end, peak_speed, stopped_speed, speed_drop):
    token = run["instance_token"]
    return {
        "scene_name": run["scene_name"],
        "scene_token": run["scene_token"],
        "start_frame": event_start,
        "end_frame": event_end,
        "objects": [
            {"token": token, "category": run["category"], "label": "braking"},
        ],
        "summary": (
            f"{token[:8]} braking f{event_start}-{event_end} "
            f"({run['n_frames']} stopped frames, peak={peak_speed:.2f}, "
            f"stop={stopped_speed:.2f}, drop={speed_drop:.2f})"
        ),
    }


## entrypoint
def run(conn: sqlite3.Connection, q: QueryRequest) -> list[dict]:
    visible_cams = q.visible_cameras or []
    hidden_cams = q.hidden_cameras or []
    if q.ego_only:
        sources = [True]
    elif q.non_ego_only:
        sources = [False]
    else:
        sources = [False, True]

    events = []
    for source_ego_only in sources:
        for r in _stopped_runs(conn, source_ego_only, q.location):
            event = _build_event(conn, source_ego_only, r, visible_cams, hidden_cams)
            if event is not None:
                events.append(event)

    events.sort(
        key=lambda r: (r["scene_name"], r["start_frame"], r["objects"][0]["token"])
    )
    return events
