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
```

2. Run:

```bash
docker compose up --build
```

Once running, open the app at **http://localhost:3000/DriverQ/** (not `/`) —
`BASE_PATH` defaults to `/DriverQ`, so Next.js serves the frontend under that
prefix rather than the bare root.

> **Note:** `docker compose up` alone is not enough to fully exercise the app.
> The frontend calls the API at `NEXT_PUBLIC_API_BASE_URL` (`/DriverQServer` by
> default), but nothing in `docker-compose.yml` routes that path to the `api`
> container — that routing is Apache's job in production (see
> `apache-setup.md`). Without a reverse proxy in front, requests to
> `/DriverQServer/api/...` will hit the Next.js container directly and 404.
> To test the full stack locally without Apache, either:
> - set up a local reverse proxy (e.g. Apache, nginx) that mirrors the
>   `apache-setup.md` routing, or
> - run the frontend with `npm run dev` (not via Docker) from `frontend/`,
>   which proxies `/api/*` and `/cameras/*` to `http://localhost:8000` for you
>   (see `next.config.ts`), alongside `docker compose up api` for just the
>   backend.

The `NEXT_PUBLIC_API_BASE_URL` defaults to `/DriverQServer` and `BASE_PATH` defaults to
`/DriverQ` (matching the Apache path prefixes in `apache-setup.md`). Explicit
prefixes are used for both the API and the frontend rather than a catch-all `/`, so the
app does not interfere with other sites hosted on the same Apache instance. To override
either, set the env vars before running compose:

```bash
export NEXT_PUBLIC_API_BASE_URL="/my-api-path"
export BASE_PATH="/my-frontend-path"
docker compose up --build
```

If you override `BASE_PATH`, open the app at `http://localhost:3000<BASE_PATH>/` instead.
