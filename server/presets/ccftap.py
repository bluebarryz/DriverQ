"""CCFtap (Euro NCAP Car-to-Car Front turn-across-path): a vehicle turns
across the path of an oncoming through vehicle.

Roles: the turner turns against traffic handedness (left in Boston's
right-hand traffic, right in Singapore's left-hand traffic); the through
vehicle goes straight and its path terminates on the road the turner arrived
from (exit_arm == turner's entry_arm) — that arm relation IS "oncoming", with
no approach-angle heuristics. The paths are guaranteed to cross in space and
near-overlap in time by the intersection_conflicts exporter.
"""

from .request import QueryRequest
from .shared.multi_vehicle import SINGAPORE_LOCATIONS, find_conflict_matches


def _roles(turner, through, location):
    yielding_turn = "right" if location in SINGAPORE_LOCATIONS else "left"
    return (
        turner["maneuver"] == yielding_turn
        and through["maneuver"] == "straight"
        and through["exit_arm"] == turner["entry_arm"]
    )


def run(conn, q: QueryRequest) -> list[dict]:
    return find_conflict_matches(
        conn,
        _roles,
        location=q.location,
        ego_only=q.ego_only,
        non_ego_only=q.non_ego_only,
        visible_cameras=q.visible_cameras,
        hidden_cameras=q.hidden_cameras,
    )
