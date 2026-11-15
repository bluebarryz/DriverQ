# Running DriverQ on a New Machine

Step-by-step setup from a fresh clone. You need two things the repo does not contain:

- **`exporter/scene_data.db`**: the SQLite database (required).
- **`frontend/public/cameras/`**: the camera images, ~29 GB for all 850 scenes. These are optional: without them everything works except the camera panel.

## 0. Prerequisites

- Git
- Python 3.11
- Node.js 20 and npm
- Docker Desktop (only if you want to run with Docker)

## 1. Clone the branch

```bash
git clone -b new-turn-detection git@github.com:bluebarryz/DriverQ.git
cd DriverQ
mkdir -p exporter frontend/public/cameras
```

## 2. Get the database

**Option A (recommended): copy it from a machine that already has it.** The DB on the main laptop has all 10 trainval subsets (850 scenes) and is already on this branch's schema. Stop the API server there first, then copy `exporter/scene_data.db` (~1.5 GB) by AirDrop, USB drive, or:

```bash
rsync -avP <user>@<main-laptop>:~/Desktop/repos/nuscenes_explorer/exporter/scene_data.db exporter/
```

Skip to step 3.

**Option B: use the public release DB and rebuild the intersection tables.** The [v0.9 release](https://github.com/bluebarryz/DriverQ/releases/tag/v0.9) DB covers only trainval01–04 (340 scenes) and uses the old schema, so this branch's server will error on intersection queries until you rebuild.

1. Download `scene_data.db` from the release and move it to `exporter/scene_data.db`.
2. Download the nuScenes **map expansion** pack (from nuscenes.org, account required) so you have `<nuscenes-root>/maps/expansion/*.json`.
3. Set `MAPS_ROOT=<nuscenes-root>` in `.env` (step 4), then:

```bash
python3.11 -m venv .venv-exporter
source .venv-exporter/bin/activate
pip install nuscenes-devkit shapely numpy python-dotenv
cd exporter
python build_db.py --db scene_data.db --stage traversals
python build_db.py --db scene_data.db --stage conflicts
python build_db.py --db scene_data.db --stage crossings
cd ..
deactivate
```

## 3. Get the camera images (optional)

Pick one:

- **Copy the folder** from the main laptop: `frontend/public/cameras/` (all 850 scenes).

  ```bash
  rsync -avP <user>@<main-laptop>:~/Desktop/repos/nuscenes_explorer/frontend/public/cameras/ frontend/public/cameras/
  ```

- **Use release tars:** download `cameras_val*_part*.tar` from the v0.9 release (trainval01–04 only) into a folder, then:

  ```bash
  bash scripts/extract_camera_assets.sh /path/to/downloads
  ```

- **Skip it.** Leave `frontend/public/cameras/` empty. See "Running without camera images" below.

Check: folders should look like `frontend/public/cameras/scene-0001/cameras/0/CAM_FRONT.jpg`.

## 4. Create `.env` in the repo root

`.env` is gitignored, so create it yourself. Use absolute paths and replace `/abs/path/DriverQ` with your clone's location (`pwd` in the repo root):

```bash
DB_PATH=/abs/path/DriverQ/exporter/scene_data.db
CAMERAS_DIR=/abs/path/DriverQ/frontend/public/cameras

# Used by docker compose
NUSCENES_DB_HOST_PATH=/abs/path/DriverQ/exporter/scene_data.db
NUSCENES_CAMERAS_HOST_DIR=/abs/path/DriverQ/frontend/public/cameras

# Only needed to re-run the exporter (step 2, option B)
MAPS_ROOT=/abs/path/to/nuscenes
```

## 5a. Run locally (recommended for development)

Terminal 1 starts the API on http://localhost:8000:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cd server
uvicorn app:app --reload --port 8000
```

Terminal 2 starts the frontend on http://localhost:3000:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. The dev server forwards `/api` and `/cameras` to the API on port 8000.

## 5b. Run with Docker

From the repo root (compose reads `.env` automatically):

```bash
docker compose up --build
```

Open http://localhost:3000. The API is on http://localhost:8000.

## Running without camera images

Everything except the camera panel works: queries, the 3D bird's-eye viewer, trajectories, telemetry charts, and the camera-visibility filters, which come from the DB, not the images. The six camera tiles show broken images.

- **Local:** the API logs `CAMERAS_DIR does not exist ... /cameras static route is disabled` if the folder is missing. That's expected.
- **Docker:** `NUSCENES_CAMERAS_HOST_DIR` must still be set or compose refuses to start. Point it at the empty `frontend/public/cameras` folder.

## Troubleshooting

- **`DB file not found`** at API startup: `DB_PATH` in `.env` is wrong or not absolute.
- **`no such table: intersection_conflicts`** or **`no such column: connector_token`**: the DB is on the old schema. Do step 2, option B, rebuild.
- **Ports in use:** stop whatever is on 3000 or 8000, or change `--port` and `NEXT_PUBLIC_API_BASE_URL`.
