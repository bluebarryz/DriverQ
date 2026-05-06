"""One match per scene that contains a tracked pedestrian passing the camera filters."""

from .request import QueryRequest
from .shared.visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

_PED = "human.pedestrian%"


### sql

_SCENES_WITH_PED_SQL = """
    SELECT DISTINCT s.scene_token, s.scene_name
    FROM scenes s
    JOIN object_poses op ON op.scene_token = s.scene_token
    WHERE {where}
    ORDER BY s.scene_name
"""

_PED_INSTANCES_SQL = """
    SELECT instance_token,
           MIN(frame_idx) AS min_f,
           MAX(frame_idx) AS max_f,
           MAX(category) AS category
    FROM object_poses
    WHERE scene_token = ? AND category LIKE ?
    GROUP BY instance_token
    ORDER BY instance_token
"""


### helpers
def _scenes_with_pedestrians(conn, location):
    where = ["op.category LIKE ?"]
    params = [_PED]
    if location:
        where.append("s.location = ?")
        params.append(location)
    return conn.execute(
        _SCENES_WITH_PED_SQL.format(where=" AND ".join(where)), params
    ).fetchall()


def _ped_instances(conn, scene_token):
    return conn.execute(_PED_INSTANCES_SQL, (scene_token, _PED)).fetchall()


def _pick_pedestrian(conn, scene_token, instances, visible_cams, hidden_cams):
    for inst in instances:
        f0, f1 = inst["min_f"], inst["max_f"]
        tok = inst["instance_token"]
        if visible_cams and not appears_in_visible_cameras(
            conn, scene_token, [tok], visible_cams, f0, f1
        ):
            continue
        if hidden_cams and any_visibility_in_hidden_cameras(
            conn, scene_token, [tok], hidden_cams, f0, f1
        ):
            continue
        return inst
    return None


### post-processing
def _format_match(scene_name, scene_token, inst, cams):
    tok = inst["instance_token"]
    f0, f1 = inst["min_f"], inst["max_f"]
    summary = f"{tok[:8]} pedestrian f{f0}-{f1}"
    if cams:
        summary += f" in {','.join(cams)}"
    return {
        "scene_name": scene_name,
        "scene_token": scene_token,
        "start_frame": f0,
        "end_frame": f1,
        "objects": [
            {"token": tok, "category": inst["category"], "label": "pedestrian"}
        ],
        "summary": summary,
    }


### entrypoint
def run(conn, q: QueryRequest) -> list[dict]:
    visible_cams = q.visible_cameras or []
    hidden_cams = q.hidden_cameras or []

    results = []
    for scene in _scenes_with_pedestrians(conn, q.location):
        scene_token = scene["scene_token"]
        chosen = _pick_pedestrian(
            conn,
            scene_token,
            _ped_instances(conn, scene_token),
            visible_cams,
            hidden_cams,
        )
        if chosen is None:
            continue
        results.append(
            _format_match(scene["scene_name"], scene_token, chosen, visible_cams)
        )
    return results
