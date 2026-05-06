"""Geometry utilities for projecting NuScenes 3D boxes into camera images."""

import numpy as np


def quat_to_rot(w, x, y, z) -> np.ndarray:
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def transform_matrix(translation, rotation_wxyz, inverse: bool = False) -> np.ndarray:
    R = quat_to_rot(*rotation_wxyz)
    t = np.asarray(translation, dtype=np.float64)
    mat = np.eye(4, dtype=np.float64)
    if inverse:
        mat[:3, :3] = R.T
        mat[:3, 3] = -(R.T @ t)
    else:
        mat[:3, :3] = R
        mat[:3, 3] = t
    return mat


def get_box_corners_world(translation, size, rotation_wxyz) -> np.ndarray:
    w, l, h = size
    hl, hw, hh = l / 2, w / 2, h / 2
    local = np.array(
        [
            [hl, hw, hh],
            [hl, hw, -hh],
            [hl, -hw, hh],
            [hl, -hw, -hh],
            [-hl, hw, hh],
            [-hl, hw, -hh],
            [-hl, -hw, hh],
            [-hl, -hw, -hh],
        ],
        dtype=np.float64,
    ).T
    R = quat_to_rot(*rotation_wxyz)
    center = np.asarray(translation, dtype=np.float64)
    return R @ local + center[:, None]


def world_to_camera(corners_world, ego_t, ego_r_wxyz, cam_t, cam_r_wxyz) -> np.ndarray:
    T_world_to_ego = transform_matrix(ego_t, ego_r_wxyz, inverse=True)
    T_ego_to_cam = transform_matrix(cam_t, cam_r_wxyz, inverse=True)
    T = T_ego_to_cam @ T_world_to_ego
    corners_h = np.vstack([corners_world, np.ones((1, corners_world.shape[1]))])
    return (T @ corners_h)[:3]


def project_to_image(corners_cam: np.ndarray, K: np.ndarray, min_z: float = 0.5):
    valid = corners_cam[2] > min_z
    if not np.any(valid):
        return None
    pts = corners_cam[:, valid]
    uv = K[0:2, 0:2] @ (pts[:2] / pts[2]) + K[0:2, 2:3]
    out = np.full((2, corners_cam.shape[1]), np.nan)
    out[:, valid] = uv
    return out


def axis_aligned_box(corners_2d: np.ndarray, img_w: int, img_h: int, pad: int = 5):
    mask = ~np.isnan(corners_2d[0])
    if not np.any(mask):
        return None
    pts = corners_2d[:, mask]
    x1 = int(max(0, np.min(pts[0]) - pad))
    y1 = int(max(0, np.min(pts[1]) - pad))
    x2 = int(min(img_w - 1, np.max(pts[0]) + pad))
    y2 = int(min(img_h - 1, np.max(pts[1]) + pad))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)
