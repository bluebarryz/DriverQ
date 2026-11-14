"""Map and geometry helpers for intersection traversal detection."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Point
from shapely.prepared import prep

from nuscenes.map_expansion.arcline_path_utils import discretize_lane
from nuscenes.map_expansion.map_api import NuScenesMap

HEADING_WEIGHT = 5.0  # metres of score per radian of mean heading error

_centerline_cache: dict[tuple, np.ndarray] = {}
_lane_points_cache: dict[tuple, list[tuple[float, float, float]]] = {}


def connector_centerline(nusc_map: NuScenesMap, token: str) -> np.ndarray:
    key = (id(nusc_map), token)
    if key not in _centerline_cache:
        arcline = nusc_map.arcline_path_3.get(token, [])
        assert arcline, f"no arcline data for connector {token}"
        _centerline_cache[key] = np.array(
            [(p[0], p[1]) for p in discretize_lane(arcline, 0.5)]
        )
    return _centerline_cache[key]


def lane_direction_at(
    nusc_map: NuScenesMap, lane_token: str, x: float, y: float
) -> float | None:
    """Direction of travel of a lane at the centerline point closest to (x, y)."""
    key = (id(nusc_map), lane_token)
    if key not in _lane_points_cache:
        arcline = nusc_map.arcline_path_3.get(lane_token)
        _lane_points_cache[key] = (
            [(p[0], p[1], p[2]) for p in discretize_lane(arcline, 1.0)]
            if arcline
            else []
        )
    pts = _lane_points_cache[key]
    if not pts:
        return None
    return min(pts, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)[2]


def project_onto_polyline(polyline: np.ndarray, point: np.ndarray) -> float:
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


@dataclass
class ScoredConnector:
    token: str
    score: float  # mean position error + HEADING_WEIGHT * mean heading error
    observed_fraction: float  # fraction of the connector the trajectory covers


def score_connectors(
    nusc_map: NuScenesMap,
    trajectory: list[tuple[float, float, float]],  # (x, y, yaw) per frame
    candidate_tokens: list[str],
) -> list[ScoredConnector]:
    """Score each candidate connector against the observed trajectory:
    align by arc length from the projection of the first point, then compare
    positions and per-point headings. Lower is better; sorted ascending."""
    assert len(trajectory) >= 2
    traj = np.asarray([(x, y) for x, y, _ in trajectory])
    yaws = np.asarray([yaw for _, _, yaw in trajectory])
    traj_cum = np.concatenate(
        ([0.0], np.cumsum(np.linalg.norm(np.diff(traj, axis=0), axis=1)))
    )

    scored: list[ScoredConnector] = []
    for ct in candidate_tokens:
        cl = connector_centerline(nusc_map, ct)
        if cl.shape[0] < 2:
            continue
        clen = float(np.linalg.norm(np.diff(cl, axis=0), axis=1).sum())
        s0 = project_onto_polyline(cl, traj[0])
        matched = _resample_polyline(cl, s0 + traj_cum)
        pos_err = float(np.linalg.norm(traj - matched, axis=1).mean())

        # Tangent per matched point; clamped duplicate points at the connector
        # end reuse the last real direction.
        diffs = np.diff(matched, axis=0)
        lens = np.linalg.norm(diffs, axis=1)
        dirs, last_dir, heading_errs = [], None, []
        for i in range(len(matched)):
            j = min(i, len(diffs) - 1)
            if lens[j] > 1e-9:
                last_dir = math.atan2(diffs[j][1], diffs[j][0])
            dirs.append(last_dir)
        for yaw, d in zip(yaws, dirs):
            if d is not None:
                heading_errs.append(abs((yaw - d + math.pi) % (2 * math.pi) - math.pi))
        heading_err = sum(heading_errs) / len(heading_errs) if heading_errs else 0.0

        covered = max(0.0, min(s0 + traj_cum[-1], clen) - min(s0, clen))
        scored.append(
            ScoredConnector(
                token=ct,
                score=pos_err + HEADING_WEIGHT * heading_err,
                observed_fraction=covered / clen if clen > 0 else 0.0,
            )
        )

    scored.sort(key=lambda s: s.score)
    return scored


def connectors_in_intersection(
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
        xy = connector_centerline(nusc_map, tok)
        if any(prepared.contains(Point(x, y)) for x, y in xy):
            result.append(tok)
    return result


def build_intersection_index(
    nusc_map: NuScenesMap, tokens: list[str]
) -> list[tuple[str, object]]:
    index = []
    for token in tokens:
        rec = nusc_map.get("road_segment", token)
        poly = nusc_map.extract_polygon(rec["polygon_token"])
        if not poly.is_valid:
            poly = poly.buffer(0)
        index.append((token, prep(poly)))
    return index


def point_to_intersection(
    x: float, y: float, index: list[tuple[str, object]]
) -> str | None:
    pt = Point(x, y)
    for token, prepared in index:
        if prepared.contains(pt):
            return token
    return None
