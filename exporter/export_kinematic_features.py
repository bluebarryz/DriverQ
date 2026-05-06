#!/usr/bin/env python3
"""Populate kinematic_features from ego_poses and object_poses.

Usage:
python exporter/export_kinematic_features.py --db scene_data.db
python exporter/export_kinematic_features.py --db scene_data.db --scene scene-0001
"""

from __future__ import annotations

import argparse
import math
import sqlite3


def _yaw_from_quaternion_wxyz(qw: float, qx: float, qy: float, qz: float) -> float:
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def _create_table(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kinematic_features (
            scene_token              TEXT NOT NULL,
            frame_idx                INTEGER NOT NULL,
            actor_token              TEXT NOT NULL,
            is_ego                   INTEGER NOT NULL,
            yaw                      REAL NOT NULL,
            speed                    REAL,
            s_rel_ego                REAL,
            l_rel_ego                REAL,
            perpendicular_displacement REAL NOT NULL,
            PRIMARY KEY (scene_token, frame_idx, actor_token),
            FOREIGN KEY (scene_token, frame_idx) REFERENCES ego_poses(scene_token, frame_idx)
        );

        CREATE INDEX IF NOT EXISTS idx_kf_scene_actor_frame
        ON kinematic_features(scene_token, actor_token, frame_idx);

        CREATE INDEX IF NOT EXISTS idx_kf_scene_frame
        ON kinematic_features(scene_token, frame_idx);

        CREATE INDEX IF NOT EXISTS idx_kf_scene_ego
        ON kinematic_features(scene_token, is_ego, frame_idx);
        """)


def _scenes(conn: sqlite3.Connection, scene_name: str | None) -> list[sqlite3.Row]:
    if scene_name:
        rows = conn.execute(
            "SELECT scene_token, scene_name FROM scenes WHERE scene_name = ? ORDER BY scene_name",
            (scene_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT scene_token, scene_name FROM scenes ORDER BY scene_name"
        ).fetchall()
    assert rows, f"No scenes found for selection={scene_name!r}"
    return rows


def _build_scene(conn: sqlite3.Connection, scene_token: str) -> int:
    rows = conn.execute(
        """
        SELECT
            op.frame_idx,
            op.instance_token,
            op.x,
            op.y,
            op.speed AS obj_speed,
            op.qw AS obj_qw,
            op.qx AS obj_qx,
            op.qy AS obj_qy,
            op.qz AS obj_qz,
            f.ego_x,
            f.ego_y,
            f.ego_qw,
            f.ego_qx,
            f.ego_qy,
            f.ego_qz
        FROM object_poses op
        JOIN ego_poses f
          ON f.scene_token = op.scene_token
         AND f.frame_idx = op.frame_idx
        WHERE op.scene_token = ?
          AND op.category LIKE 'vehicle.%'
        ORDER BY op.instance_token, op.frame_idx
        """,
        (scene_token,),
    ).fetchall()

    inserts: list[tuple] = []
    prev: dict[str, dict] = {}

    for r in rows:
        actor = r["instance_token"]
        ego_yaw = _yaw_from_quaternion_wxyz(
            r["ego_qw"], r["ego_qx"], r["ego_qy"], r["ego_qz"]
        )
        obj_yaw = _yaw_from_quaternion_wxyz(
            r["obj_qw"], r["obj_qx"], r["obj_qy"], r["obj_qz"]
        )
        dx = r["x"] - r["ego_x"]
        dy = r["y"] - r["ego_y"]
        # frenet
        s_rel = dx * math.cos(ego_yaw) + dy * math.sin(ego_yaw)
        l_rel = -dx * math.sin(ego_yaw) + dy * math.cos(ego_yaw)

        perpendicular_displacement = 0.0
        p = prev.get(actor)
        if p and r["frame_idx"] > p["frame_idx"]:
            world_dx = r["x"] - p["x"]
            world_dy = r["y"] - p["y"]
            nx = -math.sin(p["yaw"])
            ny = math.cos(p["yaw"])
            perpendicular_displacement = world_dx * nx + world_dy * ny

        inserts.append(
            (
                scene_token,
                r["frame_idx"],
                actor,
                0,
                round(obj_yaw, 4),
                None if r["obj_speed"] is None else round(r["obj_speed"], 4),
                round(s_rel, 4),
                round(l_rel, 4),
                round(perpendicular_displacement, 4),
            )
        )
        prev[actor] = {
            "frame_idx": r["frame_idx"],
            "x": r["x"],
            "y": r["y"],
            "yaw": obj_yaw,
        }

    ego_rows = conn.execute(
        """
        SELECT frame_idx, ego_x, ego_y, ego_speed, ego_qw, ego_qx, ego_qy, ego_qz
        FROM ego_poses
        WHERE scene_token = ?
        ORDER BY frame_idx
        """,
        (scene_token,),
    ).fetchall()

    prev_ego: dict | None = None
    for r in ego_rows:
        yaw = _yaw_from_quaternion_wxyz(
            r["ego_qw"], r["ego_qx"], r["ego_qy"], r["ego_qz"]
        )
        perpendicular_displacement = 0.0
        if prev_ego and r["frame_idx"] > prev_ego["frame_idx"]:
            world_dx = r["ego_x"] - prev_ego["x"]
            world_dy = r["ego_y"] - prev_ego["y"]
            nx = -math.sin(prev_ego["yaw"])
            ny = math.cos(prev_ego["yaw"])
            perpendicular_displacement = world_dx * nx + world_dy * ny

        inserts.append(
            (
                scene_token,
                r["frame_idx"],
                "__ego__",
                1,
                round(yaw, 4),
                None if r["ego_speed"] is None else round(r["ego_speed"], 4),
                None,
                None,
                round(perpendicular_displacement, 4),
            )
        )
        prev_ego = {
            "frame_idx": r["frame_idx"],
            "x": r["ego_x"],
            "y": r["ego_y"],
            "yaw": yaw,
        }

    conn.executemany(
        """
        INSERT INTO kinematic_features
            (scene_token, frame_idx, actor_token, is_ego,
             yaw, speed, s_rel_ego, l_rel_ego, perpendicular_displacement)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        inserts,
    )
    return len(inserts)


def main():
    parser = argparse.ArgumentParser(description="Populate kinematic_features table")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--scene", default=None, help="Optional scene name filter")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        _create_table(conn)
        for s in _scenes(conn, args.scene):
            conn.execute("BEGIN")
            conn.execute(
                "DELETE FROM kinematic_features WHERE scene_token = ?",
                (s["scene_token"],),
            )
            n = _build_scene(conn, s["scene_token"])
            conn.commit()
            print(f"{s['scene_name']}: kinematic_features={n}")
        conn.execute("ANALYZE")
        print("Kinematic features export complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
