"""Geometry helpers for intersection traversal detection."""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Point
from shapely.prepared import prep

from nuscenes.map_expansion.arcline_path_utils import discretize_lane, principal_value
from nuscenes.map_expansion.map_api import NuScenesMap

# prefices used in nuscenes annotations
VEHICLE_PREFIXES = (
    "vehicle.car",
    "vehicle.truck",
    "vehicle.bus",
    "vehicle.trailer",
    "vehicle.construction",
    "vehicle.motorcycle",
    "vehicle.bicycle",
    "vehicle.emergency",
)

# so we don't have to process already seen connectors
_centerline_cache: dict[tuple, np.ndarray] = {}


def _connector_centerline(nusc_map: NuScenesMap, token: str) -> np.ndarray:
    key = (id(nusc_map), token)
    if key not in _centerline_cache:
        arcline = nusc_map.arcline_path_3.get(token, [])
        assert arcline, f"no arcline data for connector {token}"
        _centerline_cache[key] = np.array(
            [(p[0], p[1]) for p in discretize_lane(arcline, 0.5)]
        )
    return _centerline_cache[key]


def _connector_delta_yaw(nusc_map: NuScenesMap, token: str) -> float:
    arcline = nusc_map.arcline_path_3.get(token, [])
    assert arcline, f"no arcline data for connector {token}"
    return principal_value(arcline[-1]["end_pose"][2] - arcline[0]["start_pose"][2])


def get_connector_start_yaw(
    nusc_map: NuScenesMap, connector_token: str
) -> float | None:
    arcline = nusc_map.arcline_path_3.get(connector_token, [])
    if not arcline:
        return None
    return arcline[0]["start_pose"][2]


def _project_onto_polyline(polyline: np.ndarray, point: np.ndarray) -> float:
    segs = np.diff(polyline, axis=0)
    seg_len_sq = (segs**2).sum(axis=1)
    mask = seg_len_sq > 1e-24
    t = np.zeros_like(seg_len_sq)
    np.divide(
        ((point - polyline[:-1]) * segs).sum(axis=1), seg_len_sq, out=t, where=mask
    )
    np.clip(t, 0.0, 1.0, out=t)
    projs = polyline[:-1] + t[:, None] * segs
    best = int(np.argmin(np.linalg.norm(point - projs, axis=1)))
    seg_lens = np.sqrt(seg_len_sq)
    cum = np.concatenate(([0.0], np.cumsum(seg_lens)))
    return float(cum[best] + t[best] * seg_lens[best])


def _resample_polyline(polyline: np.ndarray, arc_lengths: np.ndarray) -> np.ndarray:
    segs = np.diff(polyline, axis=0)
    seg_lens = np.linalg.norm(segs, axis=1)
    cum = np.concatenate(([0.0], np.cumsum(seg_lens)))
    s = np.clip(arc_lengths, 0.0, cum[-1])
    idx = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(segs) - 1)
    frac = np.where(seg_lens[idx] > 1e-12, (s - cum[idx]) / seg_lens[idx], 0.0)
    return polyline[idx] + frac[:, None] * segs[idx]


def match_trajectory_to_connector(
    nusc_map: NuScenesMap,
    trajectory_xy: list[tuple[float, float]],
    candidate_tokens: list[str],
) -> tuple[str, float, str, float]:
    """Top-2 connectors by position+heading score. Returns (best_token, best_score, second_token, second_score)."""
    assert len(trajectory_xy) >= 2
    traj = np.asarray(trajectory_xy)
    traj_cum = np.concatenate(
        ([0.0], np.cumsum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))
    )

    traj_vec = traj[-1] - traj[0]
    traj_heading = (
        math.atan2(float(traj_vec[1]), float(traj_vec[0]))
        if np.linalg.norm(traj_vec) > 1e-6
        else None
    )

    scored: list[tuple[float, str]] = []
    for ct in candidate_tokens:
        cl = _connector_centerline(nusc_map, ct)
        if cl.shape[0] < 2:
            continue
        s0 = _project_onto_polyline(cl, traj[0])
        matched = _resample_polyline(cl, s0 + traj_cum)
        pos_dist = float(np.linalg.norm(traj - matched, axis=1).mean())

        heading_err = 0.0
        if traj_heading is not None:
            m_vec = matched[-1] - matched[0]
            if np.linalg.norm(m_vec) > 1e-6:
                heading_err = abs(
                    principal_value(
                        math.atan2(float(m_vec[1]), float(m_vec[0])) - traj_heading
                    )
                )

        scored.append((pos_dist + heading_err, ct))

    scored.sort()
    best_score, best_token = scored[0] if scored else (float("inf"), "")
    second_score, second_token = scored[1] if len(scored) >= 2 else (float("inf"), "")
    return best_token, best_score, second_token, second_score


def _connectors_in_intersection(
    nusc_map: NuScenesMap, intersection_token: str
) -> list[str]:
    rec = nusc_map.get("road_segment", intersection_token)
    poly = nusc_map.extract_polygon(rec["polygon_token"])
    if not poly.is_valid:
        poly = poly.buffer(0)
    prepared = prep(poly)
    candidates = nusc_map.explorer.get_records_in_patch(
        poly.bounds, ["lane_connector"], mode="intersect"
    )

    result = []
    for tok in candidates.get("lane_connector", []):
        if not nusc_map.arcline_path_3.get(tok):
            continue
        xy = _connector_centerline(nusc_map, tok)
        if any(prepared.contains(Point(x, y)) for x, y in xy):
            result.append(tok)
    return result


def _build_intersection_index(
    nusc_map: NuScenesMap, tokens: set[str]
) -> list[tuple[str, object]]:
    index = []
    for token in tokens:
        rec = nusc_map.get("road_segment", token)
        poly = nusc_map.extract_polygon(rec["polygon_token"])
        if not poly.is_valid:
            poly = poly.buffer(0)
        index.append((token, prep(poly)))
    return index


def _point_to_intersection(
    x: float, y: float, index: list[tuple[str, object]]
) -> str | None:
    pt = Point(x, y)
    for token, prepared in index:
        if prepared.contains(pt):
            return token
    return None
