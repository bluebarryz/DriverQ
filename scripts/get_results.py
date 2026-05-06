"""Print detection counts for all preset queries, matching the report's results table."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "server"))

from presets import QueryRequest, run_preset

DB_PATH = Path(__file__).parent / "exporter" / "scene_data.db"

PRESETS = [
    ("Left turns",             QueryRequest(maneuver="left")),
    ("Right turns",            QueryRequest(maneuver="right")),
    ("Curves",                 QueryRequest(maneuver="curve")),
    ("Cut-in",                 QueryRequest(preset="cut_in")),
    ("Lane change",            QueryRequest(preset="lane_change")),
    ("Pedestrian crossing",    QueryRequest(preset="ped_crossing")),
    ("Occluded pedestrian",    QueryRequest(preset="occluded_ped")),
    ("Braking",                QueryRequest(preset="braking")),
    ("CCFtap",                 QueryRequest(preset="CCFtap")),
    ("CCCscp",                 QueryRequest(preset="CCCscp")),
]

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

total_scenes = conn.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
print(f"{'Preset':<25} {'Detections':>12} {'Scenes w/ ≥1 det':>18} (of {total_scenes})")
print("-" * 60)

for label, q in PRESETS:
    results = run_preset(conn, q)
    detections = len(results)
    scenes = len({r["scene_name"] for r in results})
    print(f"{label:<25} {detections:>12} {scenes:>18}")

conn.close()
