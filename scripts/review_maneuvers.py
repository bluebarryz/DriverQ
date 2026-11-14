#!/usr/bin/env python3
"""Interactive review tool for intersection maneuver classifications.

Samples labelled traversals from the DB, then serves a local page that draws
the intersection, its labelled lane connectors, and the animated vehicle so
each classification can be judged correct/incorrect. Verdicts are appended to
feedback.txt in the repo root via the Submit button.

Usage:
    python scripts/review_maneuvers.py [--db exporter/scene_data.db] [--port 8123]
Then open http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "exporter"))
sys.path.insert(0, os.path.join(REPO_ROOT, "server"))

from dotenv import load_dotenv
from shapely.geometry import LineString
from nuscenes.map_expansion.map_api import NuScenesMap

from maneuver_utils.helpers import connector_centerline
from maneuver_utils.intersections import classify_intersection
from presets.ccftap import _roles as ccftap_roles
from presets.cccscp import _roles as cccscp_roles

load_dotenv(os.path.join(REPO_ROOT, ".env"))

FEEDBACK_PATH = os.path.join(REPO_ROOT, "feedback.txt")
OLD_FEEDBACK_PATH = os.path.join(REPO_ROOT, "old_feedback.txt")  # old-only tab verdicts
HTML_PATH = os.path.join(REPO_ROOT, "scripts", "review_maneuvers.html")
EGO_DIMS = (4.08, 1.73)  # nuScenes ego vehicle length, width
FRAME_BUFFER = 5
LANE_CONTEXT_RADIUS = 60.0

# samples per stratum: (label, extra where-clause, count)
STRATA = [
    ("left", "", 20),
    ("right", "", 20),
    ("straight", "AND n_arms >= 3", 12),
    ("straight", "AND n_arms < 3", 8),
    ("unknown", "", 10),
    ("u_turn", "", 10),
]

# hand-picked traversals appended after the sampled set: (scene_name, vehicle_token, intersection_token)
PINNED = [
    ("scene-0151", "e99f102efe604ec0b52e02b61e7b3ea7", "4c024e78-14ef-4636-a91c-754f9051199a"),
]


def _resolve_maps_dataroot(raw: str) -> str:
    root = os.path.abspath(raw)
    if os.path.isdir(os.path.join(root, "maps", "expansion")):
        return root
    if os.path.isdir(os.path.join(root, "expansion")):
        return os.path.dirname(root)
    return root


def _yaw_from_quat(qw: float, qx: float, qy: float, qz: float) -> float:
    return math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))


def _rounded(points) -> list[list[float]]:
    return [[round(float(x), 2), round(float(y), 2)] for x, y in points]


def _feedback_key(fields: dict) -> tuple[str, str, str]:
    return (fields["scene"], fields["vehicle"], fields["intersection"])


def _parse_feedback_line(line: str) -> dict | None:
    """Parse `key=value ...` tokens after the leading timestamp; None if malformed."""
    fields = dict(
        tok.split("=", 1) for tok in line.split() if "=" in tok and not tok.endswith("=")
    )
    if {"scene", "vehicle", "intersection", "verdict"} <= fields.keys():
        return fields
    return None


def read_submitted() -> list[dict]:
    """Latest verdict per (scene, vehicle, intersection), across both feedback files."""
    latest: dict[tuple, dict] = {}
    for path in (FEEDBACK_PATH, OLD_FEEDBACK_PATH):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                fields = _parse_feedback_line(line)
                if fields:
                    latest[_feedback_key(fields)] = fields
    return [
        {
            "scene_name": f["scene"],
            "vehicle_token": f["vehicle"],
            "intersection_token": f["intersection"],
            "verdict": f["verdict"],
            "correct_label": f.get("correct"),
        }
        for f in latest.values()
    ]


def _load_frames(conn, scene_token: str, vehicle_token: str, lo: int, hi: int):
    if vehicle_token == "ego":
        rows = conn.execute(
            """SELECT frame_idx, ego_x AS x, ego_y AS y,
                      ego_qw AS qw, ego_qx AS qx, ego_qy AS qy, ego_qz AS qz
               FROM ego_poses WHERE scene_token = ? AND frame_idx BETWEEN ? AND ?
               ORDER BY frame_idx""",
            (scene_token, lo, hi),
        ).fetchall()
        length, width = EGO_DIMS
    else:
        rows = conn.execute(
            """SELECT frame_idx, x, y, qw, qx, qy, qz, length, width
               FROM object_poses
               WHERE scene_token = ? AND instance_token = ? AND frame_idx BETWEEN ? AND ?
               ORDER BY frame_idx""",
            (scene_token, vehicle_token, lo, hi),
        ).fetchall()
        length = rows[0]["length"] if rows else EGO_DIMS[0]
        width = rows[0]["width"] if rows else EGO_DIMS[1]
    frames = [
        {
            "f": r["frame_idx"],
            "x": round(r["x"], 2),
            "y": round(r["y"], 2),
            "yaw": round(_yaw_from_quat(r["qw"], r["qx"], r["qy"], r["qz"]), 4),
        }
        for r in rows
    ]
    return frames, round(length, 2), round(width, 2)


class GeomCache:
    """Map, intersection-geometry, and context-lane caches shared by the
    maneuver and scenario example builders."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.maps: dict[str, NuScenesMap] = {}
        self.inter: dict[tuple[str, str], dict] = {}
        self.lanes: dict[str, list] = {}

    def get_map(self, loc: str) -> NuScenesMap:
        if loc not in self.maps:
            print(f"loading map: {loc}")
            self.maps[loc] = NuScenesMap(
                dataroot=_resolve_maps_dataroot(os.environ["MAPS_ROOT"]), map_name=loc
            )
        return self.maps[loc]

    def intersection(self, loc: str, token: str) -> dict:
        key = (loc, token)
        if key not in self.inter:
            m = self.get_map(loc)
            info = classify_intersection(m, token)
            rec = m.get("road_segment", token)
            poly = m.extract_polygon(rec["polygon_token"])
            if not poly.is_valid:
                poly = poly.buffer(0)
            self.inter[key] = {
                "polygon": _rounded(poly.exterior.coords),
                "centroid": (poly.centroid.x, poly.centroid.y),
                "connectors": {
                    tok: {"pts": _rounded(connector_centerline(m, tok)), "label": c.maneuver}
                    for tok, c in info.connectors.items()
                },
            }
        return self.inter[key]

    def context_lanes(self, scene_token: str, centroid: tuple[float, float]) -> list:
        if scene_token not in self.lanes:
            by_lane: dict[str, list] = {}
            for cr in self.conn.execute(
                "SELECT lane_token, x, y FROM centerlines WHERE scene_token = ? ORDER BY lane_token, point_idx",
                (scene_token,),
            ):
                by_lane.setdefault(cr["lane_token"], []).append((cr["x"], cr["y"]))
            self.lanes[scene_token] = list(by_lane.values())
        cx, cy = centroid
        return [
            _rounded(pts)
            for pts in self.lanes[scene_token]
            if any(math.hypot(x - cx, y - cy) < LANE_CONTEXT_RADIUS for x, y in pts)
        ]


def build_examples(conn: sqlite3.Connection, geom: GeomCache) -> list[dict]:
    rng = random.Random(42)

    rows = []
    for label, extra, count in STRATA:
        candidates = conn.execute(
            f"""SELECT it.*, s.scene_name, s.location
                FROM intersection_traversals it
                JOIN scenes s ON s.scene_token = it.scene_token
                WHERE it.maneuver = ? {extra}""",
            (label,),
        ).fetchall()
        rows.extend(rng.sample(candidates, min(count, len(candidates))))
    rng.shuffle(rows)

    sampled = {(r["scene_name"], r["vehicle_token"], r["intersection_token"]) for r in rows}
    for scene_name, veh, inter in PINNED:
        if (scene_name, veh, inter) in sampled:
            continue
        row = conn.execute(
            """SELECT it.*, s.scene_name, s.location
               FROM intersection_traversals it
               JOIN scenes s ON s.scene_token = it.scene_token
               WHERE s.scene_name = ? AND it.vehicle_token = ? AND it.intersection_token = ?""",
            (scene_name, veh, inter),
        ).fetchone()
        assert row, f"pinned traversal not found: {scene_name} {veh} {inter}"
        rows.append(row)

    examples = []
    for r in rows:
        loc = r["location"]
        inter = geom.intersection(loc, r["intersection_token"])
        lanes = geom.context_lanes(r["scene_token"], inter["centroid"])

        frames, length, width = _load_frames(
            conn,
            r["scene_token"],
            r["vehicle_token"],
            r["start_frame"] - FRAME_BUFFER,
            r["end_frame"] + FRAME_BUFFER,
        )
        if len(frames) < 2:
            continue

        examples.append(
            {
                "scene_name": r["scene_name"],
                "location": loc,
                "vehicle_token": r["vehicle_token"],
                "intersection_token": r["intersection_token"],
                "maneuver": r["maneuver"],
                "connector_label": inter["connectors"][r["connector_token"]]["label"],
                "match_score": round(r["match_score"], 2),
                "class_margin": round(r["class_margin"], 2) if r["class_margin"] is not None else None,
                "observed_fraction": round(r["observed_fraction"], 2),
                "n_arms": r["n_arms"],
                "start_frame": r["start_frame"],
                "end_frame": r["end_frame"],
                "polygon": inter["polygon"],
                "connectors": [
                    {"pts": c["pts"], "label": c["label"], "matched": tok == r["connector_token"]}
                    for tok, c in inter["connectors"].items()
                ],
                "lanes": lanes,
                "frames": frames,
                "veh": {"l": length, "w": width},
            }
        )

    print(f"built {len(examples)} maneuver examples")
    return examples


_SCENARIO_PAIRS_SQL = """
    SELECT c.scene_token, s.scene_name, s.location, c.intersection_token,
           c.conflict_x, c.conflict_y, c.pet_seconds,
           c.a_arrival_frame, c.b_arrival_frame,
           ta.vehicle_token AS a_vehicle, ta.maneuver AS a_maneuver,
           ta.entry_arm AS a_entry_arm, ta.exit_arm AS a_exit_arm,
           ta.connector_token AS a_connector,
           ta.start_frame AS a_start, ta.end_frame AS a_end,
           tb.vehicle_token AS b_vehicle, tb.maneuver AS b_maneuver,
           tb.entry_arm AS b_entry_arm, tb.exit_arm AS b_exit_arm,
           tb.connector_token AS b_connector,
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
    WHERE ta.n_arms >= 3
"""


def _pair_example(
    pose_conn: sqlite3.Connection, geom: GeomCache, *, kind: str, scene_name: str,
    scene_token: str, location: str, intersection_token: str,
    vehicles_spec: list[dict], conflict, pet, note: str | None,
) -> dict | None:
    """Assemble one scenario-shaped example (used by scenarios + diff tabs).
    vehicles_spec: [{token, connector, maneuver, role, start, end, arrival}]."""
    inter = geom.intersection(location, intersection_token)
    starts = [v["start"] for v in vehicles_spec]
    ends = [v["end"] for v in vehicles_spec]
    lo, hi = min(starts) - FRAME_BUFFER, max(ends) + FRAME_BUFFER
    vehicles = []
    for v in vehicles_spec:
        frames, length, width = _load_frames(pose_conn, scene_token, v["token"], lo, hi)
        if len(frames) < 2:
            return None
        vehicles.append(
            {
                "token": v["token"], "maneuver": v["maneuver"], "role": v["role"],
                "connector_token": v["connector"], "start": v["start"], "end": v["end"],
                "arrival": round(v["arrival"], 2) if v.get("arrival") is not None else None,
                "frames": frames, "veh": {"l": length, "w": width},
            }
        )
    return {
        "kind": kind,
        "scene_name": scene_name,
        "location": location,
        "intersection_token": intersection_token,
        "pet": round(pet, 2) if pet is not None else None,
        "conflict": [round(conflict[0], 2), round(conflict[1], 2)] if conflict else None,
        "note": note,
        "start_frame": min(starts),
        "end_frame": max(ends),
        "polygon": inter["polygon"],
        "connectors": [
            {"token": tok, "pts": c["pts"], "label": c["label"]}
            for tok, c in inter["connectors"].items()
        ],
        "lanes": geom.context_lanes(scene_token, inter["centroid"]),
        "vehicles": vehicles,
    }


def build_scenarios(conn: sqlite3.Connection, geom: GeomCache) -> list[dict]:
    """All CCFtap/CCCscp pairs, classified by the actual preset role predicates."""

    def side(r, p, arrival_col):
        return {
            "vehicle": r[f"{p}_vehicle"], "maneuver": r[f"{p}_maneuver"],
            "entry_arm": r[f"{p}_entry_arm"], "exit_arm": r[f"{p}_exit_arm"],
            "connector": r[f"{p}_connector"], "start": r[f"{p}_start"],
            "end": r[f"{p}_end"], "arrival": r[arrival_col],
        }

    out = []
    for r in conn.execute(_SCENARIO_PAIRS_SQL).fetchall():
        a = side(r, "a", "a_arrival_frame")
        b = side(r, "b", "b_arrival_frame")
        kind = None
        for x, y in ((a, b), (b, a)):
            if ccftap_roles(x, y, r["location"]):
                kind, first, second = "CCFtap", x, y  # turner first
                break
        if kind is None and cccscp_roles(a, b, r["location"]):
            kind, first, second = "CCCscp", a, b
        if kind is None:
            continue

        roles = ("turner", "through") if kind == "CCFtap" else ("straight", "straight")
        specs = [{**v, "token": v["vehicle"], "role": role}
                 for v, role in zip((first, second), roles)]
        ex = _pair_example(
            conn, geom, kind=kind, scene_name=r["scene_name"], scene_token=r["scene_token"],
            location=r["location"], intersection_token=r["intersection_token"],
            vehicles_spec=specs, conflict=(r["conflict_x"], r["conflict_y"]),
            pet=r["pet_seconds"], note=None,
        )
        if ex is not None:
            out.append(ex)

    out.sort(key=lambda e: (e["scene_name"], e["kind"], e["pet"] is None, e["pet"]))
    print(f"built {len(out)} scenario examples")
    return out


# --- Old-version CCFtap matcher (git 5a8e3c7 logic, inlined) --------------
# Reproduces the pre-change opposite-approach geometry against the old DB so
# the diff tabs can show exactly which pairs the two versions disagree on.

_OLD_SINGAPORE = {
    "singapore-hollandvillage", "singapore-onenorth", "singapore-queenstown"
}


def _principal(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def _old_opposite(ya: float, yb: float) -> bool:
    d = abs(_principal(yb - ya))
    return math.pi - math.radians(30) <= d <= math.pi


def _old_effective_yaw(connector_yaw, vehicle_yaw):
    if connector_yaw is None:
        return vehicle_yaw
    if vehicle_yaw is None:
        return connector_yaw
    if abs(_principal(vehicle_yaw - connector_yaw)) > math.pi / 2:
        return vehicle_yaw
    return connector_yaw


def old_ccftap_matches(old_conn: sqlite3.Connection) -> list[dict]:
    """Turner+through pairs the old presets flagged as CCFtap, from the old DB."""
    rows = old_conn.execute(
        """SELECT it.scene_token, s.scene_name, s.location, it.intersection_token,
                  it.vehicle_token, it.maneuver, it.start_frame, it.end_frame,
                  it.intersection_approach_heading AS approach,
                  itgd.connector_token AS connector,
                  itgd.connector_1_start_yaw AS cyaw
           FROM intersection_traversals it
           JOIN scenes s ON s.scene_token = it.scene_token
           LEFT JOIN intersection_traversals_geometric_data itgd
             ON itgd.scene_token = it.scene_token
            AND itgd.vehicle_token = it.vehicle_token
            AND itgd.intersection_token = it.intersection_token
           WHERE it.maneuver IN ('straight', 'curve', 'left', 'right')"""
    ).fetchall()

    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["scene_token"], r["intersection_token"]), []).append(r)

    through = {"straight", "curve"}
    matches = []
    for grp in groups.values():
        loc = grp[0]["location"]
        turn = "right" if loc in _OLD_SINGAPORE else "left"
        for a in grp:
            if a["maneuver"] != turn:
                continue
            for b in grp:
                if b["vehicle_token"] == a["vehicle_token"] or b["maneuver"] not in through:
                    continue
                ya = _old_effective_yaw(a["cyaw"], a["approach"])
                yb = _old_effective_yaw(b["cyaw"], b["approach"])
                if ya is None or yb is None or not _old_opposite(ya, yb):
                    continue
                matches.append({"turner": a, "through": b})
    return matches


def _connectors_cross(geom: GeomCache, location: str, tok_a, tok_b):
    if not tok_a or not tok_b:
        return None
    m = geom.get_map(location)
    try:
        cl_a = connector_centerline(m, tok_a)
        cl_b = connector_centerline(m, tok_b)
    except AssertionError:
        return None
    inter = LineString(cl_a).intersection(LineString(cl_b))
    if inter.is_empty:
        return None
    pts = []
    for g in getattr(inter, "geoms", [inter]):
        pts.extend(g.coords)
    return (pts[0][0], pts[0][1]) if pts else None


def build_diff(
    new_conn: sqlite3.Connection, old_conn: sqlite3.Connection,
    geom: GeomCache, scenarios: list[dict],
) -> tuple[list[dict], list[dict]]:
    """CCFtap pairs the two versions disagree on, over the common scene set."""
    from presets.ccftap import run as new_ccftap_run
    from presets.request import QueryRequest

    new_scenes = {m["scene_name"] for m in new_ccftap_run(new_conn, QueryRequest())}
    old_matches = old_ccftap_matches(old_conn)
    old_scenes = {m["turner"]["scene_name"] for m in old_matches}
    common = (
        {r[0] for r in old_conn.execute("SELECT scene_name FROM scenes")}
        & {r[0] for r in new_conn.execute("SELECT scene_name FROM scenes")}
    )
    only_old_scenes = (old_scenes & common) - new_scenes
    only_new_scenes = (new_scenes & common) - old_scenes

    old_only = []
    for m in old_matches:
        t, th = m["turner"], m["through"]
        if t["scene_name"] not in only_old_scenes:
            continue
        conflict = _connectors_cross(
            geom, t["location"], t["connector"], th["connector"]
        )
        specs = [
            {"token": t["vehicle_token"], "connector": t["connector"],
             "maneuver": t["maneuver"], "role": "turner",
             "start": t["start_frame"], "end": t["end_frame"], "arrival": None},
            {"token": th["vehicle_token"], "connector": th["connector"],
             "maneuver": th["maneuver"], "role": "through",
             "start": th["start_frame"], "end": th["end_frame"], "arrival": None},
        ]
        ex = _pair_example(
            new_conn, geom, kind="CCFtap", scene_name=t["scene_name"],
            scene_token=t["scene_token"], location=t["location"],
            intersection_token=t["intersection_token"], vehicles_spec=specs,
            conflict=conflict, pet=None,
            note="Old flagged this; new version rejects it.",
        )
        if ex is not None:
            old_only.append(ex)
    old_only.sort(key=lambda e: e["scene_name"])

    new_only = []
    for e in scenarios:
        if e["kind"] == "CCFtap" and e["scene_name"] in only_new_scenes:
            e = dict(e)
            e["note"] = "New version flags this; old version missed it."
            new_only.append(e)

    print(f"built diff: {len(old_only)} old-only, {len(new_only)} new-only CCFtap")
    return old_only, new_only


class Handler(BaseHTTPRequestHandler):
    diff_old_json: bytes = b"[]"
    diff_new_json: bytes = b"[]"
    examples_json: bytes = b"[]"
    scenarios_json: bytes = b"[]"
    write_lock = threading.Lock()

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            with open(HTML_PATH, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/data":
            self._send(200, self.examples_json, "application/json")
        elif self.path == "/scenarios":
            self._send(200, self.scenarios_json, "application/json")
        elif self.path == "/diff-old":
            self._send(200, self.diff_old_json, "application/json")
        elif self.path == "/diff-new":
            self._send(200, self.diff_new_json, "application/json")
        elif self.path == "/submitted":
            self._send(200, json.dumps(read_submitted()).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/feedback":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        verdicts = json.loads(self.rfile.read(length))
        stamp = datetime.now().isoformat(timespec="seconds")

        # Old-only tab verdicts go to a separate file.
        by_path: dict[str, list] = {FEEDBACK_PATH: [], OLD_FEEDBACK_PATH: []}
        for v in verdicts:
            by_path[OLD_FEEDBACK_PATH if v.get("file") == "old" else FEEDBACK_PATH].append(v)

        with self.write_lock:
            for path, group in by_path.items():
                if group:
                    self._rewrite(path, group, stamp)

        self._send(200, json.dumps({"written": len(verdicts)}).encode(), "application/json")

    @staticmethod
    def _rewrite(path: str, verdicts: list, stamp: str) -> None:
        incoming = {
            (v["scene_name"], v["vehicle_token"], v["intersection_token"]) for v in verdicts
        }
        # Drop any prior line for an incoming key so edits replace, not duplicate.
        kept: list[str] = []
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    fields = _parse_feedback_line(line)
                    if fields and _feedback_key(fields) in incoming:
                        continue
                    kept.append(line.rstrip("\n"))
        for v in verdicts:
            line = (
                f"{stamp} scene={v['scene_name']} vehicle={v['vehicle_token']} "
                f"intersection={v['intersection_token']} predicted={v['maneuver']} "
                f"verdict={v['verdict']}"
            )
            if v.get("correct_label"):
                line += f" correct={v['correct_label']}"
            kept.append(line)
        with open(path, "w") as f:
            f.write("\n".join(kept) + "\n")

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the maneuver review page")
    parser.add_argument("--db", default=os.path.join(REPO_ROOT, "exporter", "scene_data.db"))
    parser.add_argument(
        "--old-db", default=os.path.join(REPO_ROOT, "scene_data(1).db"),
        help="pre-change DB, for the CCFtap diff tabs",
    )
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    geom = GeomCache(conn)
    Handler.examples_json = json.dumps(build_examples(conn, geom)).encode()
    scenarios = build_scenarios(conn, geom)
    Handler.scenarios_json = json.dumps(scenarios).encode()

    if os.path.exists(args.old_db):
        old_conn = sqlite3.connect(args.old_db)
        old_conn.row_factory = sqlite3.Row
        old_only, new_only = build_diff(conn, old_conn, geom, scenarios)
        old_conn.close()
        Handler.diff_old_json = json.dumps(old_only).encode()
        Handler.diff_new_json = json.dumps(new_only).encode()
    else:
        print(f"[diff tabs disabled] old DB not found: {args.old_db}")
    conn.close()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"review page ready: http://127.0.0.1:{args.port}  (feedback -> {FEEDBACK_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
