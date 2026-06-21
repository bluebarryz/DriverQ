#!/usr/bin/env python3
"""
Export scene poses to SQLite, produces tables: scenes, ego_poses, object_poses, centerlines, lane_connectivity.

Usage:
python export_poses.py --db scene_data.db
python export_poses.py --db scene_data.db --scene scene-0001
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import time
import sqlite3
import sys

from dotenv import load_dotenv
from nuscenes.map_expansion.map_api import NuScenesMap
from shapely.geometry import Point

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_env_path)

NUSCENES_ROOT = os.environ["NUSCENES_ROOT"]
DATA_VERSION = os.environ.get("DATA_VERSION", "v1.0-trainval")
MAPS_ROOT = os.environ["MAPS_ROOT"]
MAP_PADDING = int(os.environ.get("MAP_PADDING", "80"))

_TRACKED_PREFIXES = ("vehicle.", "human.pedestrian")


def _get_subset(dataroot: str) -> str:
    return os.path.basename(os.path.normpath(dataroot))


def _ensure_scenes_columns(conn: sqlite3.Connection):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    if "data_version" not in cols:
        conn.execute("ALTER TABLE scenes ADD COLUMN data_version TEXT")
    if "subset" not in cols:
        conn.execute("ALTER TABLE scenes ADD COLUMN subset TEXT")
    conn.commit()


def _ensure_trajectory_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS object_trajectories (
            scene_token    TEXT NOT NULL REFERENCES scenes(scene_token),
            instance_token TEXT NOT NULL,
            category       TEXT NOT NULL,
            start_frame    INTEGER NOT NULL,
            end_frame      INTEGER NOT NULL,
            points_json    TEXT NOT NULL,
            PRIMARY KEY (scene_token, instance_token)
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_object_trajectories_scene_instance ON object_trajectories(scene_token, instance_token)"
    )
    conn.commit()


def load_table(name: str, dataroot: str):
    path = os.path.join(dataroot, DATA_VERSION, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _find_lane_at_point(
    nusc_map: NuScenesMap, x: float, y: float
) -> tuple[str | None, float, str | None, float]:
    on_lane = nusc_map.record_on_point(x, y, "lane")
    if on_lane:
        return on_lane, 0.0, None, 0.0

    lanes = nusc_map.get_records_in_radius(x, y, 5.0, ["lane"]).get("lane", [])
    if not lanes:
        return None, 0.0, None, 0.0

    point = Point(x, y)
    ranked: list[tuple[float, str]] = []
    for lane_token in lanes:
        lane = nusc_map.get("lane", lane_token)
        polygon_token = lane.get("polygon_token")
        if not polygon_token:
            continue

        polygon = nusc_map.extract_polygon(polygon_token)
        if polygon is None:
            continue

        d = float(point.distance(polygon))
        ranked.append((d, lane_token))

    if not ranked:
        return None, 0.0, None, 0.0

    ranked.sort(key=lambda t: t[0])
    lane_1_dist, lane_1 = ranked[0]
    if len(ranked) == 1:
        return lane_1, lane_1_dist, None, 0.0

    lane_2_dist, lane_2 = ranked[1]
    return lane_1, lane_1_dist, lane_2, lane_2_dist


def _compute_speed_accel(
    pts: list[tuple[float, float, float, int]],
) -> list[tuple[float | None, float | None]]:
    """Given [(x, y, z, timestamp_us), ...], return [(speed, accel), ...] per point."""
    result = []
    prev_speed = 0.0
    for i, (x, y, z, ts) in enumerate(pts):
        if i == 0:
            result.append((0.0, 0.0))
            continue
        px, py, _, pts_ts = pts[i - 1]
        dt = (ts - pts_ts) / 1_000_000
        if dt <= 0:
            result.append((prev_speed, 0.0))
            continue
        speed = math.hypot(x - px, y - py) / dt
        accel = (speed - prev_speed) / dt
        prev_speed = speed
        result.append((round(speed, 4), round(accel, 4)))
    return result


def _dubins_to_polyline(
    arc_segs: list, pts_per_5m: float = 1.5
) -> list[tuple[float, float]]:
    """Convert nuscenes arcline_path_3 Dubins segments to an (x,y) polyline."""
    pts: list[tuple[float, float]] = []
    x, y, h = arc_segs[0]["start_pose"]
    for seg in arc_segs:
        x, y, h = seg["start_pose"]
        for c, length in zip(seg["shape"], seg["segment_length"]):
            if length < 0.01:
                continue
            n = max(2, int(length * pts_per_5m / 5))
            if c == "S":
                for j in range(n + 1):
                    t = j / n * length
                    pts.append((x + math.cos(h) * t, y + math.sin(h) * t))
                x += math.cos(h) * length
                y += math.sin(h) * length
            elif c == "L":
                cx = x - seg["radius"] * math.sin(h)
                cy = y + seg["radius"] * math.cos(h)
                a0 = math.atan2(y - cy, x - cx)
                dth = length / seg["radius"]
                for j in range(n + 1):
                    a = a0 + j / n * dth
                    pts.append(
                        (
                            cx + seg["radius"] * math.cos(a),
                            cy + seg["radius"] * math.sin(a),
                        )
                    )
                a_end = a0 + dth
                x = cx + seg["radius"] * math.cos(a_end)
                y = cy + seg["radius"] * math.sin(a_end)
                h += dth
            elif c == "R":
                cx = x + seg["radius"] * math.sin(h)
                cy = y - seg["radius"] * math.cos(h)
                a0 = math.atan2(y - cy, x - cx)
                dth = length / seg["radius"]
                for j in range(n + 1):
                    a = a0 - j / n * dth
                    pts.append(
                        (
                            cx + seg["radius"] * math.cos(a),
                            cy + seg["radius"] * math.sin(a),
                        )
                    )
                a_end = a0 - dth
                x = cx + seg["radius"] * math.cos(a_end)
                y = cy + seg["radius"] * math.sin(a_end)
                h -= dth
    return pts


def _precompute_centerlines(
    nusc_map: NuScenesMap,
) -> list[tuple[str, list[tuple[float, float]], tuple[float, float, float, float]]]:
    rows = []
    for lane_token, arc_segs in nusc_map.arcline_path_3.items():
        try:
            pts = _dubins_to_polyline(arc_segs)
        except Exception:
            continue
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bounds = (min(xs), min(ys), max(xs), max(ys))
        rows.append((lane_token, pts, bounds))
    return rows


def _precompute_lane_connectivity(nusc_map: NuScenesMap) -> list[tuple[str, str]]:
    rows = []
    seen = set()
    lane_tokens = {l["token"] for l in nusc_map.lane}
    conn_tokens = {l["token"] for l in nusc_map.lane_connector}
    for connector_token, conn_info in nusc_map.connectivity.items():
        if connector_token not in conn_tokens:
            continue
        incoming = conn_info.get("incoming", [])
        outgoing = conn_info.get("outgoing", [])
        from_lanes = [t for t in incoming if t in lane_tokens]
        to_lanes = [t for t in outgoing if t in lane_tokens]
        for fl in from_lanes:
            for tl in to_lanes:
                key = (fl, tl)
                if key in seen:
                    continue
                seen.add(key)
                rows.append((fl, tl))
    return rows


def _open_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -100000")
    return conn


def _clear_scene_pose_rows(conn: sqlite3.Connection, scene_token: str):
    conn.execute("DELETE FROM object_poses WHERE scene_token = ?", (scene_token,))
    conn.execute(
        "DELETE FROM object_trajectories WHERE scene_token = ?", (scene_token,)
    )
    conn.execute("DELETE FROM centerlines WHERE scene_token = ?", (scene_token,))
    conn.execute("DELETE FROM ego_poses WHERE scene_token = ?", (scene_token,))


def _is_scene_already_exported(
    conn: sqlite3.Connection,
    scene_token: str,
    subset: str,
    data_version: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM scenes WHERE scene_token = ? AND subset = ? AND data_version = ? LIMIT 1",
        (scene_token, subset, data_version),
    ).fetchone()
    if row is None:
        return False
    frame_row = conn.execute(
        "SELECT 1 FROM ego_poses WHERE scene_token = ? LIMIT 1", (scene_token,)
    ).fetchone()
    return frame_row is not None


def _is_scene_centerlines_exported(conn: sqlite3.Connection, scene_token: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM centerlines WHERE scene_token = ? LIMIT 1", (scene_token,)
    ).fetchone()
    return row is not None


def _compute_scene_centerline_rows(
    scene: dict,
    nusc_map: NuScenesMap | None,
    map_centerlines,
    conn: sqlite3.Connection,
) -> list[tuple[str, str, int, float, float]] | None:
    scene_token = scene["token"]
    scene_name = scene["name"]
    if not nusc_map:
        print(f"  [skip] {scene_name} -- no map")
        return None

    frame_rows = conn.execute(
        "SELECT ego_x, ego_y FROM ego_poses WHERE scene_token = ? ORDER BY frame_idx",
        (scene_token,),
    ).fetchall()
    if not frame_rows:
        print(f"  [skip] {scene_name} -- no frames in DB")
        return None

    xs = [r[0] for r in frame_rows]
    ys = [r[1] for r in frame_rows]
    min_x, max_x = min(xs) - MAP_PADDING, max(xs) + MAP_PADDING
    min_y, max_y = min(ys) - MAP_PADDING, max(ys) + MAP_PADDING

    centerline_rows = []
    for lane_token, pts, bounds in map_centerlines:
        bx0, by0, bx1, by1 = bounds
        if bx1 < min_x or bx0 > max_x or by1 < min_y or by0 > max_y:
            continue
        clipped = [
            (px, py) for px, py in pts if min_x <= px <= max_x and min_y <= py <= max_y
        ]
        if len(clipped) < 2:
            continue
        for pi, (px, py) in enumerate(clipped):
            centerline_rows.append((scene_token, lane_token, pi, px, py))

    return centerline_rows


def _write_scene_centerlines(
    conn: sqlite3.Connection,
    scene_token: str,
    centerline_rows: list[tuple[str, str, int, float, float]],
):
    conn.execute("DELETE FROM centerlines WHERE scene_token = ?", (scene_token,))
    if centerline_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO centerlines (scene_token, lane_token, point_idx, x, y) VALUES (?, ?, ?, ?, ?)",
            centerline_rows,
        )


def _compute_scene_data(
    scene: dict,
    log: dict,
    tables: dict,
    nusc_map: NuScenesMap | None,
    map_centerlines,
    data_version: str,
    subset: str,
    with_lane_tokens: bool,
    with_centerlines: bool,
) -> dict | None:
    scene_token = scene["token"]
    scene_name = scene["name"]
    location = log["location"]

    sample_by_token = tables["sample_by_token"]
    sd_by_sample = tables["sd_by_sample"]
    ego_by_token = tables["ego_by_token"]
    ann_by_sample = tables["ann_by_sample"]
    cat_by_instance = tables["cat_by_instance"]

    ego_pts: list[tuple[float, float, float, int]] = []
    ego_rotations: list[tuple[float, float, float, float]] = []
    frame_timestamps: list[int] = []
    obj_frames: dict[str, list] = {}
    lane_token_cache: dict[
        tuple[float, float], tuple[str | None, float, str | None, float]
    ] = {}

    def lane_at(x: float, y: float) -> tuple[str | None, float, str | None, float]:
        if not with_lane_tokens or not nusc_map:
            return None, 0.0, None, 0.0
        key = (round(x, 1), round(y, 1))
        if key not in lane_token_cache:
            lane_token_cache[key] = _find_lane_at_point(nusc_map, x, y)
        return lane_token_cache[key]

    token = scene["first_sample_token"]
    frame_idx = 0
    while token:
        sample = sample_by_token[token]
        lidar_sd = next(
            (
                sd
                for sd in sd_by_sample.get(token, [])
                if "LIDAR_TOP" in sd["filename"] and sd["is_key_frame"]
            ),
            None,
        )
        if lidar_sd is None:
            token = sample["next"]
            continue

        ego = ego_by_token[lidar_sd["ego_pose_token"]]
        et = ego["translation"]
        er = ego["rotation"]
        ts = sample["timestamp"]

        ego_pts.append((et[0], et[1], et[2], ts))
        ego_rotations.append((er[0], er[1], er[2], er[3]))
        frame_timestamps.append(ts)

        for ann in ann_by_sample.get(token, []):
            cat_name = cat_by_instance.get(ann["instance_token"], "unknown")
            if not any(cat_name.startswith(p) for p in _TRACKED_PREFIXES):
                continue
            inst = ann["instance_token"]
            t = ann["translation"]
            s = ann["size"]
            r = ann["rotation"]
            obj_frames.setdefault(inst, []).append(
                (
                    frame_idx,
                    t[0], t[1], t[2],
                    r[0], r[1], r[2], r[3],
                    s[0], s[1], s[2],
                    cat_name,
                    ts,
                )
            )

        token = sample["next"]
        frame_idx += 1

    if not ego_pts:
        print(f"  [skip] {scene_name} -- no frames")
        return None

    num_frames = frame_idx
    ego_tel = _compute_speed_accel(ego_pts)
    ego_lane_tokens = [lane_at(ex, ey) for ex, ey, _, _ in ego_pts]

    scene_row = (scene_token, scene_name, location, num_frames, data_version, subset)

    ego_pose_rows = [
        (
            scene_token,
            i,
            frame_timestamps[i],
            ego_pts[i][0], ego_pts[i][1], ego_pts[i][2],
            ego_rotations[i][0], ego_rotations[i][1], ego_rotations[i][2], ego_rotations[i][3],
            ego_tel[i][0], ego_tel[i][1],
            ego_lane_tokens[i][0],
        )
        for i in range(num_frames)
    ]

    obj_rows = []
    traj_rows = []
    for inst, frames_data in obj_frames.items():
        obj_pts = [(f[1], f[2], f[3], f[12]) for f in frames_data]
        obj_tel = _compute_speed_accel(obj_pts)

        is_vehicle = frames_data[0][11].startswith("vehicle.")
        for i, (fi, x, y, z, qw, qx, qy, qz, w, l, h, cat, ts) in enumerate(frames_data):
            lane_1, lane_1_dist, lane_2, lane_2_dist = (
                lane_at(x, y) if (nusc_map and is_vehicle) else (None, 0.0, None, 0.0)
            )
            obj_rows.append(
                (
                    scene_token, fi, inst, cat,
                    x, y, z,
                    qw, qx, qy, qz,
                    w, l, h,
                    obj_tel[i][0], obj_tel[i][1],
                    lane_1, lane_1_dist, lane_2, lane_2_dist,
                )
            )

        points = [
            [fi, round(x, 3), round(y, 3), round(z, 3)]
            for fi, x, y, z, *_ in frames_data
        ]
        traj_rows.append(
            (
                scene_token,
                inst,
                frames_data[0][11],
                frames_data[0][0],
                frames_data[-1][0],
                json.dumps(points, separators=(",", ":")),
            )
        )

    ego_points = [
        [i, round(ego_pts[i][0], 3), round(ego_pts[i][1], 3), round(ego_pts[i][2], 3)]
        for i in range(num_frames)
    ]
    traj_rows.append(
        (
            scene_token,
            "ego",
            "ego",
            0,
            num_frames - 1,
            json.dumps(ego_points, separators=(",", ":")),
        )
    )

    centerline_rows: list | None = None
    if with_centerlines and nusc_map:
        xs = [p[0] for p in ego_pts]
        ys = [p[1] for p in ego_pts]
        min_x, max_x = min(xs) - MAP_PADDING, max(xs) + MAP_PADDING
        min_y, max_y = min(ys) - MAP_PADDING, max(ys) + MAP_PADDING

        centerline_rows = []
        for lane_token, pts, bounds in map_centerlines:
            bx0, by0, bx1, by1 = bounds
            if bx1 < min_x or bx0 > max_x or by1 < min_y or by0 > max_y:
                continue
            clipped = [
                (px, py)
                for px, py in pts
                if min_x <= px <= max_x and min_y <= py <= max_y
            ]
            if len(clipped) < 2:
                continue
            for pi, (px, py) in enumerate(clipped):
                centerline_rows.append((scene_token, lane_token, pi, px, py))

    return {
        "scene_token": scene_token,
        "scene_name": scene_name,
        "num_frames": num_frames,
        "n_objs": len(obj_frames),
        "scene_row": scene_row,
        "ego_pose_rows": ego_pose_rows,
        "obj_rows": obj_rows,
        "traj_rows": traj_rows,
        "centerline_rows": centerline_rows,
    }


def _write_scene_data(conn: sqlite3.Connection, data: dict):
    scene_token = data["scene_token"]
    _clear_scene_pose_rows(conn, scene_token)

    conn.execute(
        "INSERT INTO scenes (scene_token, scene_name, location, num_frames, data_version, subset) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(scene_token) DO UPDATE SET "
        "scene_name=excluded.scene_name, "
        "location=excluded.location, "
        "num_frames=excluded.num_frames, "
        "data_version=excluded.data_version, "
        "subset=excluded.subset",
        data["scene_row"],
    )

    conn.executemany(
        "INSERT OR REPLACE INTO ego_poses (scene_token, frame_idx, timestamp, ego_x, ego_y, ego_z, ego_qw, ego_qx, ego_qy, ego_qz, ego_speed, ego_accel, ego_lane_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        data["ego_pose_rows"],
    )

    conn.executemany(
        "INSERT OR REPLACE INTO object_poses (scene_token, frame_idx, instance_token, category, x, y, z, qw, qx, qy, qz, width, length, height, speed, accel, lane_token_1, lane_token_1_dist, lane_token_2, lane_token_2_dist) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        data["obj_rows"],
    )

    conn.executemany(
        "INSERT OR REPLACE INTO object_trajectories (scene_token, instance_token, category, start_frame, end_frame, points_json) VALUES (?, ?, ?, ?, ?, ?)",
        data["traj_rows"],
    )

    if data["centerline_rows"] is not None:
        conn.executemany(
            "INSERT OR REPLACE INTO centerlines (scene_token, lane_token, point_idx, x, y) VALUES (?, ?, ?, ?, ?)",
            data["centerline_rows"],
        )


def _export_one_scene(
    db_path: str,
    scene: dict,
    log: dict,
    tables: dict,
    nusc_map: NuScenesMap | None,
    map_centerlines,
    subset: str,
    data_version: str,
    skip_existing: bool,
    with_lane_tokens: bool,
    with_centerlines: bool,
    centerlines_only: bool,
):
    scene_token = scene["token"]
    scene_name = scene["name"]
    conn = _open_db_connection(db_path)
    try:
        if centerlines_only:
            if skip_existing and _is_scene_centerlines_exported(conn, scene_token):
                print(f"  [skip] {scene_name} -- centerlines already exported")
                return "skipped"
            centerline_rows = _compute_scene_centerline_rows(
                scene, nusc_map, map_centerlines, conn
            )
            if centerline_rows is None:
                return "skipped"
            for attempt in range(8):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    _write_scene_centerlines(conn, scene_token, centerline_rows)
                    conn.commit()
                    print(f"  {scene_name} | {len(centerline_rows)} centerline rows")
                    return "exported"
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    if "database is locked" not in str(exc).lower() or attempt == 7:
                        raise
                    time.sleep(0.25 * (attempt + 1))
        else:
            if skip_existing and _is_scene_already_exported(
                conn, scene_token, subset, data_version
            ):
                print(f"  [skip] {scene_name} -- already exported")
                return "skipped"

            data = _compute_scene_data(
                scene,
                log,
                tables,
                nusc_map,
                map_centerlines,
                data_version,
                subset,
                with_lane_tokens,
                with_centerlines,
            )
            if data is None:
                return "skipped"

            for attempt in range(8):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    _write_scene_data(conn, data)
                    conn.commit()
                    print(
                        f"  {data['scene_name']} | {data['num_frames']} frames | "
                        f"{data['n_objs']} objects | {len(data['obj_rows'])} pose rows"
                    )
                    return "exported"
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    if "database is locked" not in str(exc).lower() or attempt == 7:
                        raise
                    time.sleep(0.25 * (attempt + 1))
    finally:
        conn.close()


def load_all_tables(dataroot: str) -> tuple[list, dict, dict]:
    print("Loading tables...")
    scenes_all = load_table("scene", dataroot)
    samples_all = load_table("sample", dataroot)
    logs_all = load_table("log", dataroot)
    sd_all = load_table("sample_data", dataroot)
    ego_all = load_table("ego_pose", dataroot)
    ann_all = load_table("sample_annotation", dataroot)
    inst_all = load_table("instance", dataroot)
    cat_all = load_table("category", dataroot)
    print("  tables loaded")

    sample_by_token = {s["token"]: s for s in samples_all}
    sd_by_sample: dict = {}
    for sd in sd_all:
        sd_by_sample.setdefault(sd["sample_token"], []).append(sd)
    ego_by_token = {ep["token"]: ep for ep in ego_all}
    ann_by_sample: dict = {}
    for ann in ann_all:
        ann_by_sample.setdefault(ann["sample_token"], []).append(ann)
    cat_by_token = {c["token"]: c["name"] for c in cat_all}
    cat_by_instance = {i["token"]: cat_by_token[i["category_token"]] for i in inst_all}
    log_by_token = {l["token"]: l for l in logs_all}

    tables = {
        "sample_by_token": sample_by_token,
        "sd_by_sample": sd_by_sample,
        "ego_by_token": ego_by_token,
        "ann_by_sample": ann_by_sample,
        "cat_by_instance": cat_by_instance,
    }
    return scenes_all, log_by_token, tables


def load_scene_metadata(dataroot: str) -> tuple[list, dict]:
    scenes_all = load_table("scene", dataroot)
    logs_all = load_table("log", dataroot)
    log_by_token = {l["token"]: l for l in logs_all}
    return scenes_all, log_by_token


def main():
    parser = argparse.ArgumentParser(description="Export NuScenes poses to SQLite")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument(
        "--scene", default=None, help="Single scene name (e.g. scene-0001)"
    )
    parser.add_argument("--dataroot", default=os.environ.get("NUSCENES_ROOT"))
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel scene workers for poses export",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-export scene(s) even when pose rows already exist",
    )
    parser.add_argument(
        "--with-lane-tokens",
        action="store_true",
        help="Populate ego/object lane_token fields (disabled by default for speed)",
    )
    parser.add_argument(
        "--no-centerlines",
        action="store_true",
        help="Disable centerlines export (enabled by default)",
    )
    parser.add_argument(
        "--with-lane-connectivity",
        action="store_true",
        help="Populate lane_connectivity table (disabled by default for speed)",
    )
    parser.add_argument(
        "--centerlines-only",
        action="store_true",
        help="Backfill centerlines only using existing frames/object poses",
    )
    args = parser.parse_args()

    if not args.dataroot:
        print(
            "ERROR: --dataroot required (or set NUSCENES_ROOT in .env)", file=sys.stderr
        )
        sys.exit(1)

    if args.centerlines_only:
        scenes_all, log_by_token = load_scene_metadata(args.dataroot)
        tables = None
    else:
        scenes_all, log_by_token, tables = load_all_tables(args.dataroot)

    if args.scene:
        selected = [s for s in scenes_all if s["name"] == args.scene]
        assert selected, f"scene '{args.scene}' not found"
    else:
        selected = scenes_all

    maps_needed = set()
    for scene in selected:
        log = log_by_token[scene["log_token"]]
        maps_needed.add(log["location"])

    nusc_maps: dict[str, NuScenesMap] = {}
    map_centerline_cache: dict[str, list] = {}
    map_connectivity_rows: dict[str, list[tuple[str, str, str]]] = {}
    for map_name in maps_needed:
        print(f"  Loading map {map_name}...")
        nusc_map = NuScenesMap(dataroot=MAPS_ROOT, map_name=map_name)
        nusc_maps[map_name] = nusc_map
        map_centerline_cache[map_name] = _precompute_centerlines(nusc_map)
        map_connectivity_rows[map_name] = _precompute_lane_connectivity(nusc_map)

    conn = _open_db_connection(args.db)
    _ensure_scenes_columns(conn)
    _ensure_trajectory_table(conn)

    if args.with_lane_connectivity:
        for rows in map_connectivity_rows.values():
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO lane_connectivity (from_lane, to_lane) VALUES (?, ?)",
                    rows,
                )
        conn.commit()
    conn.close()

    subset = _get_subset(args.dataroot)
    skip_existing = not args.force
    workers = max(1, args.workers)
    with_centerlines = not args.no_centerlines

    print(f"\nExporting {len(selected)} scene(s) with workers={workers}")
    exported = 0
    skipped = 0

    if workers == 1 or len(selected) == 1:
        for scene in selected:
            log = log_by_token[scene["log_token"]]
            nusc_map = nusc_maps.get(log["location"])
            status = _export_one_scene(
                args.db,
                scene,
                log,
                tables,
                nusc_map,
                map_centerline_cache.get(log["location"], []),
                subset,
                DATA_VERSION,
                skip_existing,
                args.with_lane_tokens,
                with_centerlines,
                args.centerlines_only,
            )
            if status == "exported":
                exported += 1
            else:
                skipped += 1
    else:
        futures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for scene in selected:
                log = log_by_token[scene["log_token"]]
                nusc_map = nusc_maps.get(log["location"])
                futures.append(
                    pool.submit(
                        _export_one_scene,
                        args.db,
                        scene,
                        log,
                        tables,
                        nusc_map,
                        map_centerline_cache.get(log["location"], []),
                        subset,
                        DATA_VERSION,
                        skip_existing,
                        args.with_lane_tokens,
                        with_centerlines,
                        args.centerlines_only,
                    )
                )
            for fut in as_completed(futures):
                status = fut.result()
                if status == "exported":
                    exported += 1
                else:
                    skipped += 1

    print(f"Done. exported={exported}, skipped={skipped}")


if __name__ == "__main__":
    main()
