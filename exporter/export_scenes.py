#!/usr/bin/env python3
"""
Export the `scenes` table to SQLite.

Usage:
python export_scenes.py --db scene_data.db
python export_scenes.py --db scene_data.db --scene scene-0001
"""

import argparse
import json
import os
import sqlite3
import sys

from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_env_path)

DATA_VERSION = os.environ.get("DATA_VERSION", "v1.0-trainval")


def load_table(name: str, dataroot: str):
    path = os.path.join(dataroot, DATA_VERSION, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def get_subset(dataroot: str) -> str:
    return os.path.basename(os.path.normpath(dataroot))


def ensure_scenes_columns(conn: sqlite3.Connection):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
    if "data_version" not in cols:
        conn.execute("ALTER TABLE scenes ADD COLUMN data_version TEXT")
    if "subset" not in cols:
        conn.execute("ALTER TABLE scenes ADD COLUMN subset TEXT")
    conn.commit()


def export_scenes(conn: sqlite3.Connection, dataroot: str, scene_name: str | None):
    scenes_all = load_table("scene", dataroot)
    logs_all = load_table("log", dataroot)
    log_by_token = {l["token"]: l for l in logs_all}

    if scene_name:
        selected = [s for s in scenes_all if s["name"] == scene_name]
        assert selected, f"scene '{scene_name}' not found"
    else:
        selected = scenes_all

    subset = get_subset(dataroot)

    rows = []
    for scene in selected:
        log = log_by_token[scene["log_token"]]
        rows.append(
            (
                scene["token"],
                scene["name"],
                log["location"],
                int(scene["nbr_samples"]),
                DATA_VERSION,
                subset,
            )
        )

    conn.executemany(
        "INSERT INTO scenes (scene_token, scene_name, location, num_frames, data_version, subset) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(scene_token) DO UPDATE SET "
        "scene_name=excluded.scene_name, "
        "location=excluded.location, "
        "num_frames=excluded.num_frames, "
        "data_version=excluded.data_version, "
        "subset=excluded.subset",
        rows,
    )
    conn.commit()

    print(
        f"Exported {len(rows)} scene row(s) with data_version={DATA_VERSION}, subset={subset}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Export NuScenes scenes metadata to SQLite"
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument(
        "--scene", default=None, help="Single scene name (e.g. scene-0001)"
    )
    parser.add_argument("--dataroot", default=os.environ.get("NUSCENES_ROOT"))
    args = parser.parse_args()

    if not args.dataroot:
        print(
            "ERROR: --dataroot required (or set NUSCENES_ROOT in .env)",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    ensure_scenes_columns(conn)
    export_scenes(conn, args.dataroot, args.scene)
    conn.close()


if __name__ == "__main__":
    main()
