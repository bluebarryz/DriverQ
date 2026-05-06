#!/usr/bin/env python3
"""Export labelled intersection traversals (left/right/curve/straight).

Pipeline (one row per geometric traversal):
1. Pull the per-vehicle in-intersection window from
    `intersection_traversals_geometric_data`.
2. Extend that window by `WINDOW_BUFFER_FRAMES` on each side so the
    kinematic event captures pre-entry approach and post-exit run-out.
3. Run the Ayres et al (2004) heading-rate
    rule on the kinematic frames inside the buffered window.
4. If a kinematic event fires, use its maneuver (left/right/curve). For
    `curve` events, defer to the connector classification when it is
    `left` or `right`. Otherwise fall back to the connector classification.

Usage:
python export_intersection_traversals.py --db scene_data.db
python export_intersection_traversals.py --db scene_data.db --scene scene-0001
"""

from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass

# Ayres thresholds, adapted for 2 Hz nuScenes keyframes.
YAW_RATE_THRESH_DEG = 1.0
SMOOTH_WINDOW = 3
CURVE_MIN_HEADING_DEG = 5.0
TURN_MIN_HEADING_DEG = 30.0
TURN_PEAK_YAW_LOW_SPEED = 11.5
TURN_LOW_SPEED_THRESH = 8.0
TURN_R_THRESH = 50.0
TURN_MIN_SPEED = 2.0
MIN_TRACK_FRAMES = 4
WINDOW_BUFFER_FRAMES = 3
RUNNER_UP_SCORE_RATIO = 0.8


@dataclass
class FrameKinematics:
    frame_idx: int
    timestamp: int
    yaw: float
    speed: float


@dataclass
class TraversalEvent:
    start_frame: int
    end_frame: int
    maneuver: str  # 'left', 'right', 'curve'


def _principal_value(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def _moving_average(values: list[float], window: int) -> list[float]:
    n, half = len(values), window // 2
    return [
        sum(values[max(0, i - half) : min(n, i + half + 1)])
        / (min(n, i + half + 1) - max(0, i - half))
        for i in range(n)
    ]


def _yaw_rates(frames: list[FrameKinematics]) -> list[float]:
    rates: list[float] = []
    for i in range(1, len(frames)):
        dt = (frames[i].timestamp - frames[i - 1].timestamp) / 1_000_000.0
        if dt <= 0:
            rates.append(0.0)
            continue
        rates.append(
            math.degrees(_principal_value(frames[i].yaw - frames[i - 1].yaw)) / dt
        )
    return rates


def _zero_crossing_before(values: list[float], idx: int) -> int:
    i = idx
    while i > 0 and values[i] * values[i - 1] > 0:
        i -= 1
    return i


def _zero_crossing_after(values: list[float], idx: int) -> int:
    i = idx
    while i < len(values) - 1 and values[i] * values[i + 1] > 0:
        i += 1
    return i


def _detect_events(frames: list[FrameKinematics]) -> list[TraversalEvent]:
    if len(frames) < MIN_TRACK_FRAMES:
        return []
    raw = _yaw_rates(frames)
    if not raw:
        return []
    smoothed = _moving_average(raw, SMOOTH_WINDOW)

    visited = [False] * len(smoothed)
    segments: list[tuple[int, int]] = []
    for i, val in enumerate(smoothed):
        if visited[i] or abs(val) < YAW_RATE_THRESH_DEG:
            continue
        s = _zero_crossing_before(smoothed, i)
        e = _zero_crossing_after(smoothed, i)
        for j in range(s, e + 1):
            visited[j] = True
        segments.append((s, e))

    events: list[TraversalEvent] = []
    for rate_start, rate_end in segments:
        f_start = rate_start
        f_end = min(rate_end + 1, len(frames) - 1)
        if f_end <= f_start:
            continue
        window = frames[f_start : f_end + 1]
        if len(window) < 2:
            continue

        heading_change = sum(
            math.degrees(_principal_value(window[k].yaw - window[k - 1].yaw))
            for k in range(1, len(window))
        )
        if abs(heading_change) < CURVE_MIN_HEADING_DEG:
            continue

        rates = raw[rate_start : rate_end + 1]
        peak_i = max(range(len(rates)), key=lambda j: abs(rates[j]))
        peak_rate = abs(rates[peak_i])
        speed_at_peak = window[min(peak_i + 1, len(window) - 1)].speed

        is_turn = False
        if speed_at_peak >= TURN_MIN_SPEED:
            if (
                speed_at_peak < TURN_LOW_SPEED_THRESH
                and peak_rate > TURN_PEAK_YAW_LOW_SPEED
            ):
                is_turn = True
            if peak_rate > 0.01:
                r = abs(speed_at_peak / math.radians(peak_rate))
                if r < TURN_R_THRESH:
                    is_turn = True

        if abs(heading_change) >= TURN_MIN_HEADING_DEG and is_turn:
            maneuver = "left" if heading_change > 0 else "right"
        else:
            maneuver = "curve"

        events.append(
            TraversalEvent(
                start_frame=window[0].frame_idx,
                end_frame=window[-1].frame_idx,
                maneuver=maneuver,
            )
        )
    return events


def _process_scene(conn: sqlite3.Connection, scene_token: str, scene_name: str) -> None:
    conn.execute(
        "DELETE FROM intersection_traversals WHERE scene_token = ?", (scene_token,)
    )

    rows = conn.execute(
        """SELECT kf.actor_token, kf.frame_idx, f.timestamp, kf.yaw, kf.speed
           FROM kinematic_features kf
           JOIN ego_poses f ON f.scene_token = kf.scene_token AND f.frame_idx = kf.frame_idx
           WHERE kf.scene_token = ?""",
        (scene_token,),
    ).fetchall()

    by_actor: dict[str, dict[int, FrameKinematics]] = {}
    for r in rows:
        by_actor.setdefault(r["actor_token"], {})[r["frame_idx"]] = FrameKinematics(
            frame_idx=r["frame_idx"],
            timestamp=r["timestamp"],
            yaw=r["yaw"],
            speed=r["speed"] or 0.0,
        )

    geom_rows = conn.execute(
        """SELECT intersection_token, vehicle_token, start_frame, end_frame,
                  connector_1_classification, connector_1_match_score,
                  connector_2_classification, connector_2_match_score
           FROM intersection_traversals_geometric_data WHERE scene_token = ?""",
        (scene_token,),
    ).fetchall()

    insert_rows: list[tuple] = []
    for g in geom_rows:
        actor = "__ego__" if g["vehicle_token"] == "ego" else g["vehicle_token"]
        frame_map = by_actor.get(actor, {})
        lo = g["start_frame"] - WINDOW_BUFFER_FRAMES
        hi = g["end_frame"] + WINDOW_BUFFER_FRAMES
        window = [frame_map[f] for f in range(lo, hi + 1) if f in frame_map]

        c1_class = g["connector_1_classification"]
        c1_score = g["connector_1_match_score"]
        c2_class = g["connector_2_classification"]
        c2_score = g["connector_2_match_score"]
        events = _detect_events(window)
        if events:
            maneuver = events[0].maneuver
            if maneuver == "curve":
                if c1_class in ("left", "right"):
                    maneuver = c1_class
                elif (
                    c2_class in ("left", "right")
                    and c1_score is not None
                    and c2_score is not None
                    and c1_score > 0
                    and c1_score >= RUNNER_UP_SCORE_RATIO * c2_score
                ):
                    maneuver = c2_class
        else:
            maneuver = c1_class or "straight"

        approach_heading = window[0].yaw if window else None
        insert_rows.append(
            (
                scene_token,
                g["intersection_token"],
                g["vehicle_token"],
                g["start_frame"],
                g["end_frame"],
                maneuver,
                approach_heading,
            )
        )

    conn.executemany(
        """INSERT INTO intersection_traversals
           (scene_token, intersection_token, vehicle_token, start_frame, end_frame,
            maneuver, intersection_approach_heading)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        insert_rows,
    )
    conn.commit()

    counts: dict[str, int] = {}
    for r in conn.execute(
        """SELECT maneuver, COUNT(*) AS n FROM intersection_traversals
           WHERE scene_token = ? GROUP BY maneuver""",
        (scene_token,),
    ).fetchall():
        counts[r["maneuver"]] = r["n"]
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    print(f"  {scene_name}: {', '.join(parts) or 'no traversals'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export intersection traversals")
    parser.add_argument("--db", required=True)
    parser.add_argument("--scene", default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.scene:
            scenes = conn.execute(
                "SELECT scene_token, scene_name FROM scenes WHERE scene_name = ?",
                (args.scene,),
            ).fetchall()
        else:
            scenes = conn.execute(
                "SELECT scene_token, scene_name FROM scenes ORDER BY scene_name"
            ).fetchall()
        assert scenes, "no scenes found for the given selection"

        for s in scenes:
            _process_scene(conn, s["scene_token"], s["scene_name"])

        conn.execute("ANALYZE intersection_traversals")
        print("Intersection traversals export complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
