#!/usr/bin/env python3
"""
Export per-object camera visibility to SQLite, export as `visibility` table.

For every keyframe, projects each 3D bounding box onto the 6 ego cameras
and writes one visibility row per (object, frame, camera) where visible.

Usage:
python export_visibility.py --db scene_data.db
python export_visibility.py --db scene_data.db --scene scene-0001
"""

import argparse
import json
import os
import sqlite3
import sys

import numpy as np
from dotenv import load_dotenv

from camera_utils.camera_projection import (
    get_box_corners_world,
    world_to_camera,
    project_to_image,
    axis_aligned_box,
)

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_env_path)

DATA_VERSION = os.environ.get("DATA_VERSION", "v1.0-trainval")

CAMERAS = [
    "CAM_FRONT",
    "CAM_FRONT_LEFT",
    "CAM_FRONT_RIGHT",
    "CAM_BACK",
    "CAM_BACK_LEFT",
    "CAM_BACK_RIGHT",
]

IMG_W, IMG_H = 1600, 900

_TRACKED_PREFIXES = ("vehicle.", "human.pedestrian")


def load_table(name: str, dataroot: str):
    path = os.path.join(dataroot, DATA_VERSION, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def load_tables(dataroot: str) -> dict:
    print("Loading tables...")
    scenes_all = load_table("scene", dataroot)
    samples_all = load_table("sample", dataroot)
    sd_all = load_table("sample_data", dataroot)
    ego_all = load_table("ego_pose", dataroot)
    ann_all = load_table("sample_annotation", dataroot)
    inst_all = load_table("instance", dataroot)
    cat_all = load_table("category", dataroot)
    cal_all = load_table("calibrated_sensor", dataroot)
    sensor_all = load_table("sensor", dataroot)
    vis_all = load_table("visibility", dataroot)
    print("  tables loaded")

    sample_by_token = {s["token"]: s for s in samples_all}
    ego_by_token = {e["token"]: e for e in ego_all}
    sensor_by_token = {s["token"]: s for s in sensor_all}
    cal_by_token = {c["token"]: c for c in cal_all}
    vis_by_token = {v["token"]: v["level"] for v in vis_all}

    sd_cam_by_sample: dict = {}
    for sd in sd_all:
        cal = cal_by_token.get(sd.get("calibrated_sensor_token", ""), {})
        if not cal:
            continue
        sensor = sensor_by_token.get(cal.get("sensor_token", ""), {})
        if sensor.get("modality") != "camera":
            continue
        sd_cam_by_sample.setdefault(sd["sample_token"], []).append(sd)

    channel_by_cal: dict = {}
    cam_cal_by_token: dict = {}
    for c in cal_all:
        s = sensor_by_token.get(c["sensor_token"], {})
        if s.get("modality") == "camera":
            channel_by_cal[c["token"]] = s["channel"]
            cam_cal_by_token[c["token"]] = c

    ann_by_sample: dict = {}
    for ann in ann_all:
        ann_by_sample.setdefault(ann["sample_token"], []).append(ann)

    cat_by_token = {c["token"]: c["name"] for c in cat_all}
    cat_by_instance = {i["token"]: cat_by_token[i["category_token"]] for i in inst_all}

    return {
        "scenes_all": scenes_all,
        "sample_by_token": sample_by_token,
        "ego_by_token": ego_by_token,
        "cam_cal_by_token": cam_cal_by_token,
        "sd_cam_by_sample": sd_cam_by_sample,
        "channel_by_cal": channel_by_cal,
        "ann_by_sample": ann_by_sample,
        "cat_by_instance": cat_by_instance,
        "vis_by_token": vis_by_token,
    }


def export_scene_visibility(scene: dict, tables: dict, conn: sqlite3.Connection):
    scene_token = scene["token"]
    scene_name = scene["name"]

    sample_by_token = tables["sample_by_token"]
    ego_by_token = tables["ego_by_token"]
    cam_cal_by_token = tables["cam_cal_by_token"]
    sd_cam_by_sample = tables["sd_cam_by_sample"]
    channel_by_cal = tables["channel_by_cal"]
    ann_by_sample = tables["ann_by_sample"]
    cat_by_instance = tables["cat_by_instance"]
    vis_by_token = tables["vis_by_token"]

    rows = []
    frame_idx = 0
    token = scene["first_sample_token"]

    while token:
        sample = sample_by_token[token]

        ann_items = []
        for ann in ann_by_sample.get(token, []):
            cat = cat_by_instance.get(ann["instance_token"], "unknown")
            if not any(cat.startswith(p) for p in _TRACKED_PREFIXES):
                continue
            corners_w = get_box_corners_world(
                ann["translation"], ann["size"], ann["rotation"]
            )
            ann_items.append((ann, corners_w))

        sd_by_channel: dict = {}
        for sd in sd_cam_by_sample.get(token, []):
            if not sd.get("is_key_frame"):
                continue
            ch = channel_by_cal.get(sd.get("calibrated_sensor_token", ""))
            if ch in CAMERAS:
                sd_by_channel[ch] = sd

        for channel in CAMERAS:
            sd = sd_by_channel.get(channel)
            if sd is None:
                continue
            cal = cam_cal_by_token.get(sd.get("calibrated_sensor_token", ""))
            ego = ego_by_token.get(sd.get("ego_pose_token", ""))
            if cal is None or ego is None:
                continue

            K = np.array(cal["camera_intrinsic"], dtype=np.float64)

            for ann, corners_w in ann_items:
                corners_c = world_to_camera(
                    corners_w,
                    ego["translation"],
                    ego["rotation"],
                    cal["translation"],
                    cal["rotation"],
                )
                corners_2d = project_to_image(corners_c, K)
                if corners_2d is None:
                    continue
                bbox = axis_aligned_box(corners_2d, IMG_W, IMG_H)
                if bbox is None:
                    continue

                vis_level = vis_by_token.get(ann.get("visibility_token", ""), "")
                x1, y1, x2, y2 = bbox
                rows.append(
                    (
                        scene_token,
                        frame_idx,
                        ann["instance_token"],
                        channel,
                        vis_level,
                        x1,
                        y1,
                        x2,
                        y2,
                    )
                )

        frame_idx += 1
        token = sample["next"]

    conn.executemany(
        "INSERT OR REPLACE INTO visibility (scene_token, frame_idx, instance_token, camera, visibility_level, bbox_x1, bbox_y1, bbox_x2, bbox_y2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"  {scene_name} | {frame_idx} frames | {len(rows)} visibility rows")


def main():
    parser = argparse.ArgumentParser(description="Export camera visibility to SQLite")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--scene", default=None, help="Single scene name")
    parser.add_argument("--dataroot", default=os.environ.get("NUSCENES_ROOT"))
    args = parser.parse_args()

    if not args.dataroot:
        print(
            "ERROR: --dataroot required (or set NUSCENES_ROOT in .env)", file=sys.stderr
        )
        sys.exit(1)

    tables = load_tables(args.dataroot)
    scenes_all = tables["scenes_all"]

    if args.scene:
        selected = [s for s in scenes_all if s["name"] == args.scene]
        assert selected, f"scene '{args.scene}' not found"
    else:
        selected = scenes_all

    conn = sqlite3.connect(args.db)

    print(f"\nExporting visibility for {len(selected)} scene(s)")
    for scene in selected:
        export_scene_visibility(scene, tables, conn)

    conn.commit()

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
