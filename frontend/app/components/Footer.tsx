"use client";

export function Footer({
  showCameras,
  followCam,
  showAllBboxes,
  onToggleCameras,
  onToggleFollowCam,
  onToggleAllBboxes,
}: {
  showCameras: boolean;
  followCam: boolean;
  showAllBboxes: boolean;
  onToggleCameras: () => void;
  onToggleFollowCam: (v: boolean) => void;
  onToggleAllBboxes: () => void;
}) {
  return (
    <div className="shrink-0 px-4 py-2 bg-[#111111] border-t border-[#333] flex items-center gap-3">
      <p className="flex-1 min-w-0 text-[14px] text-gray-500 leading-tight truncate">
        Visualized data is from the{" "}
        <a href="https://www.nuscenes.org/" target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-gray-200 underline">
          nuScenes
        </a>{" "}
        dataset (© 2020 Motional), licensed under{" "}
        <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/" target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-gray-200 underline">
          CC BY-NC-SA 4.0
        </a>
        . Not affiliated with or endorsed by Motional.
      </p>
      {showCameras && (
        <>
          <label className="flex items-center gap-1 text-xs cursor-pointer select-none text-gray-300">
            <input type="checkbox" checked={followCam} onChange={(e) => onToggleFollowCam(e.target.checked)} className="accent-cyan-400" />
            Follow cam
          </label>
          <button
            onClick={onToggleAllBboxes}
            className={`px-2 py-0.5 rounded text-xs border ${!showAllBboxes ? "bg-[#1f3f2b] border-[#22c55e] text-[#86efac]" : "bg-[#1a1a1a] border-[#444] text-gray-400 hover:bg-[#333]"}`}
          >
            {showAllBboxes ? "Show highlighted object boxes only" : "Show all bounding boxes"}
          </button>
        </>
      )}
      <button
        onClick={onToggleCameras}
        className={`px-2 py-0.5 rounded text-xs border ${showCameras ? "bg-[#1e3a5f] border-[#3b82f6] text-[#93c5fd]" : "bg-[#1a1a1a] border-[#444] text-gray-400 hover:bg-[#333]"}`}
      >
        {showCameras ? "Hide cameras" : "Cameras"}
      </button>
    </div>
  );
}
