"""CCCscp: two through vehicles approaching the same intersection from
perpendicular directions."""

from .request import QueryRequest
from .shared.geometry import perpendicular_approach
from .shared.multi_vehicle import find_multi_vehicle_matches

_THROUGH = {"straight", "curve"}


def run(conn, q: QueryRequest) -> list[dict]:
    return find_multi_vehicle_matches(
        conn,
        _THROUGH,
        _THROUGH,
        perpendicular_approach,
        location=q.location,
        ego_only=q.ego_only,
        non_ego_only=q.non_ego_only,
        visible_cameras=q.visible_cameras,
        hidden_cameras=q.hidden_cameras,
    )
