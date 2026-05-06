const DEFAULT_DEV_API_BASE = process.env.NODE_ENV === "development" ? "http://localhost:8000" : "";
const RAW_API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_DEV_API_BASE).replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return RAW_API_BASE ? `${RAW_API_BASE}${path}` : path;
}

export function cameraImageUrl(sceneName: string, frameIdx: number, channel: string): string {
  const scene = encodeURIComponent(sceneName);
  const cam = encodeURIComponent(channel);
  return apiUrl(`/cameras/${scene}/cameras/${frameIdx}/${cam}.jpg`);
}
