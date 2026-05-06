#!/usr/bin/env python3
"""Export intersection traversals with connector geometry to SQLite.

One row per (vehicle, intersection traversal):
    intersection_token, vehicle_token, connector_token, start_frame, end_frame,
    connector_1_start_yaw, connector_1_classification, connector_1_match_score,
    connector_2_classification, connector_2_match_score.
connector_token is the best match, connector_2_* is for the runner-up.

The traversing connector is the lane-connector inside the intersection that
best matches the vehicle's trajectory while it was inside the intersection
polygon

Usage:
python export_intersection_traversals_geometric_data.py --db scene_data_old.db
python export_intersection_traversals_geometric_data.py --db scene_data_old.db --subsets /path/to/v1.0-trainval01
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict

from dotenv import load_dotenv
from nuscenes.map_expansion.map_api import NuScenesMap
from nuscenes.nuscenes import NuScenes

from maneuver_utils.helpers import (
    VEHICLE_PREFIXES,
    _build_intersection_index,
    _connector_delta_yaw,
    _connectors_in_intersection,
    _point_to_intersection,
    get_connector_start_yaw,
    match_trajectory_to_connector,
)

TURN_YAW_THRESHOLD = math.pi / 6  # if below this, classify as straight

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_env_path)

MAPS_ROOT = os.environ["MAPS_ROOT"]

SINGAPORE_MAPS = {
    "singapore-hollandvillage",
    "singapore-onenorth",
    "singapore-queenstown",
}
BOSTON_MAPS = {"boston-seaport"}
ALL_MAPS = SINGAPORE_MAPS | BOSTON_MAPS

MIN_PATH_LENGTH = 4.0  # filter out vehicles barely moving through the polygon

SAME_ARM_HEADING_THRESHOLD = math.pi / 4  # if above this, treat as different arm
SAME_ARM_DISTANCE_THRESHOLD = 15.0  # metres between lane endpoints


def _is_lane(nusc_map: NuScenesMap, tok: str) -> bool:
    return tok in nusc_map._token2ind["lane"]


def _lane_end_pose(
    nusc_map: NuScenesMap, lane_token: str
) -> tuple[float, float, float]:
    arcline = nusc_map.arcline_path_3.get(lane_token, [])
    assert arcline, f"no arcline data for lane {lane_token}"
    end_pose = arcline[-1]["end_pose"]
    return end_pose[0], end_pose[1], end_pose[2]


def _same_arm(nusc_map: NuScenesMap, lane_a: str, lane_b: str) -> bool:
    ax, ay, ayaw = _lane_end_pose(nusc_map, lane_a)
    bx, by, byaw = _lane_end_pose(nusc_map, lane_b)
    if math.hypot(ax - bx, ay - by) > SAME_ARM_DISTANCE_THRESHOLD:
        return False
    yaw_diff = abs((ayaw - byaw + math.pi) % (2 * math.pi) - math.pi)
    return yaw_diff < SAME_ARM_HEADING_THRESHOLD


def _classify_connector(
    nusc_map: NuScenesMap,
    connector_token: str,
    intersection_connectors: list[str],
    cache: dict[str, str],
) -> str:
    if connector_token in cache:
        return cache[connector_token]

    delta_yaw = _connector_delta_yaw(nusc_map, connector_token)
    if abs(delta_yaw) < TURN_YAW_THRESHOLD:
        result = "straight"
    elif delta_yaw > 0:
        result = "left"
    else:
        result = "right"
    cache[connector_token] = result
    return result


def _resolve_maps_dataroot(raw_maps_root: str) -> str:
    root = os.path.abspath(raw_maps_root)
    if os.path.isdir(os.path.join(root, "maps", "expansion")):
        return root
    if os.path.isdir(os.path.join(root, "expansion")):
        return os.path.dirname(root)
    return root


def _collect_trajectories(
    nusc: NuScenes, scene_token: str
) -> dict[str, list[tuple[int, float, float]]]:
    out: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    scene = nusc.get("scene", scene_token)
    sample_token = scene["first_sample_token"]
    frame_idx = 0
    while sample_token:
        sample = nusc.get("sample", sample_token)
        sd = nusc.get("sample_data", sample["data"]["LIDAR_TOP"])
        ex, ey = nusc.get("ego_pose", sd["ego_pose_token"])["translation"][:2]
        out["ego"].append((frame_idx, ex, ey))

        for ann_token in sample["anns"]:
            ann = nusc.get("sample_annotation", ann_token)
            if not ann["category_name"].startswith(VEHICLE_PREFIXES):
                continue
            ax, ay = ann["translation"][:2]
            out[ann["instance_token"]].append((frame_idx, ax, ay))

        sample_token = sample["next"]
        frame_idx += 1
    return out


def _traversal_runs(
    points: list[tuple[int, float, float]],
    inter_index: list[tuple[str, object]],
) -> list[tuple[str, int, int, list[tuple[float, float]]]]:
    # start/end frames span the full visit even if the vehicle briefly exits
    by_inter: dict[str, list[tuple[int, float, float]]] = {}
    for frame_idx, x, y in points:
        token = _point_to_intersection(x, y, inter_index)
        if token is None:
            continue
        by_inter.setdefault(token, []).append((frame_idx, x, y))

    runs: list[tuple[str, int, int, list[tuple[float, float]]]] = []
    for token, pts in by_inter.items():
        pts.sort()
        runs.append((token, pts[0][0], pts[-1][0], [(x, y) for _, x, y in pts]))
    return runs


def export_scene(
    nusc: NuScenes,
    nusc_map: NuScenesMap,
    scene: dict,
    intersection_tokens: list[str],
    connectors_by_intersection: dict[str, list[str]],
    classification_cache: dict[str, str],
    conn: sqlite3.Connection,
) -> None:
    scene_token = scene["token"]
    conn.execute(
        "DELETE FROM intersection_traversals_geometric_data WHERE scene_token = ?",
        (scene_token,),
    )

    inter_index = _build_intersection_index(nusc_map, set(intersection_tokens))
    trajectories = _collect_trajectories(nusc, scene_token)

    rows: list[tuple] = []
    for vehicle_token, points in trajectories.items():
        for inter_token, start_frame, end_frame, traj in _traversal_runs(
            points, inter_index
        ):
            if len(traj) < 2:
                continue
            path_len = sum(
                math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(traj, traj[1:])
            )
            if path_len < MIN_PATH_LENGTH:
                continue
            candidates = connectors_by_intersection.get(inter_token, [])
            if not candidates:
                continue
            best_token, best_score, second_token, second_score = (
                match_trajectory_to_connector(nusc_map, traj, candidates)
            )
            if not best_token:
                continue
            if second_token:
                c2_class = _classify_connector(
                    nusc_map, second_token, candidates, classification_cache
                )
                c2_score = second_score
            else:
                c2_class = c2_score = None
            rows.append(
                (
                    scene_token,
                    inter_token,
                    vehicle_token,
                    best_token,
                    start_frame,
                    end_frame,
                    get_connector_start_yaw(nusc_map, best_token),
                    _classify_connector(
                        nusc_map, best_token, candidates, classification_cache
                    ),
                    best_score,
                    c2_class,
                    c2_score,
                )
            )

    conn.executemany(
        """INSERT INTO intersection_traversals_geometric_data
           (scene_token, intersection_token, vehicle_token, connector_token,
            start_frame, end_frame,
            connector_1_start_yaw, connector_1_classification, connector_1_match_score,
            connector_2_classification, connector_2_match_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    print(f"  {scene['name']} | {len(rows)} traversals")


def main():
    parser = argparse.ArgumentParser(
        description="Export intersection traversals with connector geometry"
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--subsets", nargs="+", default=None)
    parser.add_argument("--scene", default=None)
    args = parser.parse_args()

    if not args.subsets:
        nuscenes_root = os.environ.get("NUSCENES_ROOT")
        if not nuscenes_root:
            print(
                "ERROR: --subsets required (or set NUSCENES_ROOT in .env)",
                file=sys.stderr,
            )
            sys.exit(1)
        args.subsets = [nuscenes_root]

    maps_dataroot = _resolve_maps_dataroot(MAPS_ROOT)
    conn = sqlite3.connect(args.db)
    map_cache: dict[str, tuple[NuScenesMap, list[str], dict[str, list[str]]]] = {}
    classification_cache: dict[str, str] = {}

    for subset_path in args.subsets:
        nusc = NuScenes(version="v1.0-trainval", dataroot=subset_path, verbose=False)
        scene_locations = {
            s["token"]: nusc.get("log", s["log_token"])["location"] for s in nusc.scene
        }

        for scene in nusc.scene:
            map_name = scene_locations[scene["token"]]
            if map_name not in ALL_MAPS:
                continue
            if args.scene and scene["name"] != args.scene:
                continue

            if map_name not in map_cache:
                nusc_map = NuScenesMap(dataroot=maps_dataroot, map_name=map_name)
                intersection_tokens = [
                    rs["token"]
                    for rs in nusc_map.road_segment
                    if rs.get("is_intersection", False)
                ]
                connectors = {
                    t: _connectors_in_intersection(nusc_map, t)
                    for t in intersection_tokens
                }
                map_cache[map_name] = (nusc_map, intersection_tokens, connectors)

            nusc_map, intersection_tokens, connectors = map_cache[map_name]
            export_scene(
                nusc,
                nusc_map,
                scene,
                intersection_tokens,
                connectors,
                classification_cache,
                conn,
            )

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
