#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
source_dir="$repo_root/frontend/public/cameras"

scenes=()
while IFS= read -r scene; do
  scenes+=("$(basename "$scene")")
done < <(find "$source_dir" -maxdepth 1 -type d -name 'scene-*' | sort)

batch_size=85
start_batch=1
start_index=$(((start_batch - 1) * batch_size))

if [[ ${#scenes[@]} -le start_index ]]; then
  printf 'No scenes found after batch %d in %s\n' "$((start_batch - 1))" "$source_dir"
  exit 0
fi

for ((i=start_index; i<${#scenes[@]}; i+=batch_size)); do
  batch_num=$((i / batch_size + 1))
  batch_label=$(printf 'val%02d' "$batch_num")

  first_count=43
  second_count=42

  first_output="$repo_root/cameras_${batch_label}_part1.tar"
  second_output="$repo_root/cameras_${batch_label}_part2.tar"

  printf 'Creating %s\n' "$first_output"
  tar -cf "$first_output" -C "$source_dir" "${scenes[@]:i:first_count}"

  printf 'Creating %s\n' "$second_output"
  tar -cf "$second_output" -C "$source_dir" "${scenes[@]:i+first_count:second_count}"
done
