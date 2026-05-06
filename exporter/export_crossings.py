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
BBOX_PAD = 8.0  # m - axis-aligned bbox pre-filter padding
PROXIMITY_DIST = 15.0  # m - ped must be within this of a stopped vehicle
DISPLACEMENT_MIN = 5.0  # m - minimum start-to-end displacement to include an actor
CROSSING_TOLERANCE = 0.10  # m - near-hit buffer for moving-trajectory crossings


def _seg_intersect(p1x, p1y, p2x, p2y, p3x, p3y, p4x, p4y):
    """Intersection point of segments (p1,p2) and (p3,p4), or None."""
    d1x, d1y = p2x - p1x, p2y - p1y
    d2x, d2y = p4x - p3x, p4y - p3y
    cross = d1x * d2y - d1y * d2x
    if abs(cross) < 1e-12:
        return None
    t = ((p3x - p1x) * d2y - (p3y - p1y) * d2x) / cross
    u = ((p3x - p1x) * d1y - (p3y - p1y) * d1x) / cross
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (p1x + t * d1x, p1y + t * d1y)
    return None


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


def _min_dist_to_path(path, px, py):
    """Perpendicular distance from a point to the closest path segment."""
    best = float("inf")
    for i in range(len(path) - 1):
        ax, ay = path[i]
        bx, by = path[i + 1]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        if ll < 1e-12:
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        dsq = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2
        if dsq < best:
            best = dsq
    return math.sqrt(best)


def _vehicle_at_point(cx, cy, veh_traj):
    """Get frame idx and speed of the vehicle frame nearest to (cx, cy)."""
    best_d = float("inf")
    best_fi, best_spd = None, None
    for fi, vx, vy, _ts, vspd in veh_traj:
        d = (vx - cx) ** 2 + (vy - cy) ** 2
        if d < best_d:
            best_d, best_fi, best_spd = d, fi, vspd
    return best_fi, best_spd


def _detect_crossings(ped_traj, veh_traj):
    path = [(v[1], v[2]) for v in veh_traj]
    if len(path) < 2 or len(ped_traj) < 2:
        return []

    crossings: list[dict] = []
    for i in range(1, len(ped_traj)):
        px1, py1 = ped_traj[i - 1][1], ped_traj[i - 1][2]
        px2, py2 = ped_traj[i][1], ped_traj[i][2]
        if math.hypot(px2 - px1, py2 - py1) < 0.05:
            continue

        hit = None
        near = None
        for j in range(len(path) - 1):
            ax, ay = path[j][0], path[j][1]
            bx, by = path[j + 1][0], path[j + 1][1]
            hit = _seg_intersect(
                px1,
                py1,
                px2,
                py2,
                ax,
                ay,
                bx,
                by,
            )
            if hit is not None:
                break
            dist, nx, ny = _seg_closest_distance_midpoint(
                px1, py1, px2, py2, ax, ay, bx, by
            )
            if dist <= CROSSING_TOLERANCE and (near is None or dist < near[0]):
                near = (dist, nx, ny)

        if hit is None and near is None:
            continue

        ped_fi = ped_traj[i][0]
        if hit is not None:
            cx, cy = hit
            lat = min(
                _min_dist_to_path(path, px1, py1), _min_dist_to_path(path, px2, py2)
            )
        else:
            cx, cy = near[1], near[2]
            lat = near[0]
        veh_fi, _ = _vehicle_at_point(cx, cy, veh_traj)

        crossings.append(
            {
                "ped_frame": ped_fi,
                "veh_frame": veh_fi,
                "lateral_dist": lat,
            }
        )
    return crossings


def _is_stopped(trajectory) -> bool:
    if len(trajectory) < STOPPED_FRAMES:
        return False
    return all(
        t[3] is not None and t[3] < STOPPED_SPEED for t in trajectory[-STOPPED_FRAMES:]
    )


def _detect_proximity(traj, ped_traj, veh_traj):
    stopped_fis = {t[0] for t in traj if t[3] is not None and t[3] < STOPPED_SPEED}
    if not stopped_fis:
        return []

    veh_by_fi = {v[0]: v for v in veh_traj}

    best_fi, best_d = None, float("inf")
    for p in ped_traj:
        fi = p[0]
        if fi not in stopped_fis:
            continue
        v = veh_by_fi.get(fi)
        if v is None:
            continue
        d = math.hypot(p[1] - v[1], p[2] - v[2])
        if d < PROXIMITY_DIST and d < best_d:
            best_fi, best_d = fi, d

    if best_fi is None:
        return []

    return [{"ped_frame": best_fi, "veh_frame": best_fi, "lateral_dist": best_d}]


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

    seen: dict[tuple, tuple] = {}

    for veh_tok, (traj, veh_traj) in all_veh.items():
        actual_path = [(t[1], t[2]) for t in traj]
        stopped = _is_stopped(traj)
        speed_by_frame = {t[0]: t[3] for t in traj}
        xy_by_frame = {t[0]: (t[1], t[2]) for t in traj}

        if len(actual_path) >= 2:
            path_bb = _bbox(actual_path)
            for ped_tok, ped_traj in peds.items():
                if len(ped_traj) < 2:
                    continue
                ped_xy_by_frame = {p[0]: (p[1], p[2]) for p in ped_traj}
                ped_bb = _bbox([(p[1], p[2]) for p in ped_traj])
                if not _bboxes_overlap(path_bb, ped_bb, BBOX_PAD):
                    continue
                for c in _detect_crossings(ped_traj, veh_traj):
                    if veh_tok == "ego":
                        ped_speed = speed_by_frame.get(c["ped_frame"])
                        if ped_speed is not None and ped_speed < STOPPED_SPEED:
                            pxy = ped_xy_by_frame.get(c["ped_frame"])
                            vxy = xy_by_frame.get(c["ped_frame"])
                            if pxy is None or vxy is None:
                                continue
                            dist = math.hypot(pxy[0] - vxy[0], pxy[1] - vxy[1])
                            if dist > PROXIMITY_DIST:
                                continue
                            c["veh_frame"] = c["ped_frame"]
                    key = (ped_tok, veh_tok, c["ped_frame"])
                    lat = c["lateral_dist"]
                    if key not in seen or lat < seen[key][4]:
                        seen[key] = (
                            scene_token,
                            ped_tok,
                            veh_tok,
                            c["ped_frame"],
                            c["veh_frame"],
                            1 if stopped else 0,
                        )

        if stopped:
            for ped_tok, ped_traj in peds.items():
                for c in _detect_proximity(traj, ped_traj, veh_traj):
                    key = (ped_tok, veh_tok, c["ped_frame"])
                    lat = c["lateral_dist"]
                    if key not in seen or lat < seen[key][4]:
                        seen[key] = (
                            scene_token,
                            ped_tok,
                            veh_tok,
                            c["ped_frame"],
                            c["veh_frame"],
                            1,
                        )

    rows = list(seen.values())
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
