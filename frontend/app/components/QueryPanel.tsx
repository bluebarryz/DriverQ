"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { apiUrl } from "@/app/lib/api";

export interface QueryResult {
  scene_name: string;
  scene_token: string;
  start_frame: number;
  end_frame: number;
  objects: { token: string; category: string; label: string }[];
  summary: string;
  intersection_token?: string;
}

interface QueryResponse {
  count: number;
  results: QueryResult[];
}

const PRESETS = [
  { key: "left_turn", label: "Left turns", params: { maneuver: "left" } },
  { key: "right_turn", label: "Right turns", params: { maneuver: "right" } },
  { key: "braking", label: "Braking", params: { preset: "braking" } },
  { key: "pedestrian", label: "Pedestrian", params: { preset: "pedestrian_scenes" } },
  { key: "CCCscp", label: "CCCscp", params: { preset: "CCCscp" } },
  { key: "CCFtap", label: "CCFtap", params: { preset: "CCFtap" } },
  { key: "cut_in", label: "Cut-in", params: { preset: "cut_in" } },
  { key: "lane_change", label: "Lane-change", params: { preset: "lane_change" } },
  { key: "ped_crossing", label: "Ped crossing path", params: { preset: "ped_crossing" } },
  { key: "ped_crossing_ego", label: "Ped crossing ego", params: { preset: "ped_crossing", ego_only: true } },
  { key: "occluded_ped", label: "Occluded ped", params: { preset: "occluded_ped" } },
] as const;

const CAMERAS = ["CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT", "CAM_BACK", "CAM_BACK_LEFT", "CAM_BACK_RIGHT"];

const EGO_ONLY_PRESETS = new Set([
  "braking",
  "lane_change",
  "left_turn",
  "right_turn",
  "CCCscp",
  "CCFtap",
]);

// CCCscp/CCFtap keep camera filters active under ego-only - they re-target
// the filter at the non-ego partner instead of disabling it.
const EGO_ONLY_DISABLES_CAMERA_FILTERS = new Set([
  "braking",
  "lane_change",
  "left_turn",
  "right_turn",
]);

function presetSupportsEgoOnly(key: string | null): boolean {
  return key !== null && EGO_ONLY_PRESETS.has(key);
}

function egoOnlyDisablesCameraFilters(key: string | null): boolean {
  return key !== null && EGO_ONLY_DISABLES_CAMERA_FILTERS.has(key);
}

function cameraSummary(selected: string[]) {
  if (selected.length === 0) return "any";
  if (selected.length <= 2) return selected.join(", ");
  return `${selected.length} selected`;
}

function presetParamsForKey(key: string): Record<string, unknown> {
  const p = PRESETS.find((x) => x.key === key);
  return p ? { ...p.params } : {};
}

function CameraSelect({
  label,
  ariaLabel,
  selected,
  onToggle,
  open,
  setOpen,
  disabled,
}: {
  label: string;
  ariaLabel: string;
  selected: string[];
  onToggle: (cam: string) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
  disabled: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, setOpen]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => {
          if (disabled) return;
          setOpen(!open);
        }}
        disabled={disabled}
        className={`flex items-center gap-2 rounded border px-2 py-1 text-left text-xs min-w-[14rem] ${
          disabled
            ? "cursor-not-allowed border-[#333] bg-[#1a1a1a] text-gray-600"
            : "border-[#444] bg-[#222] text-gray-300 hover:border-[#666]"
        }`}
      >
        <span className="text-gray-400 shrink-0">{label}</span>
        <span className="truncate text-gray-500" title={cameraSummary(selected)}>
          {cameraSummary(selected)}
        </span>
        <span className="ml-auto shrink-0 text-[10px] text-gray-500" aria-hidden>
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open && (
        <div
          className="absolute left-0 top-full z-50 mt-1 min-w-[14rem] rounded border border-[#444] bg-[#1a1a1a] py-1.5 px-2 shadow-lg"
          role="listbox"
          aria-label={ariaLabel}
        >
          {CAMERAS.map((c) => (
            <label
              key={c}
              className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-xs text-gray-300 hover:bg-[#2a2a2a]"
            >
              <input
                type="checkbox"
                checked={selected.includes(c)}
                disabled={disabled}
                onChange={() => onToggle(c)}
                className="accent-cyan-400"
              />
              {c}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function FilterCheckbox({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex items-center gap-1 text-xs select-none ${
        disabled ? "text-gray-600 cursor-not-allowed" : "text-gray-400 cursor-pointer"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-cyan-400"
      />
      {label}
    </label>
  );
}

export function QueryPanel({
  onQueryComplete,
  onClearQuery,
  hasResults,
  activeMatch,
}: {
  onQueryComplete: (results: QueryResult[]) => void;
  onClearQuery: () => void;
  hasResults: boolean;
  activeMatch: QueryResult | null;
}) {
  const [loading, setLoading] = useState(false);
  const [activePreset, setActivePreset] = useState<string | null>(null);

  const [pedEgoFrameGap, setPedEgoFrameGap] = useState("10");
  const [occludedFramesMin, setOccludedFramesMin] = useState("1");
  const [selectedCams, setSelectedCams] = useState<string[]>([]);
  const [selectedNotCams, setSelectedNotCams] = useState<string[]>([]);
  const [egoOnly, setEgoOnly] = useState(false);
  const [nonEgoOnly, setNonEgoOnly] = useState(false);
  const [camOpen, setCamOpen] = useState(false);
  const [notCamOpen, setNotCamOpen] = useState(false);
  const [matchDataOpen, setMatchDataOpen] = useState(false);
  const matchDataRef = useRef<HTMLDivElement>(null);
  const cameraFiltersDisabled = egoOnly && egoOnlyDisablesCameraFilters(activePreset);

  useEffect(() => {
    setCamOpen(false);
    setNotCamOpen(false);
    setMatchDataOpen(false);
  }, [activePreset]);

  useEffect(() => {
    if (!matchDataOpen) return;
    const onDown = (e: MouseEvent) => {
      if (matchDataRef.current && !matchDataRef.current.contains(e.target as Node)) {
        setMatchDataOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [matchDataOpen]);

  useEffect(() => {
    if (!cameraFiltersDisabled) return;
    setCamOpen(false);
    setNotCamOpen(false);
  }, [cameraFiltersDisabled]);

  const toggleCam = useCallback((cam: string) => {
    setSelectedCams((prev) => {
      const next = prev.includes(cam) ? prev.filter((c) => c !== cam) : [...prev, cam];
      return [...next].sort((a, b) => CAMERAS.indexOf(a) - CAMERAS.indexOf(b));
    });
  }, []);

  const toggleNotCam = useCallback((cam: string) => {
    setSelectedNotCams((prev) => {
      const next = prev.includes(cam) ? prev.filter((c) => c !== cam) : [...prev, cam];
      return [...next].sort((a, b) => CAMERAS.indexOf(a) - CAMERAS.indexOf(b));
    });
  }, []);

  const runQuery = useCallback(async (params: Record<string, unknown>) => {
    setLoading(true);
    try {
      const resp = await fetch(apiUrl("/api/query"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) });
      const data: QueryResponse = await resp.json();
      onQueryComplete(data.results);
    } finally {
      setLoading(false);
    }
  }, [onQueryComplete]);

  const runPresetQuery = useCallback(
    (key: string) => {
      const base = presetParamsForKey(key);
      const sendsEgoOnly = egoOnly && presetSupportsEgoOnly(key);
      const sendsNonEgoOnly = nonEgoOnly && presetSupportsEgoOnly(key);
      const skipsCams = egoOnly && egoOnlyDisablesCameraFilters(key);

      const withExtras =
        key === "ped_crossing_ego"
          ? {
              ...base,
              ped_cross_frame_gap_max: (() => {
                const g = parseInt(pedEgoFrameGap, 10);
                return Number.isFinite(g) ? g : 10;
              })(),
            }
          : key === "occluded_ped"
            ? {
                ...base,
                occluded_frames_min: (() => {
                  const n = parseInt(occludedFramesMin, 10);
                  return Number.isFinite(n) && n >= 1 ? n : 1;
                })(),
              }
            : base;

      const withCams = skipsCams
        ? withExtras
        : {
            ...withExtras,
            ...(selectedCams.length > 0 ? { visible_cameras: selectedCams } : {}),
            ...(selectedNotCams.length > 0 ? { hidden_cameras: selectedNotCams } : {}),
          };
      const params = {
        ...withCams,
        ...(sendsEgoOnly ? { ego_only: true } : {}),
        ...(sendsNonEgoOnly ? { non_ego_only: true } : {}),
      };
      runQuery(params);
    },
    [runQuery, pedEgoFrameGap, occludedFramesMin, selectedCams, selectedNotCams, egoOnly, nonEgoOnly]
  );

  const handlePreset = useCallback(
    (key: string) => {
      setActivePreset(key);
      runPresetQuery(key);
    },
    [runPresetQuery]
  );

  const handleApplyFilters = useCallback(() => {
    if (!activePreset) return;
    runPresetQuery(activePreset);
  }, [activePreset, runPresetQuery]);

  const handleClearFilters = useCallback(() => {
    setSelectedCams([]);
    setSelectedNotCams([]);
    setEgoOnly(false);
    setNonEgoOnly(false);
    setPedEgoFrameGap("10");
    setOccludedFramesMin("1");
    setCamOpen(false);
    setNotCamOpen(false);
    if (activePreset) {
      const base = presetParamsForKey(activePreset);
      const withGap =
        activePreset === "ped_crossing_ego"
          ? { ...base, ped_cross_frame_gap_max: 10 }
          : activePreset === "occluded_ped"
            ? { ...base, occluded_frames_min: 1 }
            : base;
      runQuery(withGap);
    }
  }, [activePreset, runQuery]);

  const handleClear = useCallback(() => {
    setActivePreset(null);
    onClearQuery();
  }, [onClearQuery]);

  return (
    <div className="flex flex-col gap-2 px-4 py-2 bg-[#0d0d0d] border-b border-[#333] text-sm">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-gray-500 uppercase tracking-wide">Presets</span>
        {PRESETS.map((p) => (
          <button
            key={p.key}
            onClick={() => handlePreset(p.key)}
            className={`px-2 py-0.5 rounded text-xs border ${activePreset === p.key ? "bg-blue-600 border-blue-500 text-white" : "bg-transparent border-[#444] text-gray-400 hover:border-[#888]"}`}
          >
            {p.label}
          </button>
        ))}
        {hasResults && (
          <button onClick={handleClear} className="px-2 py-0.5 rounded text-xs border border-[#444] text-gray-400 hover:border-[#888]">
            clear
          </button>
        )}
        {loading && <span className="text-xs text-gray-500 animate-pulse">searching...</span>}
      </div>

      {activePreset !== null && (
        <div className="flex items-center gap-3 flex-wrap border-t border-[#222] pt-2">
          <span className="text-xs text-gray-500 uppercase tracking-wide shrink-0">Filters</span>
          <CameraSelect
            label="Cameras visible in"
            ariaLabel="Cameras visible in"
            selected={selectedCams}
            onToggle={toggleCam}
            open={camOpen}
            setOpen={setCamOpen}
            disabled={cameraFiltersDisabled}
          />
          <CameraSelect
            label="Cameras not visible in"
            ariaLabel="Cameras not visible in"
            selected={selectedNotCams}
            onToggle={toggleNotCam}
            open={notCamOpen}
            setOpen={setNotCamOpen}
            disabled={cameraFiltersDisabled}
          />
          {presetSupportsEgoOnly(activePreset) && (
            <>
              <FilterCheckbox
                label="ego only"
                checked={egoOnly}
                disabled={nonEgoOnly}
                onChange={setEgoOnly}
              />
              <FilterCheckbox
                label="non-ego only"
                checked={nonEgoOnly}
                disabled={egoOnly}
                onChange={setNonEgoOnly}
              />
            </>
          )}
          {(activePreset === "CCCscp" || activePreset === "CCFtap" || activePreset === "left_turn" || activePreset === "right_turn" || activePreset === "curve") && (
            <div className="relative" ref={matchDataRef}>
              <button
                type="button"
                aria-expanded={matchDataOpen}
                aria-haspopup="listbox"
                onClick={() => setMatchDataOpen((o) => !o)}
                className="flex items-center gap-2 rounded border px-2 py-1 text-left text-xs min-w-[10rem] border-[#444] bg-[#222] text-gray-300 hover:border-[#666]"
              >
                <span className="text-gray-400 shrink-0">Match data</span>
                <span className="ml-auto shrink-0 text-[10px] text-gray-500" aria-hidden>
                  {matchDataOpen ? "▴" : "▾"}
                </span>
              </button>
              {matchDataOpen && (
                <div
                  className="absolute left-0 top-full z-50 mt-1 min-w-[18rem] rounded border border-[#444] bg-[#1a1a1a] py-1.5 px-2 shadow-lg"
                  aria-label="Match data"
                >
                  {activeMatch ? (
                    <>
                      {activeMatch.objects.map((o, i) => (
                        <div key={`${o.token}-${i}`} className="px-1 py-0.5 text-xs text-gray-300 font-mono">
                          <span className="text-gray-400">{o.token}</span>
                          <span className="text-gray-500">: </span>
                          <span className="text-white">{o.label}</span>
                        </div>
                      ))}
                      {activeMatch.intersection_token && (
                        <div className="px-1 py-0.5 text-xs text-gray-300 font-mono">
                          <span className="text-gray-400">{activeMatch.intersection_token}</span>
                          <span className="text-gray-500">: </span>
                          <span className="text-white">intersection</span>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="px-1 py-1 text-xs text-gray-500">No match selected.</div>
                  )}
                </div>
              )}
            </div>
          )}
          {activePreset === "ped_crossing_ego" && (
            <label className="flex items-center gap-1.5 text-xs text-gray-400">
              <span className="text-gray-500 shrink-0">|vehicle_crossing_frame − ped_crossing_frame| ≤</span>
              <input
                type="number"
                min={0}
                value={pedEgoFrameGap}
                onChange={(e) => setPedEgoFrameGap(e.target.value)}
                className="w-14 bg-[#222] text-white border border-[#444] rounded px-1 py-0.5 text-xs"
              />
              <span className="text-gray-500">frames</span>
            </label>
          )}
          {activePreset === "occluded_ped" && (
            <label className="flex items-center gap-1.5 text-xs text-gray-400">
              <span className="text-gray-500 shrink-0">occluded frames ≥</span>
              <input
                type="number"
                min={1}
                value={occludedFramesMin}
                onChange={(e) => setOccludedFramesMin(e.target.value)}
                className="w-14 bg-[#222] text-white border border-[#444] rounded px-1 py-0.5 text-xs"
              />
            </label>
          )}
          <button onClick={handleApplyFilters} className="px-2 py-0.5 rounded bg-purple-700 hover:bg-purple-600 text-white text-xs">
            Apply filters
          </button>
          <button
            type="button"
            onClick={handleClearFilters}
            className="px-2 py-0.5 rounded border border-[#444] text-gray-400 hover:border-[#888] hover:text-gray-200 text-xs"
          >
            Clear filters
          </button>
        </div>
      )}
    </div>
  );
}
