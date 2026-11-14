#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <path-to-nuscenes-subset>" >&2
  echo "  e.g. $0 /Users/bz/Downloads/v1.0-trainval05" >&2
  exit 1
fi

subset_root=$1

if [[ ! -d "$subset_root/v1.0-trainval" ]]; then
  echo "Error: $subset_root/v1.0-trainval not found" >&2
  exit 1
fi

if [[ ! -d "$subset_root/samples/CAM_FRONT" ]]; then
  echo "Error: $subset_root/samples/CAM_FRONT not found" >&2
  exit 1
fi

python3 - "$subset_root" <<'PY'
import json, os, sys

root = sys.argv[1]
meta = os.path.join(root, "v1.0-trainval")
scene_json = os.path.join(meta, "scene.json")

scenes_all = json.load(open(scene_json))
samples_all = json.load(open(os.path.join(meta, "sample.json")))
sd_all = json.load(open(os.path.join(meta, "sample_data.json")))

sample_to_scene = {s["token"]: s["scene_token"] for s in samples_all}
present = set(os.listdir(os.path.join(root, "samples", "CAM_FRONT")))

present_scene_tokens = {
    sample_to_scene[sd["sample_token"]]
    for sd in sd_all
    if sd.get("filename", "").startswith("samples/CAM_FRONT/")
    and os.path.basename(sd["filename"]) in present
}

kept = sorted(
    (s for s in scenes_all if s["token"] in present_scene_tokens),
    key=lambda s: s["name"],
)

assert kept, "No scenes on disk matched scene.json — wrong path?"

with open(scene_json, "w") as f:
    json.dump(kept, f)

print(f"Filtered scene.json: kept {len(kept)} of {len(scenes_all)} scenes")
print(f"  first: {kept[0]['name']}  last: {kept[-1]['name']}")
print(f"  path:  {scene_json}")
PY
