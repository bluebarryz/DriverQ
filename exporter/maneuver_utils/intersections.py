"""Arm-based maneuver labels for lane connectors.

Each intersection is classified once, from map geometry alone:
  1. Cluster connector endpoints into road "arms" (position + outward heading).
  2. Fewer than 3 arms means the road segment is not a real intersection
     (a bend, merge, or split): every connector is labelled straight.
  3. Otherwise each connector is labelled by the change in arm bearing across
     the intersection, relative to the alternatives from its entry arm: the
     straightest option out of an arm is "continue" (straight) even on a
     curved road, because a turn requires choosing against a straighter option.

Arm bearings are measured on the adjacent lanes just outside the intersection
polygon, so a connector that wiggles internally does not corrupt its label.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from nuscenes.map_expansion.arcline_path_utils import discretize_lane, principal_value
from nuscenes.map_expansion.map_api import NuScenesMap

from .helpers import connectors_in_intersection

ARM_DIST_THRESHOLD = 20.0  # metres between endpoints on the same arm
ARM_HEADING_THRESHOLD = math.pi / 4
LANE_BEARING_SAMPLE = 5.0  # metres of adjacent lane used for an endpoint bearing
STRAIGHT_MAX_DELTA = math.radians(30)
CONTINUE_MAX_DELTA = math.radians(60)  # straightest-option rule applies below this
UTURN_MIN_DELTA = math.radians(150)
MIN_ARMS = 3


@dataclass
class ClassifiedConnector:
    maneuver: str  # 'left' | 'right' | 'straight' | 'u_turn'
    entry_arm: int
    exit_arm: int
    delta: float  # arm-to-arm bearing change, radians (+ = left)
    start_yaw: float


@dataclass
class ClassifiedIntersection:
    n_arms: int
    connectors: dict[str, ClassifiedConnector]


def _circular_mean(angles: list[float]) -> float:
    return math.atan2(
        sum(math.sin(a) for a in angles), sum(math.cos(a) for a in angles)
    )


_bearing_cache: dict[tuple, float | None] = {}


def _lane_end_bearing(nusc_map: NuScenesMap, lane_token: str, at_end: bool) -> float | None:
    """Direction of travel over the first/last LANE_BEARING_SAMPLE metres of a lane."""
    key = (id(nusc_map), lane_token, at_end)
    if key in _bearing_cache:
        return _bearing_cache[key]
    arcline = nusc_map.arcline_path_3.get(lane_token)
    if not arcline:
        _bearing_cache[key] = None
        return None
    pts = discretize_lane(arcline, 0.5)
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    if at_end:
        yaws = [p[2] for p, c in zip(pts, cum) if c >= total - LANE_BEARING_SAMPLE]
    else:
        yaws = [p[2] for p, c in zip(pts, cum) if c <= LANE_BEARING_SAMPLE]
    _bearing_cache[key] = _circular_mean(yaws) if yaws else None
    return _bearing_cache[key]


def _endpoint_bearing(
    nusc_map: NuScenesMap, connector_token: str, entry: bool
) -> float:
    """Inbound (entry) or outbound (exit) bearing of a connector endpoint,
    measured on the adjacent lanes outside the intersection when available."""
    connectivity = nusc_map.connectivity.get(connector_token, {})
    adjacent = connectivity.get("incoming" if entry else "outgoing", [])
    bearings = [
        b
        for lane in adjacent
        if (b := _lane_end_bearing(nusc_map, lane, at_end=entry)) is not None
    ]
    if bearings:
        return _circular_mean(bearings)
    arcline = nusc_map.arcline_path_3[connector_token]
    return arcline[0]["start_pose"][2] if entry else arcline[-1]["end_pose"][2]


def _cluster_arms(
    endpoints: list[tuple[float, float, float]],
) -> list[int]:
    """Union-find over (x, y, outward_heading) endpoints; returns a cluster id
    per endpoint. Same arm = within ARM_DIST_THRESHOLD metres and
    ARM_HEADING_THRESHOLD of outward heading."""
    n = len(endpoints)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        xi, yi, hi = endpoints[i]
        for j in range(i + 1, n):
            xj, yj, hj = endpoints[j]
            if math.hypot(xi - xj, yi - yj) > ARM_DIST_THRESHOLD:
                continue
            if abs(principal_value(hi - hj)) > ARM_HEADING_THRESHOLD:
                continue
            parent[find(i)] = find(j)

    roots: dict[int, int] = {}
    return [roots.setdefault(find(i), len(roots)) for i in range(n)]


def classify_intersection(
    nusc_map: NuScenesMap, intersection_token: str
) -> ClassifiedIntersection:
    tokens = connectors_in_intersection(nusc_map, intersection_token)

    # Two endpoints per connector: entry (index 2k) and exit (index 2k+1).
    # Outward heading points away from the intersection on both sides so that
    # the two directions of a two-way road land in the same cluster.
    endpoints: list[tuple[float, float, float]] = []
    inbound: list[float] = []
    outbound: list[float] = []
    for tok in tokens:
        arcline = nusc_map.arcline_path_3[tok]
        sx, sy, _ = arcline[0]["start_pose"]
        ex, ey, _ = arcline[-1]["end_pose"]
        b_in = _endpoint_bearing(nusc_map, tok, entry=True)
        b_out = _endpoint_bearing(nusc_map, tok, entry=False)
        endpoints.append((sx, sy, principal_value(b_in + math.pi)))
        endpoints.append((ex, ey, b_out))
        inbound.append(b_in)
        outbound.append(b_out)

    cluster = _cluster_arms(endpoints)
    n_arms = len(set(cluster))

    # Arm bearings: circular mean over the endpoints each arm contains.
    arm_inbound: dict[int, list[float]] = {}
    arm_outbound: dict[int, list[float]] = {}
    for k, tok in enumerate(tokens):
        arm_inbound.setdefault(cluster[2 * k], []).append(inbound[k])
        arm_outbound.setdefault(cluster[2 * k + 1], []).append(outbound[k])

    deltas = []
    for k, tok in enumerate(tokens):
        entry_arm, exit_arm = cluster[2 * k], cluster[2 * k + 1]
        delta = principal_value(
            _circular_mean(arm_outbound[exit_arm])
            - _circular_mean(arm_inbound[entry_arm])
        )
        deltas.append((tok, entry_arm, exit_arm, delta))

    straightest = {}  # entry_arm -> min |delta| among its connectors
    for _, entry_arm, _, delta in deltas:
        straightest[entry_arm] = min(straightest.get(entry_arm, math.inf), abs(delta))

    connectors = {}
    for k, (tok, entry_arm, exit_arm, delta) in enumerate(deltas):
        if n_arms < MIN_ARMS:
            maneuver = "straight"
        elif abs(delta) >= UTURN_MIN_DELTA:
            maneuver = "u_turn"
        elif abs(delta) <= STRAIGHT_MAX_DELTA:
            maneuver = "straight"
        elif (
            abs(delta) <= CONTINUE_MAX_DELTA
            and abs(delta) <= straightest[entry_arm] + 1e-9
        ):
            maneuver = "straight"  # straightest available continuation
        else:
            maneuver = "left" if delta > 0 else "right"
        connectors[tok] = ClassifiedConnector(
            maneuver=maneuver,
            entry_arm=entry_arm,
            exit_arm=exit_arm,
            delta=delta,
            start_yaw=nusc_map.arcline_path_3[tok][0]["start_pose"][2],
        )

    return ClassifiedIntersection(n_arms=n_arms, connectors=connectors)
