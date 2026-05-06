#!/usr/bin/env python3
"""
Export base keyframe camera JPGs into frontend/public/cameras.
This script copies (or optionally resizes) NuScenes camera keyframes to:
  {output}/{scene_name}/cameras/{frame_idx}/{channel}.jpg

Usage:
python export_camera_images.py
python export_camera_images.py --scene scene-0001
python export_camera_images.py --output ../frontend/public/cameras
python export_camera_images.py --output-width 800
"""

import argparse
import json
import os
import shutil
import sys

from dotenv import load_dotenv

try:
    from PIL import Image
except ImportError:
    Image = None

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


def load_table(name: str, dataroot: str):
    path = os.path.join(dataroot, DATA_VERSION, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def load_camera_tables(dataroot: str) -> dict:
    print("Loading camera tables...")
    scenes_all = load_table("scene", dataroot)
    samples_all = load_table("sample", dataroot)
    sd_all = load_table("sample_data", dataroot)
    cal_all = load_table("calibrated_sensor", dataroot)
    sensor_all = load_table("sensor", dataroot)

    sample_by_token = {s["token"]: s for s in samples_all}
    sensor_by_token = {s["token"]: s for s in sensor_all}
    cal_by_token = {c["token"]: c for c in cal_all}

    channel_by_cal = {}
    for c in cal_all:
        sensor = sensor_by_token.get(c["sensor_token"], {})
        if sensor.get("modality") == "camera":
            channel_by_cal[c["token"]] = sensor["channel"]

    sd_cam_by_sample = {}
    for sd in sd_all:
        if not sd.get("is_key_frame"):
            continue
        cal_token = sd.get("calibrated_sensor_token", "")
        channel = channel_by_cal.get(cal_token)
        if channel not in CAMERAS:
            continue
        sd_cam_by_sample.setdefault(sd["sample_token"], {})[channel] = sd

    print("  tables loaded")
    return {
        "scenes_all": scenes_all,
        "sample_by_token": sample_by_token,
        "sd_cam_by_sample": sd_cam_by_sample,
    }


def copy_or_resize_image(src: str, dst: str, output_width: int | None):
    if output_width is None:
        shutil.copy2(src, dst)
        return

    if Image is None:
        raise RuntimeError(
            "Pillow is required for --output-width. Install with: pip install pillow"
        )

    with Image.open(src) as img:
        w, h = img.size
        out_h = int(h * output_width / w)
        img_small = img.resize((output_width, out_h), Image.LANCZOS)
        img_small.save(dst, "JPEG", quality=85)


def export_scene_images(
    scene: dict,
    tables: dict,
    dataroot: str,
    output_dir: str,
    output_width: int | None,
    overwrite: bool,
):
    scene_name = scene["name"]
    sample_by_token = tables["sample_by_token"]
    sd_cam_by_sample = tables["sd_cam_by_sample"]

    frame_idx = 0
    token = scene["first_sample_token"]
    copied = 0

    while token:
        sample = sample_by_token[token]
        sd_by_channel = sd_cam_by_sample.get(token, {})

        for channel in CAMERAS:
            sd = sd_by_channel.get(channel)
            if sd is None:
                continue

            filename = sd.get("filename", "")
            if not filename:
                continue

            src = os.path.join(dataroot, filename)
            if not os.path.isfile(src):
                continue

            rel_dir = os.path.join(scene_name, "cameras", str(frame_idx))
            abs_dir = os.path.join(output_dir, rel_dir)
            os.makedirs(abs_dir, exist_ok=True)
            dst = os.path.join(abs_dir, f"{channel}.jpg")

            if (not overwrite) and os.path.exists(dst):
                continue

            copy_or_resize_image(src, dst, output_width)
            copied += 1

        frame_idx += 1
        token = sample["next"]

    print(f"  {scene_name} | {frame_idx} frames | {copied} images")


def main():
    parser = argparse.ArgumentParser(description="Export base camera keyframe images")
    parser.add_argument("--scene", default=None, help="Single scene name")
    parser.add_argument(
        "--dataroot", default=os.environ.get("NUSCENES_ROOT"), help="NuScenes dataroot"
    )
    parser.add_argument(
        "--output",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "frontend",
            "public",
            "cameras",
        ),
        help="Output root for camera images",
    )
    parser.add_argument(
        "--output-width",
        type=int,
        default=None,
        help="Optional width to resize output JPGs",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing files"
    )
    args = parser.parse_args()

    if not args.dataroot:
        print(
            "ERROR: --dataroot required (or set NUSCENES_ROOT in .env)", file=sys.stderr
        )
        sys.exit(1)

    if args.output_width is not None and args.output_width <= 0:
        print("ERROR: --output-width must be > 0", file=sys.stderr)
        sys.exit(1)

    tables = load_camera_tables(args.dataroot)
    scenes_all = tables["scenes_all"]

    if args.scene:
        selected = [s for s in scenes_all if s["name"] == args.scene]
        assert selected, f"scene '{args.scene}' not found"
    else:
        selected = scenes_all

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    print(
        f"\nExporting base camera images for {len(selected)} scene(s) -> {output_dir}"
    )
    for scene in selected:
        export_scene_images(
            scene=scene,
            tables=tables,
            dataroot=args.dataroot,
            output_dir=output_dir,
            output_width=args.output_width,
            overwrite=args.overwrite,
        )

    print("Done.")


if __name__ == "__main__":
    main()
