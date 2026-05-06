"""Fallback handler for `q.maneuver` when no preset matched: returns matching
intersection traversals with optional camera filtering."""

from .request import QueryRequest
from .shared.visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

### sql

_SQL = """
    SELECT s.scene_name, s.scene_token,
           it.vehicle_token, it.maneuver,
           it.start_frame, it.end_frame, it.intersection_token
    FROM intersection_traversals it
    JOIN scenes s ON s.scene_token = it.scene_token
    WHERE {where}
    ORDER BY s.scene_name, it.start_frame
"""


### helpers
def _load_traversals(conn, q):
    where = ["it.maneuver = ?"]
    params = [q.maneuver]
    if q.ego_only:
        where.append("it.vehicle_token = 'ego'")
    elif q.non_ego_only:
        where.append("it.vehicle_token != 'ego'")
    if q.location:
        where.append("s.location = ?")
        params.append(q.location)
    return conn.execute(_SQL.format(where=" AND ".join(where)), params).fetchall()


def _passes_visibility(conn, r, visible_cams, hidden_cams):
    token = r["vehicle_token"]
    f0, f1 = r["start_frame"], r["end_frame"]
    if visible_cams and not appears_in_visible_cameras(
        conn, r["scene_token"], [token], visible_cams, f0, f1
    ):
        return False
    if hidden_cams and any_visibility_in_hidden_cameras(
        conn, r["scene_token"], [token], hidden_cams, f0, f1
    ):
        return False
    return True


### post-processing
def _format_row(r):
    token = r["vehicle_token"]
    return {
        "scene_name": r["scene_name"],
        "scene_token": r["scene_token"],
        "start_frame": r["start_frame"],
        "end_frame": r["end_frame"],
        "intersection_token": r["intersection_token"],
        "objects": [{"token": token, "category": "vehicle", "label": r["maneuver"]}],
        "summary": f"{token[:8]} {r['maneuver']} f{r['start_frame']}-{r['end_frame']}",
    }


### entrypoint
def run(conn, q: QueryRequest) -> list[dict]:
    rows = _load_traversals(conn, q)
    if q.ego_only:
        return [_format_row(r) for r in rows]
    visible_cams = q.visible_cameras or []
    hidden_cams = q.hidden_cameras or []
    return [
        _format_row(r)
        for r in rows
        if _passes_visibility(conn, r, visible_cams, hidden_cams)
    ]
