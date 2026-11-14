"""CCCscp (Euro NCAP Car-to-Car Crossing straight crossing path): two through
vehicles whose straight paths cross at an intersection.

Roles: both go straight from different road arms, excluding oncoming traffic
on the same road (exit_arm == other's entry_arm in either direction). No
perpendicularity test: the paths crossing in space — established by the
intersection_conflicts exporter — is the scenario, whatever the junction's
skew.
"""

from .request import QueryRequest
from .shared.multi_vehicle import find_conflict_matches


def _roles(x, y, location):
    return (
        x["maneuver"] == "straight"
        and y["maneuver"] == "straight"
        and x["entry_arm"] != y["entry_arm"]
        and x["exit_arm"] != y["entry_arm"]
        and y["exit_arm"] != x["entry_arm"]
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
