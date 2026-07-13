# DriverQ Docker Setup

## Step 0: Getting data from GitHub Releases

Download [release assets](https://github.com/bluebarryz/DriverQ/releases) (SQLite db file and camera images) into a local folder.

Expected files:
- `scene_data.db`
- `cameras_val*_part*.tar`

1. From the repo root, create local folders:

```bash
mkdir -p exporter frontend/public/cameras
```

2. Move the SQLite database to the expected path:

```bash
mv /path/to/downloads/scene_data.db exporter/scene_data.db
```

3. Run the extraction script to unpack camera tar assets into `frontend/public/cameras`:

```bash
bash scripts/extract_camera_assets.sh /path/to/downloads
```

After extraction, scene folders should exist under `frontend/public/cameras/scene-*/`.

## Run with Docker

1. Set env vars from the repo root:

```bash
export NUSCENES_DB_HOST_PATH="$(pwd)/exporter/scene_data.db"
export NUSCENES_CAMERAS_HOST_DIR="$(pwd)/frontend/public/cameras"

# Path prefix Apache exposes for the API (see Apache section below)
export NEXT_PUBLIC_API_BASE_URL="/DriverQ"
```

2. Run:

```bash
docker compose up --build
```
