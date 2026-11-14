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

CREATE TABLE IF NOT EXISTS intersection_traversals (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_token                   TEXT NOT NULL REFERENCES scenes(scene_token),
    intersection_token            TEXT NOT NULL,
    vehicle_token                 TEXT NOT NULL,
    start_frame                   INTEGER NOT NULL,
    end_frame                     INTEGER NOT NULL,
    maneuver                      TEXT NOT NULL, -- left|right|straight|u_turn|unknown
    connector_token               TEXT NOT NULL, -- best-matching lane connector
    connector_start_yaw           REAL NOT NULL,
    match_score                   REAL NOT NULL, -- geometric fit of that connector
    class_margin                  REAL,          -- score gap to nearest differing label
    observed_fraction             REAL NOT NULL, -- fraction of the connector observed
    n_arms                        INTEGER NOT NULL, -- road arms; <3 = not a real intersection
    entry_arm                     INTEGER NOT NULL, -- arm cluster ids from maneuver_utils.intersections
    exit_arm                      INTEGER NOT NULL  -- (two-way road = one arm on both sides)
);

-- One row per vehicle pair whose matched connector centerlines cross while
-- both traverse the same intersection in (near-)overlapping frame windows.
CREATE TABLE IF NOT EXISTS intersection_conflicts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_token        TEXT NOT NULL REFERENCES scenes(scene_token),
    intersection_token TEXT NOT NULL,
    vehicle_a          TEXT NOT NULL,
    vehicle_b          TEXT NOT NULL,
    conflict_x         REAL NOT NULL, -- where the connector centerlines cross
    conflict_y         REAL NOT NULL,
    a_arrival_frame    REAL,          -- interpolated frame at the conflict point;
    b_arrival_frame    REAL,          -- NULL if not observed reaching it
    pet_seconds        REAL           -- post-encroachment time; NULL unless both arrived
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

CREATE TABLE IF NOT EXISTS lane_change_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_token TEXT NOT NULL REFERENCES scenes(scene_token),
    vehicle_id  TEXT NOT NULL,
    lane_a      TEXT NOT NULL,
    lane_b      TEXT NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame   INTEGER NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_object_poses_scene ON object_poses(scene_token);
CREATE INDEX IF NOT EXISTS idx_object_poses_instance ON object_poses(instance_token, scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_object_poses_category ON object_poses(category, scene_token);
CREATE INDEX IF NOT EXISTS idx_object_poses_lane ON object_poses(lane_token_1);
CREATE INDEX IF NOT EXISTS idx_centerlines_scene ON centerlines(scene_token);
CREATE INDEX IF NOT EXISTS idx_lane_change_events_scene ON lane_change_events(scene_token);
CREATE INDEX IF NOT EXISTS idx_lane_change_events_vehicle ON lane_change_events(scene_token, vehicle_id);
CREATE INDEX IF NOT EXISTS idx_object_trajectories_scene_instance ON object_trajectories(scene_token, instance_token);
CREATE INDEX IF NOT EXISTS idx_visibility_instance ON visibility(instance_token, scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_visibility_camera ON visibility(camera, scene_token, frame_idx);
CREATE INDEX IF NOT EXISTS idx_it_scene ON intersection_traversals(scene_token);
CREATE INDEX IF NOT EXISTS idx_ic_scene ON intersection_conflicts(scene_token, intersection_token);
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
