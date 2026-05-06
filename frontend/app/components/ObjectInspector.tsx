"use client";

import { useEffect, useState } from "react";

import { nuscenesVisibilityBarColor } from "@/app/lib/nuscenesVisibility";

interface TrajectoryPoint {
  frame_idx: number;
  x: number;
  y: number;
  speed: number;
  accel: number;
}

interface VisTimelineEntry {
  frame_idx: number;
  cameras: string[];
  best_visibility: string;
}

interface Maneuver {
  maneuver: string;
  start_frame: number;
  end_frame: number;
  intersection_token: string;
}

interface ObjectDetail {
  instance_token: string;
  category: string;
  trajectory: TrajectoryPoint[];
  visibility_timeline: VisTimelineEntry[];
  maneuvers: Maneuver[];
}

const CAMERAS = ["CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT", "CAM_BACK", "CAM_BACK_LEFT", "CAM_BACK_RIGHT"];

export function ObjectInspector({
  sceneName,
  instanceToken,
  frameIdx,
  onClose,
  onToggleTrajectory,
  showTrajectory,
}: {
  sceneName: string;
  instanceToken: string;
  frameIdx: number;
  onClose: () => void;
  onToggleTrajectory: () => void;
  showTrajectory: boolean;
}) {
  const [data, setData] = useState<ObjectDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setLoadError(null);
    const url = `/api/scene/${encodeURIComponent(sceneName)}/object/${encodeURIComponent(instanceToken)}`;
    fetch(url)
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) {
          const detail = d?.detail;
          const msg =
            typeof detail === "string"
              ? detail
              : Array.isArray(detail)
                ? detail.map((x: { msg?: string }) => x?.msg ?? JSON.stringify(x)).join("; ")
                : r.statusText || "Request failed";
          setLoadError(msg);
          return;
        }
        const trajectory = Array.isArray(d.trajectory) ? d.trajectory : [];
        const visibility_timeline = Array.isArray(d.visibility_timeline) ? d.visibility_timeline : [];
        const maneuvers = Array.isArray(d.maneuvers) ? d.maneuvers : [];
        setData({
          instance_token: d.instance_token ?? instanceToken,
          category: d.category ?? "?",
          trajectory,
          visibility_timeline,
          maneuvers,
        });
      })
      .catch(() => setLoadError("Failed to load object"));
  }, [sceneName, instanceToken]);

  if (loadError) {
    return (
      <div className="p-3 text-red-400 text-xs">
        {loadError}
        <button type="button" onClick={onClose} className="block mt-2 text-gray-500 hover:text-white">
          close
        </button>
      </div>
    );
  }
  if (!data) return <div className="p-3 text-gray-500 text-xs">Loading...</div>;

  const currentPose = data.trajectory.find((t) => t.frame_idx === frameIdx);
  const totalFrames = data.trajectory.length > 0 ? data.trajectory[data.trajectory.length - 1].frame_idx + 1 : 0;

  return (
    <div className="flex flex-col h-full overflow-y-auto bg-[#0d0d0d] text-sm p-3 gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-gray-300 truncate">{data.instance_token.slice(0, 12)}</span>
        <button onClick={onClose} className="text-gray-500 hover:text-white text-xs">close</button>
      </div>

      <div className="text-xs text-gray-400">{data.category}</div>

      {currentPose && (
        <div className="grid grid-cols-2 gap-1 text-xs">
          <div><span className="text-gray-500">Speed</span> <span className="text-white">{currentPose.speed.toFixed(1)} m/s</span></div>
          <div><span className="text-gray-500">Accel</span> <span className="text-white">{currentPose.accel.toFixed(1)} m/s²</span></div>
        </div>
      )}

      <button
        onClick={onToggleTrajectory}
        className={`px-2 py-1 rounded text-xs border ${showTrajectory ? "bg-[#1e3a5f] border-[#3b82f6] text-[#93c5fd]" : "bg-[#1a1a1a] border-[#444] text-gray-400 hover:bg-[#333]"}`}
      >
        {showTrajectory ? "Hide trajectory" : "Show trajectory"}
      </button>

      {/* Maneuvers */}
      {data.maneuvers.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Maneuvers</div>
          {data.maneuvers.map((m, i) => (
            <div key={i} className="text-xs text-gray-300">
              {m.maneuver} f{m.start_frame}-{m.end_frame}
            </div>
          ))}
        </div>
      )}

      {/* Visibility timeline */}
      {data.visibility_timeline.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Visibility</div>
          {CAMERAS.map((cam) => {
            const entries = data.visibility_timeline.filter((e) => e.cameras.includes(cam));
            if (entries.length === 0) return null;
            return (
              <div key={cam} className="flex items-center gap-1 mb-0.5">
                <span className="text-[9px] text-gray-500 w-20 truncate font-mono">{cam.replace("CAM_", "")}</span>
                <div className="flex-1 flex h-2 gap-px">
                  {Array.from({ length: totalFrames }, (_, fi) => {
                    const entry = entries.find((e) => e.frame_idx === fi);
                    return (
                      <div
                        key={fi}
                        className="flex-1 rounded-sm"
                        style={{
                          backgroundColor: entry
                            ? nuscenesVisibilityBarColor(entry.best_visibility)
                            : "#1e293b",
                        }}
                      />
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export type { ObjectDetail, TrajectoryPoint };
