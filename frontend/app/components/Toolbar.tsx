"use client";

import { useState } from "react";
import type { QueryResult } from "./QueryPanel";
import type { SceneData, SceneListEntry } from "../types";

interface Props {
  selectedScene: string;
  visibleScenes: SceneListEntry[];
  sceneData: SceneData | null;
  selectedSceneEntry: SceneListEntry | null;
  matchedSceneCount: number;
  totalSceneCount: number;
  frameIdx: number;
  totalFrames: number;
  playing: boolean;
  activeMatch: QueryResult | null;
  currentSceneMatches: QueryResult[];
  activeMatchIdx: number;
  highlightA: string;
  highlightB: string;
  showQuery: boolean;
  showTelemetry: boolean;
  onSceneChange: (e: React.ChangeEvent<HTMLSelectElement>) => void;
  onPlayPause: () => void;
  onStep: (delta: number) => void;
  onSeek: (frame: number) => void;
  onHighlightAChange: (v: string) => void;
  onHighlightBChange: (v: string) => void;
  onGoToMatch: (idx: number) => void;
  onToggleQuery: () => void;
  onToggleTelemetry: () => void;
}

export function Toolbar({
  selectedScene,
  visibleScenes,
  sceneData,
  selectedSceneEntry,
  matchedSceneCount,
  totalSceneCount,
  frameIdx,
  totalFrames,
  playing,
  activeMatch,
  currentSceneMatches,
  activeMatchIdx,
  highlightA,
  highlightB,
  showQuery,
  showTelemetry,
  onSceneChange,
  onPlayPause,
  onStep,
  onSeek,
  onHighlightAChange,
  onHighlightBChange,
  onGoToMatch,
  onToggleQuery,
  onToggleTelemetry,
}: Props) {
  const [showMeta, setShowMeta] = useState(false);
  const disabled = totalFrames === 0;
  const matchCount = currentSceneMatches.length;

  return (
    <div className="relative z-20 flex items-center gap-2 px-4 py-2 bg-[#111111] border-b border-[#333] shrink-0 flex-nowrap overflow-visible">
      <select
        value={selectedScene}
        onChange={onSceneChange}
        className="shrink-0 bg-[#222] text-white border border-[#444] rounded px-2 py-1 text-sm"
      >
        {visibleScenes.map((s) => (
          <option key={s.scene_name} value={s.scene_name}>{s.scene_name}</option>
        ))}
      </select>

      {matchedSceneCount > 0 && (
        <span className="text-xs text-gray-400">{matchedSceneCount} / {totalSceneCount}</span>
      )}

      <div className="relative shrink-0">
        <button
          onClick={() => setShowMeta((v) => !v)}
          className="whitespace-nowrap px-2 py-1 rounded border border-[#444] bg-[#1a1a1a] hover:bg-[#242424] text-xs text-gray-300"
        >
          Metadata {showMeta ? "▴" : "▾"}
        </button>
        {showMeta && (
          <div className="absolute left-0 top-full mt-2 w-[27rem] max-w-[90vw] rounded border border-[#3a3a3a] bg-[#111111] shadow-xl z-50">
            <div className="px-3 py-2 border-b border-[#2d2d2d] text-xs uppercase tracking-wide text-gray-500">
              Active Scene Metadata
            </div>
            <div className="p-3 text-xs font-mono text-gray-200 grid grid-cols-[8.5rem_1fr] gap-x-3 gap-y-2">
              <span className="text-gray-500">scene token</span>
              <span className="break-all">{sceneData?.scene_token ?? selectedSceneEntry?.scene_token ?? "-"}</span>
              <span className="text-gray-500">location</span>
              <span>{sceneData?.location ?? selectedSceneEntry?.location ?? "-"}</span>
              <span className="text-gray-500">data version</span>
              <span>{sceneData?.data_version ?? selectedSceneEntry?.data_version ?? "-"}</span>
              <span className="text-gray-500">subset</span>
              <span>{sceneData?.subset ?? selectedSceneEntry?.subset ?? "-"}</span>
            </div>
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <button
          onClick={() => onStep(-1)}
          disabled={disabled}
          className="w-8 h-8 rounded bg-[#333] hover:bg-[#444] text-xs leading-none inline-flex items-center justify-center disabled:opacity-40"
          title="Previous frame"
        >
          <span className="inline-block text-[10px] leading-none -rotate-90">▲</span>
        </button>
        <button
          onClick={onPlayPause}
          disabled={disabled}
          className="w-12 h-8 rounded bg-[#333] hover:bg-[#444] text-sm disabled:opacity-40 inline-flex items-center justify-center"
        >
          {playing ? "Pause" : "Play"}
        </button>
        <button
          onClick={() => onStep(1)}
          disabled={disabled}
          className="w-8 h-8 rounded bg-[#333] hover:bg-[#444] text-xs leading-none inline-flex items-center justify-center disabled:opacity-40"
          title="Next frame"
        >
          <span className="inline-block text-[10px] leading-none rotate-90">▲</span>
        </button>
      </div>

      <div className="relative flex shrink-0 flex-col w-40 lg:w-48">
        <input
          type="range"
          min={0}
          max={Math.max(0, totalFrames - 1)}
          value={frameIdx}
          onChange={(e) => onSeek(Number(e.target.value))}
          className="w-full accent-cyan-400"
          disabled={disabled}
        />
        {activeMatch && totalFrames > 1 && (() => {
          const left = (activeMatch.start_frame / (totalFrames - 1)) * 100;
          const width = ((activeMatch.end_frame - activeMatch.start_frame) / (totalFrames - 1)) * 100;
          return (
            <div
              className="absolute bottom-0 h-1 rounded pointer-events-none"
              style={{ left: `${left}%`, width: `${Math.max(width, 2)}%`, background: "linear-gradient(to right, #ffff00, #ff44ff)" }}
            />
          );
        })()}
      </div>

      <span className="shrink-0 text-xs text-gray-400 whitespace-nowrap">
        {totalFrames > 0 ? `${frameIdx + 1} / ${totalFrames}` : "-"}
      </span>

      <div className="flex shrink-0 items-center gap-2 ml-4 border-l border-[#444] pl-4">
        <span className="text-xs text-gray-500 whitespace-nowrap">Highlight:</span>
        <HighlightInput value={highlightA} color="#ffff00" bg="#1a1a00" border="#555522" label="A" onChange={onHighlightAChange} />
        <HighlightInput value={highlightB} color="#ff44ff" bg="#1a001a" border="#552255" label="B" onChange={onHighlightBChange} />
      </div>

      {matchCount > 0 && (
        <div className="flex shrink-0 items-center gap-2 ml-4 border-l border-[#444] pl-4">
          <span className="text-xs text-gray-400 whitespace-nowrap">
            {activeMatchIdx >= 0 ? `match ${activeMatchIdx + 1}/${matchCount}` : `${matchCount} matches`}
          </span>
          <button
            onClick={() => onGoToMatch(activeMatchIdx <= 0 ? matchCount - 1 : activeMatchIdx - 1)}
            className="w-8 h-8 rounded bg-[#333] hover:bg-[#444] text-xs leading-none inline-flex items-center justify-center"
          >
            <span className="inline-block text-[10px] leading-none -rotate-90">▲</span>
          </button>
          <button
            onClick={() => onGoToMatch(activeMatchIdx + 1)}
            className="w-8 h-8 rounded bg-[#333] hover:bg-[#444] text-xs leading-none inline-flex items-center justify-center"
          >
            <span className="inline-block text-[10px] leading-none rotate-90">▲</span>
          </button>
        </div>
      )}

      <div className="flex shrink-0 items-center gap-2 ml-4 border-l border-[#444] pl-4">
        <button
          onClick={onToggleQuery}
          className={`whitespace-nowrap px-2 py-0.5 rounded text-xs border ${showQuery ? "bg-purple-700 border-purple-500 text-white" : "bg-[#1a1a1a] border-[#444] text-gray-400 hover:bg-[#333]"}`}
        >
          {showQuery ? "Hide query" : "Query"}
        </button>
        <button
          onClick={onToggleTelemetry}
          className={`whitespace-nowrap px-2 py-0.5 rounded text-xs border ${showTelemetry ? "bg-[#1e1b4b] border-indigo-500 text-indigo-300" : "bg-[#1a1a1a] border-[#444] text-gray-400 hover:bg-[#333]"}`}
        >
          {showTelemetry ? "Hide telemetry" : "Telemetry"}
        </button>
      </div>
    </div>
  );
}

function HighlightInput({
  value,
  color,
  bg,
  border,
  label,
  onChange,
}: {
  value: string;
  color: string;
  bg: string;
  border: string;
  label: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-xs font-bold" style={{ color }}>{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="token"
        className="w-16 rounded px-2 py-0.5 text-xs font-mono placeholder-gray-600 focus:outline-none"
        style={{ background: bg, color, border: `1px solid ${border}` }}
      />
      {value && (
        <button onClick={() => onChange("")} className="text-gray-500 hover:text-white text-xs">x</button>
      )}
    </div>
  );
}
