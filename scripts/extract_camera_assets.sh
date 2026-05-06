#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <path-to-downloaded-tar-assets>" >&2
  exit 1
fi

assets_dir=$1
repo_root=$(cd "$(dirname "$0")/.." && pwd)
target_dir="$repo_root/frontend/public/cameras"

if [[ ! -d "$assets_dir" ]]; then
  echo "Error: assets directory does not exist: $assets_dir" >&2
  exit 1
fi

mkdir -p "$target_dir"

shopt -s nullglob
camera_tars=("$assets_dir"/cameras_val*_part*.tar)
shopt -u nullglob

if [[ ${#camera_tars[@]} -eq 0 ]]; then
  echo "Error: no camera tar files found in $assets_dir matching cameras_val*_part*.tar" >&2
  exit 1
fi

echo "Extracting ${#camera_tars[@]} tar files to $target_dir"
for tar_file in "${camera_tars[@]}"; do
  echo "- $tar_file"
  tar -xf "$tar_file" -C "$target_dir"
done

echo "Done. Camera folders are available under $target_dir"
