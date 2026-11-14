#!/usr/bin/env python3
"""Export intersection conflict pairs.

A conflict pair is two vehicles traversing the same intersection in
(near-)overlapping frame windows whose matched connector centerlines cross.
The crossing point is the conflict point; each vehicle's (interpolated) frame
of arrival there gives the post-encroachment time (PET) — the standard
surrogate measure of how closely the two paths interacted in time.

Scenario presets (CCFtap, CCCscp) are pure predicates over these rows joined
with intersection_traversals: roles come from maneuver labels and arm ids, the
crossing itself is established here. No approach-angle heuristics anywhere.

Runs off the DB + map expansion only. Must run after export_intersection_traversals.

Usage:
    python export_intersection_conflicts.py --db scene_data.db
    python export_intersection_conflicts.py --db scene_data.db --scene scene-0001
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from itertools import combinations

import numpy as np
from dotenv import load_dotenv
from shapely.geometry import LineString
from nuscenes.map_expansion.map_api import NuScenesMap

from maneuver_utils.helpers import connector_centerline, project_onto_polyline
from schema import INDEXES, SCHEMA

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
MAPS_ROOT = os.environ["MAPS_ROOT"]

WINDOW_PAD_FRAMES = 4  # near-overlap tolerance between the two traversal windows
FRAME_SECONDS = 0.5  # nuScenes keyframes are 2 Hz


def _resolve_maps_dataroot(raw: str) -> str:
    root = os.path.abspath(raw)
    if os.path.isdir(os.path.join(root, "maps", "expansion")):
        return root
    if os.path.isdir(os.path.join(root, "expansion")):
        return os.path.dirname(root)
    return root


def _conflict_point(cl_a: np.ndarray, cl_b: np.ndarray) -> tuple[float, float] | None:
    """First crossing of the two centerlines along A's direction of travel."""
    inter = LineString(cl_a).intersection(LineString(cl_b))
    if inter.is_empty:
        return None
    pts = []
    for geom in getattr(inter, "geoms", [inter]):
        pts.extend(geom.coords)
    best = min(pts, key=lambda p: project_onto_polyline(cl_a, np.asarray(p[:2])))
    return best[0], best[1]


def _arrival_frame(
    frames: list[tuple[int, float, float]], centerline: np.ndarray, s_target: float
) -> float | None:
    """Interpolated frame index at which the vehicle's arc-length position on
    its connector first passes s_target; None if that moment was not observed
    (the vehicle stopped short, or entered already past the point)."""
    s = [project_onto_polyline(centerline, np.array([x, y])) for _, x, y in frames]
    for i in range(1, len(s)):
        if s[i - 1] < s_target <= s[i]:
            f0, f1 = frames[i - 1][0], frames[i][0]
            u = (s_target - s[i - 1]) / (s[i] - s[i - 1])
            return f0 + u * (f1 - f0)
    return None


def _vehicle_frames(
    conn: sqlite3.Connection, scene_token: str, vehicle: str, lo: int, hi: int
) -> list[tuple[int, float, float]]:
    if vehicle == "ego":
        rows = conn.execute(
            """SELECT frame_idx, ego_x AS x, ego_y AS y FROM ego_poses
               WHERE scene_token = ? AND frame_idx BETWEEN ? AND ? ORDER BY frame_idx""",
            (scene_token, lo, hi),
        )
    else:
        rows = conn.execute(
            """SELECT frame_idx, x, y FROM object_poses
               WHERE scene_token = ? AND instance_token = ? AND frame_idx BETWEEN ? AND ?
               ORDER BY frame_idx""",
            (scene_token, vehicle, lo, hi),
        )
    return [(r["frame_idx"], r["x"], r["y"]) for r in rows]


def _process_scene(
    conn: sqlite3.Connection, maps: dict[str, NuScenesMap], location: str,
    scene_token: str, scene_name: str,
) -> None:
    conn.execute(
        "DELETE FROM intersection_conflicts WHERE scene_token = ?", (scene_token,)
    )
    traversals = conn.execute(
        """SELECT vehicle_token, intersection_token, connector_token, start_frame, end_frame
           FROM intersection_traversals
           WHERE scene_token = ? AND maneuver != 'unknown'""",
        (scene_token,),
    ).fetchall()

    by_intersection: dict[str, list] = {}
    for t in traversals:
        by_intersection.setdefault(t["intersection_token"], []).append(t)

    nusc_map = maps[location]
    rows: list[tuple] = []
    for inter_token, group in by_intersection.items():
        for a, b in combinations(group, 2):
            if a["connector_token"] == b["connector_token"]:
                continue  # same path: following, not crossing
            if (
                a["start_frame"] > b["end_frame"] + WINDOW_PAD_FRAMES
                or b["start_frame"] > a["end_frame"] + WINDOW_PAD_FRAMES
            ):
                continue
            cl_a = connector_centerline(nusc_map, a["connector_token"])
            cl_b = connector_centerline(nusc_map, b["connector_token"])
            point = _conflict_point(cl_a, cl_b)
            if point is None:
                continue

            arrivals = []
            for t, cl in ((a, cl_a), (b, cl_b)):
                frames = _vehicle_frames(
                    conn, scene_token, t["vehicle_token"],
                    t["start_frame"] - WINDOW_PAD_FRAMES,
                    t["end_frame"] + WINDOW_PAD_FRAMES,
                )
                s_target = project_onto_polyline(cl, np.asarray(point))
                arrivals.append(_arrival_frame(frames, cl, s_target))

            pet = (
                abs(arrivals[0] - arrivals[1]) * FRAME_SECONDS
                if arrivals[0] is not None and arrivals[1] is not None
                else None
            )
            rows.append(
                (
                    scene_token, inter_token,
                    a["vehicle_token"], b["vehicle_token"],
                    point[0], point[1],
                    arrivals[0], arrivals[1], pet,
                )
            )

    conn.executemany(
        """INSERT INTO intersection_conflicts
           (scene_token, intersection_token, vehicle_a, vehicle_b,
            conflict_x, conflict_y, a_arrival_frame, b_arrival_frame, pet_seconds)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    print(f"  {scene_name}: {len(rows)} conflict pairs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export intersection conflict pairs")
    parser.add_argument("--db", required=True)
    parser.add_argument("--scene", default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)

    where, params = ("WHERE scene_name = ?", [args.scene]) if args.scene else ("", [])
    scenes = conn.execute(
        f"SELECT scene_token, scene_name, location FROM scenes {where} ORDER BY scene_name",
        params,
    ).fetchall()
    assert scenes, "no scenes found for the given selection"

    maps: dict[str, NuScenesMap] = {}
    dataroot = _resolve_maps_dataroot(MAPS_ROOT)
    try:
        for s in scenes:
            if s["location"] not in maps:
                maps[s["location"]] = NuScenesMap(dataroot=dataroot, map_name=s["location"])
            _process_scene(conn, maps, s["location"], s["scene_token"], s["scene_name"])

        total, with_pet = conn.execute(
            "SELECT COUNT(*), COUNT(pet_seconds) FROM intersection_conflicts"
        ).fetchone()
        print(f"Totals: {total} conflict pairs, {with_pet} with PET")
        conn.execute("ANALYZE intersection_conflicts")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
