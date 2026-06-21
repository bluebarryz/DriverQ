#!/usr/bin/env python3
"""Detect lane change events and write them to lane_change_events.

Per actor: build per-frame lane assignments -> filter geometrically implausible
ones -> find stable blocks (dominant lane, >= MIN_BLOCK_FRAMES) -> emit a lane
change between consecutive blocks A->B iff the lanes are lateral neighbors (not
topologically connected, 1.2-7.5m apart, parallel, overlapping) and the vehicle
has actually departed lane A.

Usage:
python exporter/export_lane_change_events.py --db scene_data.db
python exporter/export_lane_change_events.py --db scene_data.db --scene scene-0001
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass

LANE_AMBIGUITY_GAP_M = 0.25
# polygon-based assignments at forks/intersections frequently bleed into adjacent lanes
MAX_LATERAL_OFFSET_M = 2.0
# rejects relabelings (no real crossing) and round-trip excursions
MIN_LATERAL_DEPARTURE_M = 0.7

BLOCK_DOMINANCE = 0.75
MIN_BLOCK_FRAMES = 3
MAX_GAP_FRAMES = 5

LATERAL_MAX_HEADING_DIFF_RAD = math.radians(15.0)
LATERAL_MIN_DIST_M = 1.2
LATERAL_MAX_DIST_M = 7.5
LATERAL_MIN_PARALLEL_OVERLAP_M = 10.0


@dataclass
class FrameLane:
    frame_idx: int
    lane: str | None
    x: float
    y: float
    yaw: float


@dataclass
class Block:
    lane: str
    start_idx: int  # index into the actor's frame list (inclusive)
    end_idx: int  # index into the actor's frame list (inclusive)


def _scenes(conn: sqlite3.Connection, scene_name: str | None) -> list[sqlite3.Row]:
    if scene_name:
        rows = conn.execute(
            "SELECT scene_token, scene_name FROM scenes WHERE scene_name = ?",
            (scene_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT scene_token, scene_name FROM scenes ORDER BY scene_name"
        ).fetchall()
    assert rows, f"No scenes found for selection={scene_name!r}"
    return rows


def _definitive_lane(
    token1: str | None,
    dist1: float | None,
    token2: str | None,
    dist2: float | None,
) -> str | None:
    if token1 is None:
        return None
    if (
        token2 is not None
        and dist1 is not None
        and dist2 is not None
        and dist2 - dist1 < LANE_AMBIGUITY_GAP_M
    ):
        return None
    return token1


def _yaw_from_quaternion_wxyz(qw: float, qx: float, qy: float, qz: float) -> float:
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def _load_actor_frames(
    conn: sqlite3.Connection, scene_token: str
) -> dict[str, list[FrameLane]]:
    by_actor: dict[str, list[FrameLane]] = {}

    by_actor["__ego__"] = [
        FrameLane(
            frame_idx=r["frame_idx"],
            lane=r["ego_lane_token"],
            x=r["ego_x"],
            y=r["ego_y"],
            yaw=_yaw_from_quaternion_wxyz(
                r["ego_qw"], r["ego_qx"], r["ego_qy"], r["ego_qz"]
            ),
        )
        for r in conn.execute(
            """SELECT frame_idx, ego_x, ego_y, ego_qw, ego_qx, ego_qy, ego_qz,
                      ego_lane_token
               FROM ego_poses
               WHERE scene_token = ?
               ORDER BY frame_idx""",
            (scene_token,),
        )
    ]

    rows = conn.execute(
        """SELECT instance_token, frame_idx, x, y, qw, qx, qy, qz,
                  lane_token_1, lane_token_1_dist,
                  lane_token_2, lane_token_2_dist
           FROM object_poses
           WHERE scene_token = ? AND category LIKE 'vehicle.%'
           ORDER BY instance_token, frame_idx""",
        (scene_token,),
    )
    for r in rows:
        lane = _definitive_lane(
            r["lane_token_1"],
            r["lane_token_1_dist"],
            r["lane_token_2"],
            r["lane_token_2_dist"],
        )
        by_actor.setdefault(r["instance_token"], []).append(
            FrameLane(
                frame_idx=r["frame_idx"],
                lane=lane,
                x=r["x"],
                y=r["y"],
                yaw=_yaw_from_quaternion_wxyz(r["qw"], r["qx"], r["qy"], r["qz"]),
            )
        )
    return by_actor


def _load_centerlines(
    conn: sqlite3.Connection, scene_token: str, lane_tokens: set[str]
) -> dict[str, list[tuple[float, float]]]:
    if not lane_tokens:
        return {}
    placeholders = ",".join(["?"] * len(lane_tokens))
    rows = conn.execute(
        f"""SELECT lane_token, x, y
            FROM centerlines
            WHERE scene_token = ? AND lane_token IN ({placeholders})
            ORDER BY lane_token, point_idx""",
        [scene_token, *sorted(lane_tokens)],
    )
    by_lane: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        by_lane.setdefault(r["lane_token"], []).append((r["x"], r["y"]))
    return by_lane


def _load_lane_connectivity(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (r["from_lane"], r["to_lane"])
        for r in conn.execute("SELECT from_lane, to_lane FROM lane_connectivity")
    }


def _lateral_offset_to_centerline(
    px: float, py: float, yaw: float, poly: list[tuple[float, float]]
) -> float:
    if len(poly) < 2:
        return float("inf")
    cos_y = math.cos(yaw)
    sin_y = math.sin(yaw)
    pts = [
        (
            (x - px) * cos_y + (y - py) * sin_y,
            -(x - px) * sin_y + (y - py) * cos_y,
        )
        for x, y in poly
    ]
    best = min(abs(lat) for _, lat in pts)
    for (f0, l0), (f1, l1) in zip(pts, pts[1:]):
        if (f0 <= 0 <= f1) or (f1 <= 0 <= f0):
            if f0 == f1:
                lat = 0.5 * (l0 + l1)
            else:
                t = -f0 / (f1 - f0)
                lat = l0 + t * (l1 - l0)
            best = min(best, abs(lat))
    return best


def _polyline_heading(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    x0, y0 = points[0]
    x1, y1 = points[-1]
    if x0 == x1 and y0 == y1:
        return None
    return math.atan2(y1 - y0, x1 - x0)


def _principal_value(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def _polyline_min_dist(
    a: list[tuple[float, float]], b: list[tuple[float, float]]
) -> float:
    best = float("inf")
    for x1, y1 in a:
        for x2, y2 in b:
            d = math.hypot(x1 - x2, y1 - y2)
            if d < best:
                best = d
    return best


def _is_lateral_neighbor(
    lane_a: str,
    lane_b: str,
    connectivity: set[tuple[str, str]],
    centerlines: dict[str, list[tuple[float, float]]],
) -> bool:
    if (lane_a, lane_b) in connectivity or (lane_b, lane_a) in connectivity:
        return False

    cl_a = centerlines.get(lane_a)
    cl_b = centerlines.get(lane_b)
    if not cl_a or not cl_b:
        return False

    h_a = _polyline_heading(cl_a)
    h_b = _polyline_heading(cl_b)
    if h_a is None or h_b is None:
        return False

    heading_diff = abs(_principal_value(h_a - h_b))
    heading_diff = min(heading_diff, math.pi - heading_diff)
    if heading_diff > LATERAL_MAX_HEADING_DIFF_RAD:
        return False

    min_dist = _polyline_min_dist(cl_a, cl_b)
    if not (LATERAL_MIN_DIST_M <= min_dist <= LATERAL_MAX_DIST_M):
        return False

    # Project both centerlines onto lane A's tangent and require longitudinal
    # overlap - two parallel curbs that don't overlap aren't really neighbors.
    ax0, ay0 = cl_a[0]
    ax1, ay1 = cl_a[-1]
    tx, ty = ax1 - ax0, ay1 - ay0
    tnorm = math.hypot(tx, ty)
    if tnorm < 1e-3:
        return False
    tx /= tnorm
    ty /= tnorm
    a_proj = [(x - ax0) * tx + (y - ay0) * ty for x, y in cl_a]
    b_proj = [(x - ax0) * tx + (y - ay0) * ty for x, y in cl_b]
    overlap = min(max(a_proj), max(b_proj)) - max(min(a_proj), min(b_proj))
    return overlap >= LATERAL_MIN_PARALLEL_OVERLAP_M


def _find_stable_blocks(frames: list[FrameLane]) -> list[Block]:
    # greedy: at each unvisited frame, take the longest dominant block
    blocks: list[Block] = []
    n = len(frames)
    i = 0
    while i < n:
        if frames[i].lane is None:
            i += 1
            continue
        lane = frames[i].lane
        count = 0
        best_j = -1
        for j in range(i, n):
            if frames[j].lane == lane:
                count += 1
            length = j - i + 1
            if (
                length >= MIN_BLOCK_FRAMES
                and count / length >= BLOCK_DOMINANCE
                and frames[j].lane == lane
            ):
                best_j = j
        if best_j >= 0:
            blocks.append(Block(lane=lane, start_idx=i, end_idx=best_j))
            i = best_j + 1
        else:
            i += 1
    return blocks


def _filter_by_lateral_offset(
    frames: list[FrameLane],
    centerlines: dict[str, list[tuple[float, float]]],
) -> list[FrameLane]:
    out: list[FrameLane] = []
    for f in frames:
        if f.lane is None:
            out.append(f)
            continue
        cl = centerlines.get(f.lane)
        if (
            cl is None
            or _lateral_offset_to_centerline(f.x, f.y, f.yaw, cl) > MAX_LATERAL_OFFSET_M
        ):
            out.append(
                FrameLane(frame_idx=f.frame_idx, lane=None, x=f.x, y=f.y, yaw=f.yaw)
            )
        else:
            out.append(f)
    return out


def _has_left_lane_a(
    frames: list[FrameLane],
    a_start_idx: int,
    b_end_idx: int,
    cl_a: list[tuple[float, float]],
) -> bool:
    lats = [
        _lateral_offset_to_centerline(f.x, f.y, f.yaw, cl_a)
        for f in frames[a_start_idx : b_end_idx + 1]
    ]
    return (lats[-1] - min(lats)) >= MIN_LATERAL_DEPARTURE_M


def _detect_lane_changes(
    frames: list[FrameLane],
    connectivity: set[tuple[str, str]],
    centerlines: dict[str, list[tuple[float, float]]],
) -> list[tuple[str, str, int, int]]:
    frames = _filter_by_lateral_offset(frames, centerlines)
    blocks = _find_stable_blocks(frames)
    events: list[tuple[str, str, int, int]] = []
    for k in range(len(blocks) - 1):
        a, b = blocks[k], blocks[k + 1]
        if a.lane == b.lane:
            continue
        gap = frames[b.start_idx].frame_idx - frames[a.end_idx].frame_idx - 1
        if gap < 0 or gap > MAX_GAP_FRAMES:
            continue
        if not _is_lateral_neighbor(a.lane, b.lane, connectivity, centerlines):
            continue
        cl_a = centerlines.get(a.lane)
        if cl_a is None or not _has_left_lane_a(frames, a.start_idx, b.end_idx, cl_a):
            continue
        events.append(
            (
                a.lane,
                b.lane,
                frames[a.start_idx].frame_idx,
                frames[b.end_idx].frame_idx,
            )
        )
    return events


def _process_scene(
    conn: sqlite3.Connection,
    scene_token: str,
    scene_name: str,
    connectivity: set[tuple[str, str]],
):
    conn.execute("DELETE FROM lane_change_events WHERE scene_token = ?", (scene_token,))
    by_actor = _load_actor_frames(conn, scene_token)
    lane_tokens = {f.lane for frames in by_actor.values() for f in frames if f.lane}
    centerlines = _load_centerlines(conn, scene_token, lane_tokens)

    rows: list[tuple] = []
    for vehicle_id, frames in by_actor.items():
        for lane_a, lane_b, start, end in _detect_lane_changes(
            frames, connectivity, centerlines
        ):
            rows.append((scene_token, vehicle_id, lane_a, lane_b, start, end))
    conn.executemany(
        """INSERT INTO lane_change_events
           (scene_token, vehicle_id, lane_a, lane_b, start_frame, end_frame)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    print(f"{scene_name}: lane_change_events={len(rows)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--scene", default=None, help="Optional scene name filter")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        connectivity = _load_lane_connectivity(conn)
        for s in _scenes(conn, args.scene):
            _process_scene(conn, s["scene_token"], s["scene_name"], connectivity)
            conn.commit()
        conn.execute("ANALYZE")
        print("Lane-change event export complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
