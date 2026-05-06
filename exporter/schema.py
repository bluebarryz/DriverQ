"""SQLite schema for the nuscenes_explorer scene database."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenes (
    scene_token TEXT PRIMARY KEY,
    scene_name  TEXT NOT NULL UNIQUE,
    location    TEXT NOT NULL,
    num_frames  INTEGER NOT NULL,
    data_version TEXT NOT NULL,
    subset      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ego_poses (
    scene_token    TEXT NOT NULL REFERENCES scenes(scene_token),
    frame_idx      INTEGER NOT NULL,
    timestamp      INTEGER NOT NULL,
    ego_x          REAL NOT NULL,
    ego_y          REAL NOT NULL,
    ego_z          REAL NOT NULL,
    ego_qw         REAL NOT NULL,
    ego_qx         REAL NOT NULL,
    ego_qy         REAL NOT NULL,
    ego_qz         REAL NOT NULL,
    ego_speed      REAL,
    ego_accel      REAL,
    ego_lane_token TEXT,
    PRIMARY KEY (scene_token, frame_idx)
);

CREATE TABLE IF NOT EXISTS object_poses (
    scene_token    TEXT NOT NULL,
    frame_idx      INTEGER NOT NULL,
    instance_token TEXT NOT NULL,
    category       TEXT NOT NULL,
    x              REAL NOT NULL,
    y              REAL NOT NULL,
    z              REAL NOT NULL,
    qw             REAL NOT NULL,
    qx             REAL NOT NULL,
    qy             REAL NOT NULL,
    qz             REAL NOT NULL,
    width          REAL NOT NULL,
    length         REAL NOT NULL,
    height         REAL NOT NULL,
    speed          REAL,
    accel          REAL,
    lane_token_1        TEXT,
    lane_token_1_dist   REAL,
    lane_token_2        TEXT,
    lane_token_2_dist   REAL,
    PRIMARY KEY (scene_token, frame_idx, instance_token),
    FOREIGN KEY (scene_token, frame_idx) REFERENCES ego_poses(scene_token, frame_idx)
);

CREATE TABLE IF NOT EXISTS object_trajectories (
    scene_token    TEXT NOT NULL REFERENCES scenes(scene_token),
    instance_token TEXT NOT NULL,
    category       TEXT NOT NULL,
    start_frame    INTEGER NOT NULL,
    end_frame      INTEGER NOT NULL,
    points_json    TEXT NOT NULL,
    PRIMARY KEY (scene_token, instance_token)
);

CREATE TABLE IF NOT EXISTS visibility (
    scene_token    TEXT NOT NULL,
    frame_idx      INTEGER NOT NULL,
    instance_token TEXT NOT NULL,
    camera         TEXT NOT NULL,
    visibility_level TEXT,
    bbox_x1        REAL,
    bbox_y1        REAL,
    bbox_x2        REAL,
    bbox_y2        REAL,
    PRIMARY KEY (scene_token, frame_idx, instance_token, camera),
    FOREIGN KEY (scene_token, frame_idx, instance_token)
        REFERENCES object_poses(scene_token, frame_idx, instance_token)
);

CREATE TABLE IF NOT EXISTS intersection_traversals_geometric_data (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_token                   TEXT NOT NULL REFERENCES scenes(scene_token),
    intersection_token            TEXT NOT NULL,
    vehicle_token                 TEXT NOT NULL,
    connector_token               TEXT NOT NULL,
    start_frame                   INTEGER NOT NULL,
    end_frame                     INTEGER NOT NULL,
    connector_1_start_yaw         REAL,
    connector_1_classification    TEXT,
    connector_1_match_score       REAL,
    connector_2_classification    TEXT,
    connector_2_match_score       REAL
);

CREATE TABLE IF NOT EXISTS intersection_traversals (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_token                   TEXT NOT NULL REFERENCES scenes(scene_token),
    intersection_token            TEXT NOT NULL,
    vehicle_token                 TEXT NOT NULL,
    start_frame                   INTEGER NOT NULL,
    end_frame                     INTEGER NOT NULL,
    maneuver                      TEXT NOT NULL,
    intersection_approach_heading REAL
);

CREATE TABLE IF NOT EXISTS centerlines (
    scene_token TEXT NOT NULL REFERENCES scenes(scene_token),
    lane_token  TEXT NOT NULL,
    point_idx   INTEGER NOT NULL,
    x           REAL NOT NULL,
    y           REAL NOT NULL,
    PRIMARY KEY (scene_token, lane_token, point_idx)
);

CREATE TABLE IF NOT EXISTS lane_connectivity (
    from_lane TEXT NOT NULL,
    to_lane   TEXT NOT NULL,
    PRIMARY KEY (from_lane, to_lane)
);

CREATE TABLE IF NOT EXISTS ped_vehicle_crossings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_token     TEXT NOT NULL REFERENCES scenes(scene_token),
    ped_token       TEXT NOT NULL,
    vehicle_token   TEXT NOT NULL,
    ped_frame       INTEGER NOT NULL,
    veh_frame       INTEGER NOT NULL,
    vehicle_stopped INTEGER NOT NULL
);

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

"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_object_poses_instance ON object_poses(instance_token, scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_object_poses_category ON object_poses(category, scene_token);
CREATE INDEX IF NOT EXISTS idx_object_poses_lane ON object_poses(lane_token_1);
CREATE INDEX IF NOT EXISTS idx_object_trajectories_scene_instance ON object_trajectories(scene_token, instance_token);
CREATE INDEX IF NOT EXISTS idx_visibility_instance ON visibility(instance_token, scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_visibility_camera ON visibility(camera, scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_itgd_scene ON intersection_traversals_geometric_data(scene_token);
CREATE INDEX IF NOT EXISTS idx_itgd_vehicle ON intersection_traversals_geometric_data(scene_token, vehicle_token, intersection_token);
CREATE INDEX IF NOT EXISTS idx_it_scene ON intersection_traversals(scene_token);
CREATE INDEX IF NOT EXISTS idx_it_maneuver ON intersection_traversals(maneuver, scene_token);
CREATE INDEX IF NOT EXISTS idx_it_vehicle ON intersection_traversals(scene_token, vehicle_token, intersection_token);
CREATE INDEX IF NOT EXISTS idx_ego_poses_scene ON ego_poses(scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_lane_connectivity_from ON lane_connectivity(from_lane);
CREATE INDEX IF NOT EXISTS idx_ped_vehicle_crossings_scene ON ped_vehicle_crossings(scene_token);
CREATE INDEX IF NOT EXISTS idx_ped_vehicle_crossings_vehicle ON ped_vehicle_crossings(vehicle_token);
CREATE INDEX IF NOT EXISTS idx_kf_scene_actor_frame ON kinematic_features(scene_token, actor_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_kf_scene_frame ON kinematic_features(scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_kf_scene_ego ON kinematic_features(scene_token, is_ego, frame_idx);
"""
