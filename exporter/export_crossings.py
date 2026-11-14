#!/usr/bin/env python3
"""Export pedestrian crossing events to SQLite."""

#  e.g. python export_crossings.py --db scene_data.db --scene scene-0061

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

STOPPED_SPEED = 0.5  # m/s - below this the vehicle counts as stopped
STOPPED_FRAMES = 3  # consecutive frames below threshold to declare stopped
DISPLACEMENT_MIN = 5.0  # m - minimum start-to-end displacement to include an actor
CROSSING_DIST = 15.0  # m - ped and vehicle paths must pass within this to cross
PERP_TOL_DEG = 30.0  # deg - how far from a right angle still counts as crossing
HEADING_WINDOW = 3  # frames each side used to estimate local heading
BBOX_PAD = CROSSING_DIST  # bbox pre-filter must not discard any within-tolerance pair


def _seg_closest_distance_midpoint(p1x, p1y, p2x, p2y, p3x, p3y, p4x, p4y):
    """Min distance between two segments and midpoint of closest points."""
    ux, uy = p2x - p1x, p2y - p1y
    vx, vy = p4x - p3x, p4y - p3y
    wx, wy = p1x - p3x, p1y - p3y

    a = ux * ux + uy * uy
    b = ux * vx + uy * vy
    c = vx * vx + vy * vy
    d = ux * wx + uy * wy
    e = vx * wx + vy * wy

    den = a * c - b * b
    s_num, s_den = den, den
    t_num, t_den = den, den
    eps = 1e-12

    if den < eps:
        s_num, s_den = 0.0, 1.0
        t_num, t_den = e, c
    else:
        s_num = b * e - c * d
        t_num = a * e - b * d
        if s_num < 0.0:
            s_num = 0.0
            t_num, t_den = e, c
        elif s_num > s_den:
            s_num = s_den
            t_num, t_den = e + b, c

    if t_num < 0.0:
        t_num = 0.0
        if -d < 0.0:
            s_num = 0.0
        elif -d > a:
            s_num = s_den
        else:
            s_num, s_den = -d, a
    elif t_num > t_den:
        t_num = t_den
        if -d + b < 0.0:
            s_num = 0.0
        elif -d + b > a:
            s_num = s_den
        else:
            s_num, s_den = -d + b, a

    s = 0.0 if abs(s_num) < eps else s_num / s_den
    t = 0.0 if abs(t_num) < eps else t_num / t_den

    c1x, c1y = p1x + s * ux, p1y + s * uy
    c2x, c2y = p3x + t * vx, p3y + t * vy
    dist = math.hypot(c1x - c2x, c1y - c2y)
    return dist, 0.5 * (c1x + c2x), 0.5 * (c1y + c2y)


def _nearest_index(path, px, py):
    """Index of the path vertex closest to (px, py)."""
    return min(
        range(len(path)),
        key=lambda i: (path[i][0] - px) ** 2 + (path[i][1] - py) ** 2,
    )


def _local_heading(path, idx):
    """Unit direction of travel near vertex `idx`, using a local window so a
    turn elsewhere in the trajectory doesn't skew the estimate. None if the
    actor is roughly stationary over the window."""
    lo = max(0, idx - HEADING_WINDOW)
    hi = min(len(path) - 1, idx + HEADING_WINDOW)
    dx, dy = path[hi][0] - path[lo][0], path[hi][1] - path[lo][1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return None
    return (dx / n, dy / n)


def _is_perpendicular(h1, h2):
    """True if two headings are within PERP_TOL_DEG of a right angle."""
    if h1 is None or h2 is None:
        return False
    cos = abs(h1[0] * h2[0] + h1[1] * h2[1])
    return cos <= math.sin(math.radians(PERP_TOL_DEG))


def _detect_crossings(ped_traj, veh_traj):
    """A crossing is the closest approach between the ped and vehicle paths,
    kept only if they pass within CROSSING_DIST and their local headings there
    are roughly perpendicular. At most one crossing per (ped, vehicle) pair."""
    ped_path = [(p[1], p[2]) for p in ped_traj]
    veh_path = [(v[1], v[2]) for v in veh_traj]
    if len(ped_path) < 2 or len(veh_path) < 2:
        return []

    best_d, best_pt = float("inf"), None
    for i in range(len(ped_path) - 1):
        for j in range(len(veh_path) - 1):
            d, mx, my = _seg_closest_distance_midpoint(
                ped_path[i][0], ped_path[i][1], ped_path[i + 1][0], ped_path[i + 1][1],
                veh_path[j][0], veh_path[j][1], veh_path[j + 1][0], veh_path[j + 1][1],
            )
            if d < best_d:
                best_d, best_pt = d, (mx, my)

    if best_pt is None or best_d > CROSSING_DIST:
        return []

    cx, cy = best_pt
    pi = _nearest_index(ped_path, cx, cy)
    vj = _nearest_index(veh_path, cx, cy)
    if not _is_perpendicular(
        _local_heading(ped_path, pi), _local_heading(veh_path, vj)
    ):
        return []

    return [
        {
            "ped_frame": ped_traj[pi][0],
            "veh_frame": veh_traj[vj][0],
            "lateral_dist": best_d,
        }
    ]


def _is_stopped(trajectory) -> bool:
    if len(trajectory) < STOPPED_FRAMES:
        return False
    return all(
        t[3] is not None and t[3] < STOPPED_SPEED for t in trajectory[-STOPPED_FRAMES:]
    )


def _load_scene(conn: sqlite3.Connection, scene_token: str):
    vehicles: dict[str, list] = defaultdict(list)
    for r in conn.execute(
        "SELECT op.instance_token, op.frame_idx, op.x, op.y, op.speed, "
        "       op.lane_token_1 AS lane_token, f.timestamp "
        "FROM object_poses op "
        "JOIN ego_poses f ON f.scene_token = op.scene_token "
        "                AND f.frame_idx = op.frame_idx "
        "WHERE op.scene_token = ? AND op.category LIKE 'vehicle.%' "
        "ORDER BY op.instance_token, op.frame_idx",
        (scene_token,),
    ):
        vehicles[r["instance_token"]].append(
            (
                r["frame_idx"],
                r["x"],
                r["y"],
                r["speed"],
                r["lane_token"],
                r["timestamp"],
            )
        )

    ego = [
        (
            r["frame_idx"],
            r["ego_x"],
            r["ego_y"],
            r["ego_speed"],
            r["ego_lane_token"],
            r["timestamp"],
        )
        for r in conn.execute(
            "SELECT frame_idx, ego_x, ego_y, ego_speed, ego_lane_token, "
            "       timestamp "
            "FROM ego_poses WHERE scene_token = ? ORDER BY frame_idx",
            (scene_token,),
        )
    ]

    peds: dict[str, list] = defaultdict(list)
    for r in conn.execute(
        "SELECT op.instance_token, op.frame_idx, op.x, op.y, f.timestamp "
        "FROM object_poses op "
        "JOIN ego_poses f ON f.scene_token = op.scene_token "
        "                AND f.frame_idx = op.frame_idx "
        "WHERE op.scene_token = ? AND op.category LIKE 'human.pedestrian%' "
        "ORDER BY op.instance_token, op.frame_idx",
        (scene_token,),
    ):
        peds[r["instance_token"]].append(
            (
                r["frame_idx"],
                r["x"],
                r["y"],
                r["timestamp"],
            )
        )

    return dict(vehicles), ego, dict(peds)


def _displacement(pts_xy):
    if len(pts_xy) < 2:
        return 0.0
    return math.hypot(pts_xy[-1][0] - pts_xy[0][0], pts_xy[-1][1] - pts_xy[0][1])


def _bbox(pts_xy):
    xs = [p[0] for p in pts_xy]
    ys = [p[1] for p in pts_xy]
    return min(xs), min(ys), max(xs), max(ys)


def _bboxes_overlap(a, b, pad):
    return not (
        a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1]
    )


def process_scene(conn, scene_token, scene_name):
    vehicles, ego, peds = _load_scene(conn, scene_token)

    peds = {
        tok: traj
        for tok, traj in peds.items()
        if _displacement([(p[1], p[2]) for p in traj]) >= DISPLACEMENT_MIN
    }
    if not peds:
        print(f"  {scene_name} | 0 crossings (no moving peds)")
        return []

    vehicles = {
        tok: traj
        for tok, traj in vehicles.items()
        if _displacement([(t[1], t[2]) for t in traj]) >= DISPLACEMENT_MIN
    }

    all_veh: dict[str, tuple] = {}
    for tok, traj in vehicles.items():
        veh_traj = [(t[0], t[1], t[2], t[5], t[3]) for t in traj]
        all_veh[tok] = (traj, veh_traj)
    if ego:
        ego_traj = [(t[0], t[1], t[2], t[5], t[3]) for t in ego]
        all_veh["ego"] = (ego, ego_traj)

    rows = []

    for veh_tok, (traj, veh_traj) in all_veh.items():
        actual_path = [(t[1], t[2]) for t in traj]
        if len(actual_path) < 2:
            continue
        stopped = _is_stopped(traj)
        path_bb = _bbox(actual_path)
        for ped_tok, ped_traj in peds.items():
            if len(ped_traj) < 2:
                continue
            ped_bb = _bbox([(p[1], p[2]) for p in ped_traj])
            if not _bboxes_overlap(path_bb, ped_bb, BBOX_PAD):
                continue
            for c in _detect_crossings(ped_traj, veh_traj):
                rows.append(
                    (
                        scene_token,
                        ped_tok,
                        veh_tok,
                        c["ped_frame"],
                        c["veh_frame"],
                        1 if stopped else 0,
                    )
                )

    print(f"  {scene_name} | {len(rows)} crossings")
    return rows


def _process_scene_task(db_path: str, scene_token: str, scene_name: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = process_scene(conn, scene_token, scene_name)
        return rows
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Export path-crossing events to SQLite"
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--scene", default=None, help="Single scene name")
    parser.add_argument(
        "--subset",
        default=None,
        help="Subset name in scenes.subset (e.g. v1.0-trainval02)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Parallel worker processes for per-scene crossing detection",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.scene:
        row = conn.execute(
            "SELECT scene_token, scene_name FROM scenes WHERE scene_name = ?",
            (args.scene,),
        ).fetchone()
        if not row:
            print(f"Scene '{args.scene}' not found", file=sys.stderr)
            sys.exit(1)
        conn.execute(
            "DELETE FROM ped_vehicle_crossings WHERE scene_token = ?",
            (row["scene_token"],),
        )
        scenes = [dict(row)]
    else:
        if args.subset:
            scenes = [
                dict(r)
                for r in conn.execute(
                    "SELECT scene_token, scene_name FROM scenes WHERE subset = ? ORDER BY scene_name",
                    (args.subset,),
                )
            ]
            conn.executemany(
                "DELETE FROM ped_vehicle_crossings WHERE scene_token = ?",
                [(s["scene_token"],) for s in scenes],
            )
        else:
            conn.execute("DELETE FROM ped_vehicle_crossings")
            scenes = [
                dict(r)
                for r in conn.execute(
                    "SELECT scene_token, scene_name FROM scenes ORDER BY scene_name"
                )
            ]

    if not scenes:
        scope = f"subset '{args.subset}'" if args.subset else "all scenes"
        print(f"No scenes found for {scope}.")
        conn.close()
        return

    total = 0
    workers = max(1, args.workers)

    if workers == 1 or len(scenes) == 1:
        for s in scenes:
            rows = process_scene(conn, s["scene_token"], s["scene_name"])
            if rows:
                conn.executemany(
                    "INSERT INTO ped_vehicle_crossings "
                    "(scene_token, ped_token, vehicle_token, ped_frame, veh_frame, "
                    " vehicle_stopped) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
            total += len(rows)
    else:
        tasks = [(args.db, s["scene_token"], s["scene_name"]) for s in scenes]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            dbs = [t[0] for t in tasks]
            tokens = [t[1] for t in tasks]
            names = [t[2] for t in tasks]
            for rows in pool.map(_process_scene_task, dbs, tokens, names):
                if rows:
                    conn.executemany(
                        "INSERT INTO ped_vehicle_crossings "
                        "(scene_token, ped_token, vehicle_token, ped_frame, veh_frame, "
                        " vehicle_stopped) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                total += len(rows)

    conn.commit()

    print(f"Done. {total} total crossings across {len(scenes)} scenes.")
    conn.close()


if __name__ == "__main__":
    main()
