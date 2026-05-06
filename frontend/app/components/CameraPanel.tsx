"use client";

import { useState } from "react";

import { nuscenesVisibilityBadge } from "@/app/lib/nuscenesVisibility";
import { cameraImageUrl } from "@/app/lib/api";

export interface VisibilityEntry {
  token: string;
  cat: string;
  visibility_level: string;
  bbox: [number, number, number, number];
}

export type FrameVisibility = Record<string, VisibilityEntry[]>;

const CAMERA_ORDER = [
  "CAM_FRONT_LEFT",
  "CAM_FRONT",
  "CAM_FRONT_RIGHT",
  "CAM_BACK_LEFT",
  "CAM_BACK",
  "CAM_BACK_RIGHT",
];

function catShort(cat: string): string {
  return cat.replace("vehicle.", "").replace("human.pedestrian", "ped").slice(0, 6);
}

function tokenMatches(vehicleToken: string, prefix: string): boolean {
  return prefix !== "" && (vehicleToken.startsWith(prefix) || prefix.startsWith(vehicleToken));
}

function getHighlightColors(
  vehicles: VisibilityEntry[],
  highlightTokens: Record<string, string>,
): string[] {
  const colors: string[] = [];
  for (const [tokenPrefix, color] of Object.entries(highlightTokens)) {
    if (tokenPrefix && vehicles.some((v) => tokenMatches(v.token, tokenPrefix))) {
      colors.push(color);
    }
  }
  return colors;
}

function highlightStyle(colors: string[]): React.CSSProperties {
  if (colors.length === 0) return {};
  if (colors.length === 1) return { boxShadow: `inset 0 0 0 2px ${colors[0]}` };
  return { boxShadow: `inset 0 0 0 2px ${colors[0]}, inset 0 0 0 5px ${colors[1]}` };
}

function highlightColorForToken(
  token: string,
  highlightTokens: Record<string, string>,
): string | null {
  for (const [tokenPrefix, color] of Object.entries(highlightTokens)) {
    if (tokenPrefix && tokenMatches(token, tokenPrefix)) return color;
  }
  return null;
}

function getVisibleVehicles(
  vehicles: VisibilityEntry[],
  showAllBboxes: boolean,
  highlightTokens: Record<string, string>,
): VisibilityEntry[] {
  if (showAllBboxes) return vehicles;
  return vehicles.filter((v) => highlightColorForToken(v.token, highlightTokens) !== null);
}

function CameraImageOverlay({
  imagePath,
  channel,
  vehicles,
  showAllBboxes,
  highlightTokens,
}: {
  imagePath: string;
  channel: string;
  vehicles: VisibilityEntry[];
  showAllBboxes: boolean;
  highlightTokens: Record<string, string>;
}) {
  const visibleVehicles = getVisibleVehicles(vehicles, showAllBboxes, highlightTokens);

  return (
    <div className="relative w-full h-full">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={imagePath} alt={channel} className="absolute inset-0 w-full h-full object-contain block" loading="lazy" />
      <svg
        className="absolute inset-0 w-full h-full pointer-events-none"
        viewBox="0 0 1600 900"
        preserveAspectRatio="xMidYMid meet"
      >
        {visibleVehicles
          .slice()
          .sort((a, b) => {
            const ah = highlightColorForToken(a.token, highlightTokens) ? 1 : 0;
            const bh = highlightColorForToken(b.token, highlightTokens) ? 1 : 0;
            return ah - bh;
          })
          .map((v) => {
            const [x1, y1, x2, y2] = v.bbox;
            const w = x2 - x1;
            const h = y2 - y1;
            if (!(w > 0 && h > 0)) return null;

            const hlColor = highlightColorForToken(v.token, highlightTokens);
            return (
              <g key={`${v.token}:${x1}:${y1}:${x2}:${y2}`}>
                <rect
                  x={x1}
                  y={y1}
                  width={w}
                  height={h}
                  fill="none"
                  stroke={hlColor ?? "rgba(255,255,255,0.5)"}
                  strokeWidth={hlColor ? 4 : 1.5}
                  vectorEffect="non-scaling-stroke"
                />
                <text
                  x={x1}
                  y={Math.max(12, y1 - 4)}
                  fill={hlColor ?? "rgba(255,255,255,0.9)"}
                  fontSize={36}
                  fontFamily="monospace"
                  stroke="rgba(0,0,0,0.75)"
                  strokeWidth={2}
                  paintOrder="stroke"
                >
                  {v.token.slice(0, 5)}
                </text>
              </g>
            );
          })}
      </svg>
    </div>
  );
}

function CameraThumb({
  channel,
  imagePath,
  vehicles,
  showAllBboxes,
  highlightTokens,
  onSelect,
  onVehicleClick,
}: {
  channel: string;
  imagePath: string;
  vehicles: VisibilityEntry[];
  showAllBboxes: boolean;
  highlightTokens: Record<string, string>;
  onSelect: () => void;
  onVehicleClick: (token: string) => void;
}) {
  const colors = getHighlightColors(vehicles, highlightTokens);
  const visibleVehicles = getVisibleVehicles(vehicles, showAllBboxes, highlightTokens);

  return (
    <div
      className="flex flex-col min-h-0 h-full bg-[#0f172a] rounded overflow-hidden"
      style={{ border: "1px solid #1e293b", ...highlightStyle(colors) }}
    >
      <div className="shrink-0 text-[10px] text-gray-400 px-2 py-0.5 font-mono flex justify-between items-center">
        <span>{channel}</span>
        <button className="text-gray-600 hover:text-gray-300" onClick={onSelect} title="Enlarge">⛶</button>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden cursor-pointer hover:opacity-90" onClick={onSelect}>
        <CameraImageOverlay
          imagePath={imagePath}
          channel={channel}
          vehicles={vehicles}
          showAllBboxes={showAllBboxes}
          highlightTokens={highlightTokens}
        />
      </div>
      {visibleVehicles.length > 0 && (
        <div className="shrink-0 flex flex-wrap gap-1 px-1 py-1 bg-[#0a0f1a]">
          {visibleVehicles.slice(0, 6).map((v) => {
            const lbl = nuscenesVisibilityBadge(v.visibility_level);
            return (
              <button
                key={v.token}
                onClick={() => onVehicleClick(v.token)}
                className="flex items-center gap-0.5 rounded px-1 text-[9px] font-mono hover:opacity-80"
                style={{ background: lbl.bg, color: lbl.fg }}
                title={`${v.cat} - visibility ${v.visibility_level}`}
              >
                <span className="opacity-60">{catShort(v.cat)}</span>
                <span>{lbl.text}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CameraFull({
  channel,
  imagePath,
  vehicles,
  showAllBboxes,
  highlightTokens,
  onClose,
  onVehicleClick,
}: {
  channel: string;
  imagePath: string;
  vehicles: VisibilityEntry[];
  showAllBboxes: boolean;
  highlightTokens: Record<string, string>;
  onClose: () => void;
  onVehicleClick: (token: string) => void;
}) {
  const visibleVehicles = getVisibleVehicles(vehicles, showAllBboxes, highlightTokens);

  return (
    <div className="flex flex-col h-full bg-[#0a0a0a]">
      <div className="flex items-center gap-2 px-3 py-1 bg-[#111] border-b border-[#333] shrink-0">
        <span className="text-sm font-mono text-gray-300">{channel}</span>
        <button onClick={onClose} className="ml-auto text-gray-500 hover:text-white text-xs px-2 py-0.5 rounded hover:bg-[#333]">
          close
        </button>
      </div>
      <div className="flex-1 flex gap-2 p-2 min-h-0 overflow-hidden">
        <div className="flex-1 min-w-0">
          <CameraImageOverlay
            imagePath={imagePath}
            channel={channel}
            vehicles={vehicles}
            showAllBboxes={showAllBboxes}
            highlightTokens={highlightTokens}
          />
        </div>
        {visibleVehicles.length > 0 && (
          <div className="w-36 shrink-0 flex flex-col gap-1 overflow-y-auto">
            <div className="text-[10px] text-gray-500 font-mono px-1">Objects</div>
            {visibleVehicles.map((v) => {
              const lbl = nuscenesVisibilityBadge(v.visibility_level);
              return (
                <button
                  key={v.token}
                  onClick={() => onVehicleClick(v.token)}
                  className="text-left rounded p-1.5 hover:opacity-80"
                  style={{ background: lbl.bg }}
                >
                  <div className="text-[10px] font-mono truncate" style={{ color: lbl.fg }}>{v.token.slice(0, 8)}</div>
                  <div className="text-[9px] opacity-60" style={{ color: lbl.fg }}>{catShort(v.cat)}</div>
                  <div className="text-[11px] font-bold" style={{ color: lbl.fg }}>{lbl.text}</div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export function CameraPanel({
  sceneName,
  frameIdx,
  visibility,
  showAllBboxes,
  highlightTokens,
  onVehicleClick,
}: {
  sceneName: string;
  frameIdx: number;
  visibility: FrameVisibility | null;
  showAllBboxes: boolean;
  highlightTokens: Record<string, string>;
  onVehicleClick: (token: string) => void;
}) {
  const [focusCamera, setFocusCamera] = useState<string | null>(null);

  const imagePath = (cam: string) => cameraImageUrl(sceneName, frameIdx, cam);

  if (focusCamera && visibility) {
    const vehicles = visibility[focusCamera] ?? [];
    return (
      <CameraFull
        channel={focusCamera}
        imagePath={imagePath(focusCamera)}
        vehicles={vehicles}
        showAllBboxes={showAllBboxes}
        highlightTokens={highlightTokens}
        onClose={() => setFocusCamera(null)}
        onVehicleClick={onVehicleClick}
      />
    );
  }

  return (
    <div className="grid grid-cols-3 grid-rows-2 gap-1 p-1 h-full overflow-hidden bg-[#070a10]">
      {CAMERA_ORDER.map((ch) => (
        <CameraThumb
          key={ch}
          channel={ch}
          imagePath={imagePath(ch)}
          vehicles={visibility?.[ch] ?? []}
          showAllBboxes={showAllBboxes}
          highlightTokens={highlightTokens}
          onSelect={() => setFocusCamera(ch)}
          onVehicleClick={onVehicleClick}
        />
      ))}
    </div>
  );
}
