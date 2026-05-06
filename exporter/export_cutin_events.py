#!/usr/bin/env python3
"""Detect cut-in events from kinematic_features and write them to cutin_events.

A cut-in is a contiguous window where abs(l_rel_ego) decreases almost-monotonically
from >= START_ABS_L_MIN down to <= END_ABS_L_MAX, with the vehicle (not the ego)
doing the lateral motion.

Usage:
python exporter/export_cutin_events.py --db scene_data.db
python exporter/export_cutin_events.py --db scene_data.db --scene scene-0001
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass

START_ABS_L_MIN = 2.5
END_ABS_L_MAX = 1.2
S_MIN = 1.0
S_MAX = 30.0
MIN_WINDOW_FRAMES = 4
MAX_VIOLATION_RATIO = 0.20
PERP_DISP_RATIO = 1.15
SUSTAIN_FRAMES = 3  # trailing frames must all be within END_ABS_L_MAX
PER_FRAME_PERP_DOMINANCE_RATIO = 0.6  # fraction where |veh_perp| > |ego_perp|
SAME_DIRECTION_MAX_HEADING_DIFF_RAD = math.radians(30.0)  # checked at end-frame only
EGO_VS_VEHICLE_YAW_CHANGE_TOLERANCE_RAD = math.radians(3.0)  # absorbs yaw noise


@dataclass
class Frame:
    frame_idx: int
    l_rel_ego: float
    s_rel_ego: float
    vehicle_perp_disp: float
    ego_perp_disp: float
    vehicle_yaw: float
    ego_yaw: float


def _heading_diff(yaw_a: float, yaw_b: float) -> float:
    d = yaw_a - yaw_b
    while d > math.pi:
        d -= 2 * math.pi
    while d < -math.pi:
        d += 2 * math.pi
    return abs(d)


def _ensure_table(conn: sqlite3.Connection):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cutin_events)")]
    expected = ["id", "scene_token", "vehicle_id", "start_frame", "end_frame"]
    if cols and cols != expected:
        print("Dropping legacy cutin_events table (incompatible schema).")
        conn.execute("DROP TABLE cutin_events")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cutin_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_token TEXT NOT NULL,
            vehicle_id  TEXT NOT NULL,
            start_frame INTEGER NOT NULL,
            end_frame   INTEGER NOT NULL,
            FOREIGN KEY (scene_token) REFERENCES scenes(scene_token)
        );

        CREATE INDEX IF NOT EXISTS idx_cutin_events_scene
        ON cutin_events(scene_token);

        CREATE INDEX IF NOT EXISTS idx_cutin_events_vehicle
        ON cutin_events(scene_token, vehicle_id);
        """)


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


def _load_frames(conn: sqlite3.Connection, scene_token: str) -> dict[str, list[Frame]]:
    ego_perp: dict[int, float] = {}
    ego_yaw: dict[int, float] = {}
    for r in conn.execute(
        """SELECT frame_idx, perpendicular_displacement, yaw
           FROM kinematic_features
           WHERE scene_token = ? AND is_ego = 1""",
        (scene_token,),
    ):
        ego_perp[r["frame_idx"]] = r["perpendicular_displacement"]
        ego_yaw[r["frame_idx"]] = r["yaw"]

    by_actor: dict[str, list[Frame]] = {}
    rows = conn.execute(
        """SELECT actor_token, frame_idx, l_rel_ego, s_rel_ego,
                  perpendicular_displacement, yaw
           FROM kinematic_features
           WHERE scene_token = ?
             AND is_ego = 0
             AND l_rel_ego IS NOT NULL
             AND s_rel_ego IS NOT NULL
           ORDER BY actor_token, frame_idx""",
        (scene_token,),
    )
    for r in rows:
        by_actor.setdefault(r["actor_token"], []).append(
            Frame(
                frame_idx=r["frame_idx"],
                l_rel_ego=r["l_rel_ego"],
                s_rel_ego=r["s_rel_ego"],
                vehicle_perp_disp=r["perpendicular_displacement"],
                ego_perp_disp=ego_perp.get(r["frame_idx"], 0.0),
                vehicle_yaw=r["yaw"],
                ego_yaw=ego_yaw.get(r["frame_idx"], 0.0),
            )
        )
    return by_actor


def _is_almost_monotonic_decreasing(values: list[float]) -> bool:
    if len(values) < 2:
        return True
    violations = sum(1 for i in range(1, len(values)) if values[i] >= values[i - 1])
    return violations <= MAX_VIOLATION_RATIO * len(values)


def _is_valid_cutin_window(window: list[Frame]) -> bool:
    abs_l = [abs(f.l_rel_ego) for f in window]
    if abs_l[0] < START_ABS_L_MIN:
        return False
    if any(v > END_ABS_L_MAX for v in abs_l[-SUSTAIN_FRAMES:]):
        return False
    if any(not (S_MIN <= f.s_rel_ego <= S_MAX) for f in window):
        return False
    if not _is_almost_monotonic_decreasing(abs_l):
        return False
    end = window[-1]
    if (
        _heading_diff(end.vehicle_yaw, end.ego_yaw)
        > SAME_DIRECTION_MAX_HEADING_DIFF_RAD
    ):
        return False
    ego_yaw_change = _heading_diff(window[0].ego_yaw, end.ego_yaw)
    vehicle_yaw_change = _heading_diff(window[0].vehicle_yaw, end.vehicle_yaw)
    if ego_yaw_change > vehicle_yaw_change + EGO_VS_VEHICLE_YAW_CHANGE_TOLERANCE_RAD:
        return False
    perp_dominant = sum(
        1 for f in window if abs(f.vehicle_perp_disp) > abs(f.ego_perp_disp)
    )
    if perp_dominant < PER_FRAME_PERP_DOMINANCE_RATIO * len(window):
        return False
    sum_v = sum(f.vehicle_perp_disp for f in window)
    sum_e = sum(f.ego_perp_disp for f in window)
    return abs(sum_v) >= PERP_DISP_RATIO * abs(sum_e)


def _detect_cutins(frames: list[Frame]) -> list[tuple[int, int]]:
    # greedy: at each start, take the longest valid window, then advance past it
    events: list[tuple[int, int]] = []
    n = len(frames)
    i = 0
    while i < n:
        if abs(frames[i].l_rel_ego) < START_ABS_L_MIN:
            i += 1
            continue
        best_j = -1
        for j in range(i + MIN_WINDOW_FRAMES - 1, n):
            if _is_valid_cutin_window(frames[i : j + 1]):
                best_j = j
        if best_j >= 0:
            events.append((frames[i].frame_idx, frames[best_j].frame_idx))
            i = best_j + 1
        else:
            i += 1
    return events


def _process_scene(conn: sqlite3.Connection, scene_token: str, scene_name: str):
    conn.execute("DELETE FROM cutin_events WHERE scene_token = ?", (scene_token,))
    by_actor = _load_frames(conn, scene_token)
    rows: list[tuple] = []
    for vehicle_id, frames in by_actor.items():
        for start, end in _detect_cutins(frames):
            rows.append((scene_token, vehicle_id, start, end))
    conn.executemany(
        """INSERT INTO cutin_events
           (scene_token, vehicle_id, start_frame, end_frame)
           VALUES (?, ?, ?, ?)""",
        rows,
    )
    print(f"{scene_name}: cutin_events={len(rows)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--scene", default=None, help="Optional scene name filter")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_table(conn)
        for s in _scenes(conn, args.scene):
            _process_scene(conn, s["scene_token"], s["scene_name"])
            conn.commit()
        conn.execute("ANALYZE")
        print("Cut-in event export complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
