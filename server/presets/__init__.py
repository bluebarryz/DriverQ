"""Preset query handlers for /api/query."""

from .request import QueryRequest
from . import (
    braking,
    cccscp,
    ccftap,
    cut_in,
    lane_change,
    maneuver,
    occluded_ped,
    ped_crossing,
    pedestrian_scenes,
)

PRESET_HANDLERS = {
    "pedestrian_scenes": pedestrian_scenes.run,
    "braking": braking.run,
    "cut_in": cut_in.run,
    "lane_change": lane_change.run,
    "CCCscp": cccscp.run,
    "CCFtap": ccftap.run,
    "ped_crossing": ped_crossing.run,
    "occluded_ped": occluded_ped.run,
}


def run_preset(conn, q: QueryRequest) -> list[dict]:
    handler = PRESET_HANDLERS.get(q.preset)
    if handler is not None:
        return handler(conn, q)
    if q.maneuver:
        return maneuver.run(conn, q)
    return []


__all__ = ["QueryRequest", "run_preset", "PRESET_HANDLERS"]
