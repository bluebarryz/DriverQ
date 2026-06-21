#!/usr/bin/env python3
"""
Build the nuScenes scene database.

Usage:
python build_db.py --db scene_data.db                       # full pipeline
python build_db.py --db scene_data.db --scene scene-0001    # single scene
python build_db.py --db scene_data.db --stage poses         # single stage
python build_db.py --db scene_data.db --dataroot /path/to/v1.0-trainval02
python build_db.py --db scene_data.db --schema-only

Stage order:
  poses -> visibility -> traversals_geom -> crossings ->
  kinematic -> cutin -> lane_change -> traversals
"""

import argparse
import os
import sqlite3
import subprocess
import sys

from schema import SCHEMA, INDEXES

PIPELINE = [
    "poses",
    "visibility",
    "traversals_geom",
    "crossings",
    "kinematic",
    "cutin",
    "lane_change",
    "traversals",
]

SCRIPTS = {
    "poses": "export_poses.py",
    "visibility": "export_visibility.py",
    "traversals_geom": "export_intersection_traversals_geometric_data.py",
    "crossings": "export_crossings.py",
    "kinematic": "export_kinematic_features.py",
    "cutin": "export_cutin_events.py",
    "lane_change": "export_lane_change_events.py",
    "traversals": "export_intersection_traversals.py",
}


def create_schema(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()
    print(f"Schema created: {db_path}")


def create_indexes(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA temp_store = DEFAULT")
    conn.execute("PRAGMA cache_size = -20000")
    conn.executescript(INDEXES)
    conn.execute("ANALYZE")
    conn.close()
    print("Indexes created and ANALYZE complete.")


def configure_bulk_load_pragmas(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -100000")
    conn.close()


def _subset_name(dataroot: str) -> str:
    return os.path.basename(os.path.normpath(dataroot))


def _scene_names_for_subset(db_path: str, subset: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT scene_name FROM scenes WHERE subset = ? ORDER BY scene_name", (subset,)
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def run_stage(
    stage: str,
    db_path: str,
    scene: str | None,
    dataroot: str | None,
    subsets: list[str] | None,
    workers: int,
    force_poses: bool,
    with_map_features: bool,
    centerlines_only: bool,
):
    assert stage in SCRIPTS, f"unknown stage: {stage}. Choose from: {list(SCRIPTS)}"
    script_dir = os.path.dirname(os.path.abspath(__file__))

    cmd = [sys.executable, os.path.join(script_dir, SCRIPTS[stage]), "--db", db_path]
    if scene:
        cmd.extend(["--scene", scene])

    if stage in ("poses", "visibility") and dataroot:
        cmd.extend(["--dataroot", dataroot])

    if stage == "traversals_geom":
        if subsets:
            cmd.extend(["--subsets", *subsets])
        elif dataroot:
            cmd.extend(["--subsets", dataroot])

    subset = _subset_name(dataroot) if dataroot else None
    if stage == "crossings" and subset and not scene:
        cmd.extend(["--subset", subset])

    if stage == "poses":
        cmd.extend(["--workers", str(workers)])
        if force_poses:
            cmd.append("--force")
        if centerlines_only:
            cmd.append("--centerlines-only")
        if with_map_features:
            cmd.extend(["--with-lane-tokens", "--with-lane-connectivity"])

    if stage == "crossings":
        cmd.extend(["--workers", str(workers)])

    print(f"\n{'='*60}\nRunning stage: {stage}\n{'='*60}")
    result = subprocess.run(cmd, cwd=script_dir)
    if result.returncode != 0:
        print(f"Stage {stage} failed (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Build the nuScenes scene database")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument(
        "--scene", default=None, help="Single scene name (e.g. scene-0001)"
    )
    parser.add_argument(
        "--dataroot",
        default=None,
        help="NuScenes subset root (for poses/visibility and subset-scoped crossings)",
    )
    parser.add_argument(
        "--subsets",
        nargs="+",
        default=None,
        help="NuScenes roots for traversals_geom (overrides --dataroot for that stage)",
    )
    parser.add_argument(
        "--stage",
        default=None,
        choices=PIPELINE,
        help="Run only a single stage",
    )
    parser.add_argument(
        "--schema-only", action="store_true", help="Only create schema and indexes"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="Worker count for poses/crossings stages",
    )
    parser.add_argument(
        "--force-poses",
        action="store_true",
        help="Re-export poses even if scene pose rows already exist",
    )
    parser.add_argument(
        "--with-map-features",
        action="store_true",
        help="Include lane tokens and lane connectivity in poses stage (required for lane_change)",
    )
    parser.add_argument(
        "--centerlines-only",
        action="store_true",
        help="Run poses stage in centerlines-only backfill mode",
    )
    args = parser.parse_args()

    if args.centerlines_only and args.stage not in (None, "poses"):
        parser.error("--centerlines-only can only be used with --stage poses")

    db_path = os.path.abspath(args.db)
    create_schema(db_path)
    create_indexes(db_path)
    configure_bulk_load_pragmas(db_path)

    if args.schema_only:
        return

    stages = [args.stage] if args.stage else PIPELINE
    subset = _subset_name(args.dataroot) if args.dataroot else None

    for stage in stages:
        if stage == "crossings" and args.scene is None and subset:
            if not _scene_names_for_subset(db_path, subset):
                print(
                    f"No scenes found for subset '{subset}'. Skipping crossings.",
                    file=sys.stderr,
                )
                continue

        run_stage(
            stage,
            db_path,
            args.scene,
            args.dataroot,
            args.subsets,
            args.workers,
            args.force_poses,
            args.with_map_features,
            args.centerlines_only,
        )

    create_indexes(db_path)
    print(f"\nDatabase ready: {db_path}")


if __name__ == "__main__":
    main()
