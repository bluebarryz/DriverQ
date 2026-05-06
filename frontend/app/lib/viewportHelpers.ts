import * as THREE from "three";
import type { TrajectoryPoint3D } from "../types";

export function annotationColor(cat: string): number {
  if (cat.startsWith("vehicle.car")) return 0x3b82f6;
  if (cat.startsWith("vehicle.truck") || cat.startsWith("vehicle.bus")) return 0xf97316;
  if (cat.startsWith("human.pedestrian")) return 0x22c55e;
  return 0x94a3b8;
}

export function makeTextSprite(text: string, hexColor: number): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 80;
  canvas.height = 28;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const r = (hexColor >> 16) & 0xff;
  const g = (hexColor >> 8) & 0xff;
  const b = hexColor & 0xff;
  ctx.fillStyle = `rgb(${r},${g},${b})`;
  ctx.font = "bold 15px monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, 40, 14);
  const tex = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: true });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(5, 1.75, 1);
  return sprite;
}

export function setTrajectoryLine(
  line: THREE.Line | null,
  points: TrajectoryPoint3D[],
  color: number,
) {
  if (!line) return;
  (line.material as THREE.LineBasicMaterial).color.setHex(color);
  if (points.length < 2) {
    line.visible = false;
    return;
  }
  const positions: number[] = [];
  for (const p of points) positions.push(p.x, p.y, p.z + 0.2);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const oldGeo = line.geometry;
  line.geometry = geo;
  oldGeo.dispose();
  line.visible = true;
}
