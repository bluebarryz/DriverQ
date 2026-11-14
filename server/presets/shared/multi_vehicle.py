"""Scenario matching over intersection conflict pairs.

The exporter has already established the hard part: intersection_conflicts
holds every vehicle pair whose matched connector centerlines cross while both
traverse the same intersection in near-overlapping windows, with the PET
(post-encroachment time) at the crossing point. A scenario preset is then just
a role predicate over the two traversals' maneuver labels and road-arm ids.
Shared by CCCscp and CCFtap.
"""

from .visibility import (
    any_visibility_in_hidden_cameras,
    appears_in_visible_cameras,
)

SINGAPORE_LOCATIONS = {
    "singapore-hollandvillage",
    "singapore-onenorth",
    "singapore-queenstown",
}


### sql

_PAIRS_SQL = """
    SELECT c.scene_token, s.scene_name, s.location, c.intersection_token,
           c.pet_seconds,
           ta.vehicle_token AS a_vehicle, ta.maneuver AS a_maneuver,
           ta.entry_arm AS a_entry_arm, ta.exit_arm AS a_exit_arm,
           ta.start_frame AS a_start, ta.end_frame AS a_end,
           tb.vehicle_token AS b_vehicle, tb.maneuver AS b_maneuver,
           tb.entry_arm AS b_entry_arm, tb.exit_arm AS b_exit_arm,
           tb.start_frame AS b_start, tb.end_frame AS b_end
    FROM intersection_conflicts c
    JOIN scenes s ON s.scene_token = c.scene_token
    JOIN intersection_traversals ta
      ON ta.scene_token = c.scene_token
     AND ta.intersection_token = c.intersection_token
     AND ta.vehicle_token = c.vehicle_a
    JOIN intersection_traversals tb
      ON tb.scene_token = c.scene_token
     AND tb.intersection_token = c.intersection_token
     AND tb.vehicle_token = c.vehicle_b
    WHERE ta.n_arms >= 3{location_clause}
"""


### entrypoint


def find_conflict_matches(
    conn, roles_match,
    *, location=None, ego_only=False, non_ego_only=False,
    visible_cameras=None, hidden_cameras=None,
):
    """roles_match(first, second, location) decides whether the oriented pair
    fills the scenario's roles; each side is a dict with vehicle, maneuver,
    entry_arm, exit_arm, start, end. Results are sorted by PET (closest
    interactions first, pairs without an observed PET last)."""
    clause, params = "", []
    if location:
        clause = " AND s.location = ?"
        params.append(location)
    rows = conn.execute(_PAIRS_SQL.format(location_clause=clause), params).fetchall()

    results = []
    for r in rows:
        a, b = _side(r, "a"), _side(r, "b")
        for first, second in ((a, b), (b, a)):
            if not roles_match(first, second, r["location"]):
                continue
            tokens = (first["vehicle"], second["vehicle"])
            if ego_only and "ego" not in tokens:
                continue
            if non_ego_only and "ego" in tokens:
                continue
            start = min(first["start"], second["start"])
            end = max(first["end"], second["end"])
            if not _passes_visibility(
                conn, r["scene_token"], tokens, start, end,
                visible_cameras or [], hidden_cameras or [],
            ):
                continue
            results.append(_format_match(r, first, second, start, end))
            break

    results.sort(key=lambda m: (m["pet_seconds"] is None, m["pet_seconds"]))
    return results


### helpers


def _side(r, prefix):
    return {
        "vehicle": r[f"{prefix}_vehicle"],
        "maneuver": r[f"{prefix}_maneuver"],
        "entry_arm": r[f"{prefix}_entry_arm"],
        "exit_arm": r[f"{prefix}_exit_arm"],
        "start": r[f"{prefix}_start"],
        "end": r[f"{prefix}_end"],
    }


def _passes_visibility(conn, scene_token, tokens, start, end,
                       visible_cameras, hidden_cameras):
    # Camera filters apply to the non-ego vehicles in the match: the single
    # non-ego partner with ego_only on, otherwise all non-ego vehicles.
    targets = [t for t in tokens if t != "ego"]
    if visible_cameras and not appears_in_visible_cameras(
        conn, scene_token, targets, visible_cameras, start, end
    ):
        return False
    if hidden_cameras and any_visibility_in_hidden_cameras(
        conn, scene_token, targets, hidden_cameras, start, end
    ):
        return False
    return True


### post-processing


def _format_match(r, first, second, start, end):
    pet = r["pet_seconds"]
    pet_note = f" pet={pet:.1f}s" if pet is not None else ""
    return {
        "scene_name": r["scene_name"],
        "scene_token": r["scene_token"],
        "start_frame": start,
        "end_frame": end,
        "intersection_token": r["intersection_token"],
        "pet_seconds": pet,
        "objects": [
            {"token": m["vehicle"], "category": "vehicle", "label": m["maneuver"]}
            for m in (first, second)
        ],
        "summary": (
            f"{first['vehicle'][:8]}:{first['maneuver']} + "
            f"{second['vehicle'][:8]}:{second['maneuver']} at "
            f"{r['intersection_token'][:8]}{pet_note}"
        ),
    }
