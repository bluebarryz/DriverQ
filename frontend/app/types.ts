export interface EgoPose {
  x: number; y: number; z: number;
  qw: number; qx: number; qy: number; qz: number;
  speed: number; accel: number;
}

export interface Annotation {
  token: string;
  x: number; y: number; z: number;
  w: number; l: number; h: number;
  qw: number; qx: number; qy: number; qz: number;
  cat: string;
  speed: number; accel: number;
}

export interface Frame {
  frame_idx: number;
  timestamp: number;
  ego: EgoPose;
  annotations: Annotation[];
}

export interface Maneuver {
  vehicle_token: string;
  maneuver: string;
  start_frame: number;
  end_frame: number;
  intersection_token: string;
  connector_token: string | null;
  connector_start_yaw: number | null;
}

export interface SceneData {
  scene_token: string;
  scene_name: string;
  location: string;
  data_version: string;
  subset: string;
  frames: Frame[];
  centerlines: number[][][];
  maneuvers: Maneuver[];
}

export interface SceneListEntry {
  scene_token: string;
  scene_name: string;
  location: string;
  num_frames: number;
  data_version: string;
  subset: string;
}

export interface TrajectoryPoint3D {
  frame_idx: number;
  x: number;
  y: number;
  z: number;
}
