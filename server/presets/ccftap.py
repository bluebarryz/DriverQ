"""CCFtap: through vehicle vs. opposing-direction turner.

Boston (right-hand traffic) yields on left turns; Singapore (left-hand traffic)
on right turns.
"""

from .request import QueryRequest
from .shared.geometry import opposite_approach
from .shared.multi_vehicle import SINGAPORE_LOCATIONS, find_multi_vehicle_matches

_THROUGH = {"straight", "curve"}


def _locations_to_search(requested):
    if requested == "boston-seaport":
        return ["boston-seaport"]
    if requested in SINGAPORE_LOCATIONS:
        return [requested]
    return ["boston-seaport"] + sorted(SINGAPORE_LOCATIONS)


def _yielding_turn(location):
    return {"right"} if location in SINGAPORE_LOCATIONS else {"left"}


def run(conn, q: QueryRequest) -> list[dict]:
    results = []
    for loc in _locations_to_search(q.location):
        results += find_multi_vehicle_matches(
            conn,
            _THROUGH,
            _yielding_turn(loc),
            opposite_approach,
            location=loc,
            ego_only=q.ego_only,
            non_ego_only=q.non_ego_only,
            visible_cameras=q.visible_cameras,
            hidden_cameras=q.hidden_cameras,
        )
    return results
