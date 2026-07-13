"""FastAPI server for nuscenes_explorer. Serves scene data from SQLite + camera images."""

import json
import logging
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from presets import QueryRequest, run_preset

load_dotenv(Path(__file__).parent.parent / ".env")
logger = logging.getLogger("nuscenes_explorer")

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "scene_data.db"))
CAMERAS_DIR = os.environ.get(
    "CAMERAS_DIR", str(Path(__file__).parent.parent / "frontend" / "public" / "cameras")
)

app = FastAPI(title="nuscenes_explorer")


@app.on_event("startup")
def _validate_runtime_paths() -> None:
    db_parent = Path(DB_PATH).expanduser().resolve().parent
    if not db_parent.exists():
        raise RuntimeError(
            f"DB parent directory not found: {db_parent}. "
            "Check DB_PATH and docker volume mount."
        )
    if not Path(DB_PATH).expanduser().exists():
        raise RuntimeError(
            f"DB file not found: {DB_PATH}. "
            "For docker compose, set NUSCENES_DB_HOST_PATH and mount it to /data/scene_data.db."
        )

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"Cannot open DB at {DB_PATH}: {exc}. "
            "Check file permissions and volume mount."
        ) from exc

    if not os.path.isdir(CAMERAS_DIR):
        logger.warning(
            "CAMERAS_DIR does not exist: %s. /cameras static route is disabled.",
            CAMERAS_DIR,
        )


# Serve camera images as static files
if os.path.isdir(CAMERAS_DIR):
    app.mount("/cameras", StaticFiles(directory=CAMERAS_DIR), name="cameras")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_scene_token(conn: sqlite3.Connection, scene_name: str) -> str:
    scene = conn.execute(
        "SELECT scene_token FROM scenes WHERE scene_name = ?", (scene_name,)
    ).fetchone()
    if not scene:
        raise HTTPException(404, f"scene '{scene_name}' not found")
    return scene["scene_token"]


def _resolve_instance_by_prefix(
    conn: sqlite3.Connection, scene_token: str, token_prefix: str
) -> str:
    rows = conn.execute(
        """SELECT instance_token
           FROM object_trajectories
           WHERE scene_token = ? AND instance_token LIKE ?
           ORDER BY instance_token
           LIMIT 3""",
        (scene_token, f"{token_prefix}%"),
    ).fetchall()
    if not rows:
        raise HTTPException(404, f"no object matches prefix '{token_prefix}'")
    if len(rows) > 1:
        raise HTTPException(
            400,
            {
                "error": "prefix is ambiguous",
                "matches": [r["instance_token"] for r in rows],
            },
        )
    return rows[0]["instance_token"]


@app.get("/api/scenes")
def list_scenes():
    conn = get_db()
    rows = conn.execute(
        "SELECT scene_token, scene_name, location, num_frames, data_version, subset FROM scenes ORDER BY scene_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/scene/{scene_name}")
def get_scene(scene_name: str):
    conn = get_db()

    scene = conn.execute(
        "SELECT scene_token, scene_name, location, num_frames, data_version, subset FROM scenes WHERE scene_name = ?",
        (scene_name,),
    ).fetchone()
    if not scene:
        conn.close()
        raise HTTPException(404, f"scene '{scene_name}' not found")
    scene_token = scene["scene_token"]

    frame_rows = conn.execute(
        "SELECT frame_idx, timestamp, ego_x, ego_y, ego_z, ego_qw, ego_qx, ego_qy, ego_qz, ego_speed, ego_accel FROM ego_poses WHERE scene_token = ? ORDER BY frame_idx",
        (scene_token,),
    ).fetchall()

    obj_rows = conn.execute(
        "SELECT frame_idx, instance_token, category, x, y, z, qw, qx, qy, qz, width, length, height, speed, accel FROM object_poses WHERE scene_token = ? ORDER BY frame_idx",
        (scene_token,),
    ).fetchall()
    objs_by_frame: dict[int, list] = defaultdict(list)
    for r in obj_rows:
        objs_by_frame[r["frame_idx"]].append(
            {
                "token": r["instance_token"],
                "x": r["x"],
                "y": r["y"],
                "z": r["z"],
                "w": r["width"],
                "l": r["length"],
                "h": r["height"],
                "qw": r["qw"],
                "qx": r["qx"],
                "qy": r["qy"],
                "qz": r["qz"],
                "cat": r["category"],
                "speed": r["speed"],
                "accel": r["accel"],
            }
        )

    frames = []
    for f in frame_rows:
        fi = f["frame_idx"]
        frames.append(
            {
                "frame_idx": fi,
                "timestamp": f["timestamp"],
                "ego": {
                    "x": f["ego_x"],
                    "y": f["ego_y"],
                    "z": f["ego_z"],
                    "qw": f["ego_qw"],
                    "qx": f["ego_qx"],
                    "qy": f["ego_qy"],
                    "qz": f["ego_qz"],
                    "speed": f["ego_speed"],
                    "accel": f["ego_accel"],
                },
                "annotations": objs_by_frame.get(fi, []),
            }
        )

    cl_rows = conn.execute(
        "SELECT lane_token, point_idx, x, y FROM centerlines WHERE scene_token = ? ORDER BY lane_token, point_idx",
        (scene_token,),
    ).fetchall()
    centerlines_by_lane: dict[str, list] = defaultdict(list)
    for r in cl_rows:
        centerlines_by_lane[r["lane_token"]].append([r["x"], r["y"]])
    centerlines = list(centerlines_by_lane.values())

    man_rows = conn.execute(
        """SELECT it.vehicle_token, it.maneuver, it.intersection_token,
                  it.start_frame, it.end_frame,
                  itgd.connector_token, itgd.connector_1_start_yaw AS connector_start_yaw
           FROM intersection_traversals it
           LEFT JOIN intersection_traversals_geometric_data itgd
             ON itgd.scene_token = it.scene_token
            AND itgd.vehicle_token = it.vehicle_token
            AND itgd.intersection_token = it.intersection_token
           WHERE it.scene_token = ?""",
        (scene_token,),
    ).fetchall()
    maneuvers = [dict(r) for r in man_rows]

    conn.close()
    return {
        "scene_token": scene["scene_token"],
        "scene_name": scene["scene_name"],
        "location": scene["location"],
        "data_version": scene["data_version"],
        "subset": scene["subset"],
        "frames": frames,
        "centerlines": centerlines,
        "maneuvers": maneuvers,
    }


@app.get("/api/scene/{scene_name}/visibility/{frame_idx}")
def get_visibility(scene_name: str, frame_idx: int):
    conn = get_db()
    scene = conn.execute(
        "SELECT scene_token FROM scenes WHERE scene_name = ?", (scene_name,)
    ).fetchone()
    if not scene:
        conn.close()
        raise HTTPException(404)
    scene_token = scene["scene_token"]

    rows = conn.execute(
        """SELECT v.instance_token, v.camera, v.visibility_level,
                  v.bbox_x1, v.bbox_y1, v.bbox_x2, v.bbox_y2, op.category
           FROM visibility v
           JOIN object_poses op ON op.scene_token = v.scene_token AND op.frame_idx = v.frame_idx AND op.instance_token = v.instance_token
           WHERE v.scene_token = ? AND v.frame_idx = ?""",
        (scene_token, frame_idx),
    ).fetchall()
    conn.close()

    by_camera: dict[str, list] = defaultdict(list)
    for r in rows:
        by_camera[r["camera"]].append(
            {
                "token": r["instance_token"],
                "cat": r["category"],
                "visibility_level": r["visibility_level"],
                "bbox": [r["bbox_x1"], r["bbox_y1"], r["bbox_x2"], r["bbox_y2"]],
            }
        )
    return dict(by_camera)


#  GET /api/scene/{scene_name}/object/{instance_token}xw


@app.get("/api/scene/{scene_name}/object/{instance_token}")
def get_object_detail(scene_name: str, instance_token: str):
    conn = get_db()
    scene = conn.execute(
        "SELECT scene_token FROM scenes WHERE scene_name = ?", (scene_name,)
    ).fetchone()
    if not scene:
        conn.close()
        raise HTTPException(404)
    scene_token = scene["scene_token"]

    # Trajectory
    traj_rows = conn.execute(
        "SELECT frame_idx, x, y, z, speed, accel FROM object_poses WHERE scene_token = ? AND instance_token = ? ORDER BY frame_idx",
        (scene_token, instance_token),
    ).fetchall()
    if not traj_rows:
        conn.close()
        raise HTTPException(404, "object not found in scene")

    category = conn.execute(
        "SELECT category FROM object_poses WHERE scene_token = ? AND instance_token = ? LIMIT 1",
        (scene_token, instance_token),
    ).fetchone()["category"]

    trajectory = [
        {
            "frame_idx": r["frame_idx"],
            "x": r["x"],
            "y": r["y"],
            "speed": r["speed"],
            "accel": r["accel"],
        }
        for r in traj_rows
    ]

    # Visibility timeline
    vis_rows = conn.execute(
        "SELECT frame_idx, camera, visibility_level FROM visibility WHERE scene_token = ? AND instance_token = ? ORDER BY frame_idx, camera",
        (scene_token, instance_token),
    ).fetchall()
    vis_by_frame: dict[int, dict] = defaultdict(
        lambda: {"cameras": [], "best_visibility": None}
    )
    for r in vis_rows:
        entry = vis_by_frame[r["frame_idx"]]
        entry["cameras"].append(r["camera"])
        level = r["visibility_level"]
        if entry["best_visibility"] is None or (
            level and level > (entry["best_visibility"] or "")
        ):
            entry["best_visibility"] = level

    visibility_timeline = [
        {"frame_idx": fi, **data} for fi, data in sorted(vis_by_frame.items())
    ]

    # Maneuvers
    man_rows = conn.execute(
        """SELECT maneuver, start_frame, end_frame, intersection_token
           FROM intersection_traversals
           WHERE scene_token = ? AND vehicle_token = ?""",
        (scene_token, instance_token),
    ).fetchall()
    maneuvers = [dict(r) for r in man_rows]

    conn.close()
    return {
        "instance_token": instance_token,
        "category": category,
        "trajectory": trajectory,
        "visibility_timeline": visibility_timeline,
        "maneuvers": maneuvers,
    }


@app.get("/api/scene/{scene_name}/trajectory/{token_prefix}")
def get_trajectory(
    scene_name: str, token_prefix: str, stride: int = Query(1, ge=1, le=20)
):
    conn = get_db()
    try:
        scene_token = _resolve_scene_token(conn, scene_name)

        if token_prefix == "ego" or "ego".startswith(token_prefix):
            instance_token = "ego"
        else:
            instance_token = _resolve_instance_by_prefix(
                conn, scene_token, token_prefix
            )

        row = conn.execute(
            """SELECT instance_token, category, start_frame, end_frame, points_json
               FROM object_trajectories
               WHERE scene_token = ? AND instance_token = ?""",
            (scene_token, instance_token),
        ).fetchone()
        if row:
            sampled = json.loads(row["points_json"])[::stride]
            return {
                "scene_name": scene_name,
                "token_prefix": token_prefix,
                "instance_token": row["instance_token"],
                "category": row["category"],
                "start_frame": row["start_frame"],
                "end_frame": row["end_frame"],
                "point_count": len(sampled),
                "trajectory": [
                    {"frame_idx": p[0], "x": p[1], "y": p[2], "z": p[3]}
                    for p in sampled
                ],
            }

        # Legacy DB: ego row not yet backfilled into object_trajectories.
        if instance_token == "ego":
            rows = conn.execute(
                "SELECT frame_idx, ego_x AS x, ego_y AS y, ego_z AS z FROM ego_poses WHERE scene_token = ? ORDER BY frame_idx",
                (scene_token,),
            ).fetchall()
            points = [
                {"frame_idx": r["frame_idx"], "x": r["x"], "y": r["y"], "z": r["z"]}
                for r in rows[::stride]
            ]
            return {
                "scene_name": scene_name,
                "token_prefix": token_prefix,
                "instance_token": "ego",
                "category": "ego",
                "start_frame": points[0]["frame_idx"] if points else 0,
                "end_frame": points[-1]["frame_idx"] if points else 0,
                "point_count": len(points),
                "trajectory": points,
            }

        raise HTTPException(404, "trajectory not found")
    except sqlite3.OperationalError:
        scene_token = _resolve_scene_token(conn, scene_name)
        if token_prefix == "ego" or "ego".startswith(token_prefix):
            rows = conn.execute(
                "SELECT frame_idx, ego_x AS x, ego_y AS y, ego_z AS z FROM ego_poses WHERE scene_token = ? ORDER BY frame_idx",
                (scene_token,),
            ).fetchall()
            points = [
                {
                    "frame_idx": r["frame_idx"],
                    "x": r["x"],
                    "y": r["y"],
                    "z": r["z"],
                }
                for r in rows[::stride]
            ]
            return {
                "scene_name": scene_name,
                "token_prefix": token_prefix,
                "instance_token": "ego",
                "category": "ego",
                "start_frame": points[0]["frame_idx"] if points else 0,
                "end_frame": points[-1]["frame_idx"] if points else 0,
                "point_count": len(points),
                "trajectory": points,
            }

        rows = conn.execute(
            """SELECT frame_idx, instance_token, category, x, y, z
               FROM object_poses
               WHERE scene_token = ? AND instance_token LIKE ?
               ORDER BY instance_token, frame_idx""",
            (scene_token, f"{token_prefix}%"),
        ).fetchall()
        if not rows:
            raise HTTPException(404, f"no object matches prefix '{token_prefix}'")
        instance_tokens = sorted({r["instance_token"] for r in rows})
        if len(instance_tokens) > 1:
            raise HTTPException(
                400,
                {
                    "error": "prefix is ambiguous",
                    "matches": instance_tokens[:3],
                },
            )
        target = instance_tokens[0]
        filtered = [r for r in rows if r["instance_token"] == target][::stride]
        points = [
            {
                "frame_idx": r["frame_idx"],
                "x": r["x"],
                "y": r["y"],
                "z": r["z"],
            }
            for r in filtered
        ]
        return {
            "scene_name": scene_name,
            "token_prefix": token_prefix,
            "instance_token": target,
            "category": filtered[0]["category"],
            "start_frame": points[0]["frame_idx"],
            "end_frame": points[-1]["frame_idx"],
            "point_count": len(points),
            "trajectory": points,
        }
    finally:
        conn.close()


@app.post("/api/query")
def run_query(q: QueryRequest):
    conn = get_db()
    try:
        results = run_preset(conn, q)
        return {"count": len(results), "results": results}
    finally:
        conn.close()
