# Ingesting a New nuScenes Subset

End-to-end recipe for adding a new nuScenes trainval part (85 scenes) to the existing `scene_data.db` and frontend camera assets.

## Prerequisites

### 1. Download from nuScenes

From https://nuscenes.org/download, for each part `N` you want to add:

- **Keyframe blobs only for part N** (~3.7 GB) — provides `samples/CAM_*` keyframe images.
- **Metadata** — provides `v1.0-trainval/*.json`. May ship bundled with the blob package or as a separate download.
- **Map expansion + CAN bus** — only needed once across all parts (already present, reused via `MAPS_ROOT` in `.env`).

The pipeline only reads camera keyframes under `samples/CAM_*`. Lidar/radar/sweep blobs are **not** required.

### 2. Expected directory layout

After extraction, the subset root should look like:

```
/path/to/v1.0-trainvalNN/
├── samples/        # camera keyframes (lidar/radar dirs may also be present, unused)
├── v1.0-trainval/  # metadata JSONs
├── maps/           # if included
└── can_bus/        # if included
```

## Step 1: Filter scene.json if metadata is full

The standalone metadata package contains all 850 scenes. The pipeline expects `scene.json` to contain only the 85 scenes belonging to the current part. Check:

```bash
python3 -c "import json; print(len(json.load(open('/path/to/v1.0-trainvalNN/v1.0-trainval/scene.json'))))"
```

If you see **850**, filter it down:

```bash
bash scripts/filter_scene_json.sh "$DATAROOT"
```

The script cross-references `sample_data.json` against the keyframe JPGs actually on disk, derives the 85 scene tokens for the part, and rewrites `scene.json` in place. If you see **85**, the metadata is already partitioned — skip this step.

## Step 2: Run the pipeline

```bash
cd /Users/bz/Desktop/repos/nuscenes_explorer/exporter

DATAROOT=/path/to/v1.0-trainvalNN
DB=/Users/bz/Desktop/repos/nuscenes_explorer/exporter/scene_data.db

# Optional but recommended: back up the DB first
cp "$DB" "$DB.bak"

# 1. Register the 85 new scenes in the `scenes` table
python export_scenes.py --db "$DB" --dataroot "$DATAROOT"

# 2. Run the 8-stage exporter pipeline
#    --with-map-features populates lane tokens + lane connectivity (required for lane_change stage)
python build_db.py --db "$DB" --dataroot "$DATAROOT" --with-map-features

# 3. Extract camera keyframe JPGs to the frontend asset dir
python export_camera_images.py --dataroot "$DATAROOT"
```

Stage order inside `build_db.py`: `poses` → `visibility` → `traversals_geom` → `crossings` → `kinematic` → `cutin` → `lane_change` → `traversals`.

## Step 3: Verify

Replace `v1.0-trainvalNN` with the subset name you just ingested:

```bash
DB=/Users/bz/Desktop/repos/nuscenes_explorer/exporter/scene_data.db
SUBSET=v1.0-trainvalNN

# 85 scenes registered
sqlite3 "$DB" "SELECT COUNT(*) FROM scenes WHERE subset='$SUBSET';"

# Foundational tables should each cover all 85 scenes
for tbl in object_poses ego_poses kinematic_features visibility centerlines; do
  cnt=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT t.scene_token) FROM $tbl t JOIN scenes s ON s.scene_token=t.scene_token WHERE s.subset='$SUBSET';")
  printf "  %-25s %s/85\n" "$tbl" "$cnt"
done

# Camera dirs for all part-N scenes
missing=0
for scene in $(sqlite3 "$DB" "SELECT scene_name FROM scenes WHERE subset='$SUBSET';"); do
  [[ -d "/Users/bz/Desktop/repos/nuscenes_explorer/frontend/public/cameras/$scene" ]] || missing=$((missing+1))
done
echo "  camera dirs missing: $missing (expect 0)"
```

Event tables (`cutin_events`, `lane_change_events`, `ped_vehicle_crossings`, `intersection_traversals`) are sparse — coverage varies by subset and is not expected to be 85/85.

## Troubleshooting

### Resuming after partial failure

Stages are independently re-runnable; `poses` automatically skips already-exported scenes. Re-run a single stage with:

```bash
python build_db.py --db "$DB" --dataroot "$DATAROOT" --with-map-features --stage <stage_name>
```

Valid stages: `poses`, `visibility`, `traversals_geom`, `crossings`, `kinematic`, `cutin`, `lane_change`, `traversals`.

### "database is locked" during poses stage

Should no longer occur. Two structural fixes are in place:

1. `scene_token`-leading indexes on `object_poses`, `centerlines`, `lane_change_events` (created upfront in `build_db.py`) — DELETEs are now O(log n) index seeks instead of full table scans.
2. `_compute_scene_data` runs *outside* the writer transaction; `_write_scene_data` runs inside a `BEGIN IMMEDIATE` + 8-attempt retry loop (matching the centerlines path at [export_poses.py:599](exporter/export_poses.py#L599)). The writer lock is now held only during the actual INSERTs, not during ~thousands of `lane_at()` map lookups.

If it ever recurs anyway, fall back to a single worker for poses:

```bash
python build_db.py --db "$DB" --dataroot "$DATAROOT" --with-map-features --stage poses --workers 1
```

### Wrong scene count in scene.json

If `export_scenes.py` registers 850 scenes instead of 85, you skipped Step 1. Restore the DB from the backup and re-run after filtering:

```bash
cp "$DB.bak" "$DB"
bash scripts/filter_scene_json.sh "$DATAROOT"
# then redo Step 2
```
