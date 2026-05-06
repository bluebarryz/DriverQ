"""Lane-change events from the pre-computed `lane_change_events` table."""

from .request import QueryRequest

### sql
_SQL = """
    SELECT me.*, s.scene_name
    FROM lane_change_events me
    JOIN scenes s ON s.scene_token = me.scene_token
    {where}
    ORDER BY s.scene_name, me.start_frame
"""


### post-processing
def _format_row(r):
    is_ego = r["vehicle_id"] == "__ego__"
    token = "ego" if is_ego else r["vehicle_id"]
    who = "ego" if is_ego else r["vehicle_id"][:8]
    return {
        "scene_name": r["scene_name"],
        "scene_token": r["scene_token"],
        "start_frame": r["start_frame"],
        "end_frame": r["end_frame"],
        "objects": [{"token": token, "category": "vehicle", "label": "lane-change"}],
        "summary": f"{who} lane-change f{r['start_frame']}-{r['end_frame']}",
    }


### entrypoint
def run(conn, q: QueryRequest) -> list[dict]:
    if q.ego_only:
        where = "WHERE me.vehicle_id = '__ego__'"
    elif q.non_ego_only:
        where = "WHERE me.vehicle_id != '__ego__'"
    else:
        where = ""
    rows = conn.execute(_SQL.format(where=where), []).fetchall()
    return [_format_row(r) for r in rows]
