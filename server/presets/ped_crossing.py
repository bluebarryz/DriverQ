"""Pedestrian-vehicle crossings from `ped_vehicle_crossings`, with camera filters."""

from .request import QueryRequest
from .shared.visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

### sql

_SQL = """
    SELECT pc.*, s.scene_name
    FROM ped_vehicle_crossings pc
    JOIN scenes s ON s.scene_token = pc.scene_token
    WHERE 1=1 {where}
    ORDER BY s.scene_name, pc.ped_frame
"""


### helpers
def _load_crossings(conn, location, ego_only):
    where = []
    params = []
    if location:
        where.append("s.location = ?")
        params.append(location)
    if ego_only:
        where.append("pc.vehicle_token = 'ego'")
    clause = f"AND {' AND '.join(where)}" if where else ""
    return conn.execute(_SQL.format(where=clause), params).fetchall()


def _passes_filters(conn, r, ego_only, gap_max, visible_cams, hidden_cams):
    ped_f, veh_f = r["ped_frame"], r["veh_frame"]
    if ego_only and abs(veh_f - ped_f) > gap_max:
        return False
    f0, f1 = min(ped_f, veh_f), max(ped_f, veh_f)
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
def _format_row(r):
    ped_f, veh_f = r["ped_frame"], r["veh_frame"]
    stopped = "stopped " if r["vehicle_stopped"] else ""
    return {
        "scene_name": r["scene_name"],
        "scene_token": r["scene_token"],
        "start_frame": min(ped_f, veh_f),
        "end_frame": max(ped_f, veh_f),
        "objects": [
            {
                "token": r["ped_token"],
                "category": "human.pedestrian",
                "label": "pedestrian",
            },
            {
                "token": r["vehicle_token"],
                "category": "vehicle",
                "label": f"{stopped}vehicle",
            },
        ],
        "summary": (
            f"ped {r['ped_token'][:8]} crosses {stopped}{r['vehicle_token'][:8]} "
            f"ped@f{ped_f} veh@f{veh_f}"
        ),
    }


### entrypoint
def run(conn, q: QueryRequest) -> list[dict]:
    gap_max = q.ped_cross_frame_gap_max if q.ped_cross_frame_gap_max is not None else 10
    visible_cams = q.visible_cameras or []
    hidden_cams = q.hidden_cameras or []

    return [
        _format_row(r)
        for r in _load_crossings(conn, q.location, q.ego_only)
        if _passes_filters(conn, r, q.ego_only, gap_max, visible_cams, hidden_cams)
    ]
