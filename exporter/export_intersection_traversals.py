#!/usr/bin/env python3
"""Export labelled intersection traversals (left/right/straight/u_turn/unknown).

The maneuver is a property of the map, not the vehicle: every lane connector
carries an arm-based label (maneuver_utils.intersections), and a traversal
inherits the label of the connector its trajectory matches. Two phases:

1. Per map: cluster each intersection's connector endpoints into road arms and
   label every connector. Fewer than 3 arms = not a real intersection, so all
   of its connectors are straight (no branching, no turn).
2. Per scene (DB poses only, no devkit load): find each vehicle's in-polygon
   traversal runs, score the intersection's connectors against the observed
   (x, y, yaw) trajectory, apply a lane-graph prior from the validated
   pre-entry/post-exit lane snaps, and label from the best connector. When
   near-tied connectors disagree on the label, or the observed heading change
   contradicts it, the traversal is 'unknown'.

Partial traversals need no special handling: a trajectory fragment still
matches a connector, and the full connector supplies the label;
observed_fraction records how much of it was actually seen.

Usage:
    python export_intersection_traversals.py --db scene_data.db
    python export_intersection_traversals.py --db scene_data.db --scene scene-0001
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass

from dotenv import load_dotenv
from nuscenes.map_expansion.arcline_path_utils import principal_value
from nuscenes.map_expansion.map_api import NuScenesMap

from maneuver_utils.helpers import (
    build_intersection_index,
    lane_direction_at,
    point_to_intersection,
    score_connectors,
)
from maneuver_utils.intersections import ClassifiedIntersection, classify_intersection
from schema import INDEXES, SCHEMA

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
MAPS_ROOT = os.environ["MAPS_ROOT"]

ALL_MAPS = {
    "singapore-hollandvillage",
    "singapore-onenorth",
    "singapore-queenstown",
    "boston-seaport",
}

MIN_PATH_LENGTH = 4.0  # metres; skip vehicles barely moving through the polygon
PRIOR_WINDOW_FRAMES = 5  # pre-entry / post-exit frames used for the lane prior
SNAP_MAX_HEADING = math.pi / 4  # reject lane snaps that disagree with vehicle yaw
LANE_PRIOR_BONUS = 2.0  # score bonus for lane-graph-consistent connectors
TIE_MARGIN = 1.5  # connectors within this of the best are considered ties
MAX_MATCH_SCORE = 8.0  # above this the trajectory follows no connector
VETO_OPPOSITE = math.radians(30)  # observed heading change contradicting the label


@dataclass
class Frame:
    x: float
    y: float
    yaw: float
    lane: str | None


@dataclass
class MapContext:
    nusc_map: NuScenesMap
    index: list[tuple[str, object]]  # (intersection_token, prepared polygon)
    classified: dict[str, ClassifiedIntersection]
    incoming: dict[str, set[str]]  # connector -> lanes feeding it
    outgoing: dict[str, set[str]]


def _resolve_maps_dataroot(raw: str) -> str:
    root = os.path.abspath(raw)
    if os.path.isdir(os.path.join(root, "maps", "expansion")):
        return root
    if os.path.isdir(os.path.join(root, "expansion")):
        return os.path.dirname(root)
    return root


def _load_map(map_name: str) -> MapContext:
    nusc_map = NuScenesMap(
        dataroot=_resolve_maps_dataroot(MAPS_ROOT), map_name=map_name
    )
    tokens = [
        rs["token"] for rs in nusc_map.road_segment if rs.get("is_intersection", False)
    ]
    classified = {t: classify_intersection(nusc_map, t) for t in tokens}
    incoming, outgoing = {}, {}
    for info in classified.values():
        for ct in info.connectors:
            conn_rec = nusc_map.connectivity.get(ct, {})
            incoming[ct] = set(conn_rec.get("incoming", []))
            outgoing[ct] = set(conn_rec.get("outgoing", []))
    return MapContext(
        nusc_map=nusc_map,
        index=build_intersection_index(nusc_map, tokens),
        classified=classified,
        incoming=incoming,
        outgoing=outgoing,
    )


def _yaw_from_quat(qw: float, qx: float, qy: float, qz: float) -> float:
    return math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))


def _load_vehicles(
    conn: sqlite3.Connection, scene_token: str
) -> dict[str, dict[int, Frame]]:
    vehicles: dict[str, dict[int, Frame]] = {}
    for r in conn.execute(
        """SELECT frame_idx, ego_x AS x, ego_y AS y, ego_qw AS qw, ego_qx AS qx,
                  ego_qy AS qy, ego_qz AS qz, ego_lane_token AS lane
           FROM ego_poses WHERE scene_token = ?""",
        (scene_token,),
    ):
        vehicles.setdefault("ego", {})[r["frame_idx"]] = Frame(
            r["x"], r["y"], _yaw_from_quat(r["qw"], r["qx"], r["qy"], r["qz"]), r["lane"]
        )
    for r in conn.execute(
        """SELECT frame_idx, instance_token, x, y, qw, qx, qy, qz, lane_token_1 AS lane
           FROM object_poses WHERE scene_token = ? AND category LIKE 'vehicle.%'""",
        (scene_token,),
    ):
        vehicles.setdefault(r["instance_token"], {})[r["frame_idx"]] = Frame(
            r["x"], r["y"], _yaw_from_quat(r["qw"], r["qx"], r["qy"], r["qz"]), r["lane"]
        )
    return vehicles


def _validated_lane(
    frames: dict[int, Frame], frame_range: range, nusc_map: NuScenesMap
) -> str | None:
    """Majority-vote lane over the window, accepted only if the vehicle's yaw
    agrees with the lane's direction at the closest of those frames."""
    window = [frames[f] for f in frame_range if f in frames]
    votes = [w.lane for w in window if w.lane is not None]
    if not votes:
        return None
    lane = Counter(votes).most_common(1)[0][0]
    ref = next(w for w in reversed(window) if w.lane == lane)
    direction = lane_direction_at(nusc_map, lane, ref.x, ref.y)
    if direction is None or abs(principal_value(ref.yaw - direction)) > SNAP_MAX_HEADING:
        return None
    return lane


def _traversal_runs(
    frames: dict[int, Frame], index: list[tuple[str, object]]
) -> list[tuple[str, list[int]]]:
    # A run spans the vehicle's full visit even if it briefly exits the polygon.
    by_inter: dict[str, list[int]] = {}
    for f in sorted(frames):
        token = point_to_intersection(frames[f].x, frames[f].y, index)
        if token is not None:
            by_inter.setdefault(token, []).append(f)
    return list(by_inter.items())


def _label_traversal(
    ctx: MapContext, frames: dict[int, Frame], inter_token: str, run: list[int]
) -> tuple | None:
    """Returns (maneuver, connector_token, connector_start_yaw, match_score,
    class_margin, observed_fraction) or None when the run is not a usable
    traversal (too short, or the intersection has no connectors)."""
    info = ctx.classified[inter_token]
    traj = [(frames[f].x, frames[f].y, frames[f].yaw) for f in run]
    if len(traj) < 2 or not info.connectors:
        return None
    path_len = sum(
        math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(traj, traj[1:])
    )
    if path_len < MIN_PATH_LENGTH:
        return None

    scored = score_connectors(ctx.nusc_map, traj, list(info.connectors))
    if not scored:
        return None

    start, end = run[0], run[-1]
    entry_lane = _validated_lane(
        frames, range(start - PRIOR_WINDOW_FRAMES, start), ctx.nusc_map
    )
    exit_lane = _validated_lane(
        frames, range(end + 1, end + 1 + PRIOR_WINDOW_FRAMES), ctx.nusc_map
    )

    def effective(s) -> float:
        bonus = 0.0
        if entry_lane is not None and entry_lane in ctx.incoming[s.token]:
            bonus += LANE_PRIOR_BONUS
        if exit_lane is not None and exit_lane in ctx.outgoing[s.token]:
            bonus += LANE_PRIOR_BONUS
        return s.score - bonus

    scored.sort(key=effective)
    best = scored[0]
    label = info.connectors[best.token].maneuver
    start_yaw = info.connectors[best.token].start_yaw

    if best.score > MAX_MATCH_SCORE:
        return ("unknown", best.token, start_yaw, best.score, None, best.observed_fraction)

    differing = [
        effective(s) - effective(best)
        for s in scored
        if info.connectors[s.token].maneuver != label
    ]
    class_margin = min(differing) if differing else None

    maneuver = label if class_margin is None or class_margin > TIE_MARGIN else "unknown"

    # Veto: an observed heading change strongly opposing the label means the
    # match is wrong, not the vehicle.
    observed = sum(
        principal_value(b[2] - a[2]) for a, b in zip(traj, traj[1:])
    )
    if (maneuver == "left" and observed < -VETO_OPPOSITE) or (
        maneuver == "right" and observed > VETO_OPPOSITE
    ):
        maneuver = "unknown"

    return (maneuver, best.token, start_yaw, best.score, class_margin, best.observed_fraction)


def _process_scene(
    conn: sqlite3.Connection, ctx: MapContext, scene_token: str, scene_name: str
) -> None:
    conn.execute(
        "DELETE FROM intersection_traversals WHERE scene_token = ?", (scene_token,)
    )

    rows: list[tuple] = []
    for vehicle_token, frames in _load_vehicles(conn, scene_token).items():
        for inter_token, run in _traversal_runs(frames, ctx.index):
            labelled = _label_traversal(ctx, frames, inter_token, run)
            if labelled is None:
                continue
            maneuver, connector, start_yaw, score, margin, observed_frac = labelled
            info = ctx.classified[inter_token]
            rows.append(
                (
                    scene_token,
                    inter_token,
                    vehicle_token,
                    run[0],
                    run[-1],
                    maneuver,
                    connector,
                    start_yaw,
                    score,
                    margin,
                    observed_frac,
                    info.n_arms,
                    info.connectors[connector].entry_arm,
                    info.connectors[connector].exit_arm,
                )
            )

    conn.executemany(
        """INSERT INTO intersection_traversals
           (scene_token, intersection_token, vehicle_token, start_frame, end_frame,
            maneuver, connector_token, connector_start_yaw, match_score,
            class_margin, observed_fraction, n_arms, entry_arm, exit_arm)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()

    counts = Counter(r[5] for r in rows)
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    print(f"  {scene_name}: {', '.join(parts) or 'no traversals'}")


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(intersection_traversals)")}
    if cols and "entry_arm" not in cols:
        conn.execute("DROP TABLE intersection_traversals")
    conn.execute("DROP TABLE IF EXISTS intersection_traversals_geometric_data")
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export intersection traversals")
    parser.add_argument("--db", required=True)
    parser.add_argument("--scene", default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    _migrate(conn)

    where, params = ("WHERE scene_name = ?", [args.scene]) if args.scene else ("", [])
    scenes = conn.execute(
        f"SELECT scene_token, scene_name, location FROM scenes {where} ORDER BY scene_name",
        params,
    ).fetchall()
    assert scenes, "no scenes found for the given selection"

    maps: dict[str, MapContext] = {}
    try:
        for s in scenes:
            if s["location"] not in ALL_MAPS:
                continue
            if s["location"] not in maps:
                print(f"Classifying intersections: {s['location']}")
                maps[s["location"]] = _load_map(s["location"])
            _process_scene(conn, maps[s["location"]], s["scene_token"], s["scene_name"])

        totals = conn.execute(
            "SELECT maneuver, COUNT(*) FROM intersection_traversals GROUP BY maneuver"
        ).fetchall()
        print("Totals: " + ", ".join(f"{m}={n}" for m, n in totals))
        conn.execute("ANALYZE intersection_traversals")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
