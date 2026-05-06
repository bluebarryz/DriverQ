"use client";

import { useEffect, useLayoutEffect, useRef, useState, useCallback, useMemo } from "react";
import { CameraPanel, type FrameVisibility } from "./components/CameraPanel";
import { TelemetryChart, type TelemetrySample } from "./components/TelemetryChart";
import { QueryPanel, type QueryResult } from "./components/QueryPanel";
import { Toolbar } from "./components/Toolbar";
import { Footer } from "./components/Footer";
import { useThreeViewport } from "./hooks/useThreeViewport";
import { apiUrl } from "./lib/api";
import type { SceneData, SceneListEntry, TrajectoryPoint3D } from "./types";

const HIGHLIGHT_A_COLOR = "#ffff00";
const HIGHLIGHT_B_COLOR = "#ff44ff";
const EGO_TELEMETRY_DEFAULT_COLOR = "#9ca3af";

export default function Home() {
  const [sceneList, setSceneList] = useState<SceneListEntry[]>([]);
  const [selectedScene, setSelectedScene] = useState("");
  const [sceneData, setSceneData] = useState<SceneData | null>(null);
  const [frameIdx, setFrameIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [followCam, setFollowCam] = useState(false);
  const [highlightA, setHighlightA] = useState("");
  const [highlightB, setHighlightB] = useState("");
  const [trajectoryA, setTrajectoryA] = useState<TrajectoryPoint3D[]>([]);
  const [trajectoryB, setTrajectoryB] = useState<TrajectoryPoint3D[]>([]);
  const [showCameras, setShowCameras] = useState(false);
  const [visibility, setVisibility] = useState<FrameVisibility | null>(null);
  const [showAllBboxes, setShowAllBboxes] = useState(false);
  const [showQuery, setShowQuery] = useState(false);
  const [queryResults, setQueryResults] = useState<QueryResult[]>([]);
  const [activeMatchIdx, setActiveMatchIdx] = useState(-1);
  const [showTelemetry, setShowTelemetry] = useState(false);
  const [telemetryHeight, setTelemetryHeight] = useState(200);
  const [cameraHeight, setCameraHeight] = useState(300);

  const mainContainerRef = useRef<HTMLDivElement>(null);
  const cameraHeightInitialized = useRef(false);
  const playIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const trajectoryCacheRef = useRef<Map<string, TrajectoryPoint3D[]>>(new Map());

  const totalFrames = sceneData?.frames.length ?? 0;

  const selectedSceneEntry = useMemo(
    () => sceneList.find((s) => s.scene_name === selectedScene) ?? null,
    [sceneList, selectedScene],
  );

  const matchesByScene = useMemo(() => {
    const map = new Map<string, QueryResult[]>();
    for (const r of queryResults) {
      const arr = map.get(r.scene_name) ?? [];
      arr.push(r);
      map.set(r.scene_name, arr);
    }
    return map;
  }, [queryResults]);

  const visibleScenes = useMemo(
    () => queryResults.length === 0 ? sceneList : sceneList.filter((s) => matchesByScene.has(s.scene_name)),
    [sceneList, queryResults, matchesByScene],
  );

  const currentSceneMatches = useMemo(
    () => matchesByScene.get(selectedScene) ?? [],
    [matchesByScene, selectedScene],
  );

  const activeMatch = activeMatchIdx >= 0 && activeMatchIdx < currentSceneMatches.length
    ? currentSceneMatches[activeMatchIdx] : null;

  const egoSamples = useMemo((): TelemetrySample[] => {
    if (!sceneData) return [];
    return sceneData.frames.map((f) => ({ frameIdx: f.frame_idx, speed: f.ego.speed, accel: f.ego.accel }));
  }, [sceneData]);

  const selectedTrackSamples = useMemo(() => {
    if (!sceneData) return [];
    return [
      { prefix: highlightA, color: HIGHLIGHT_A_COLOR },
      { prefix: highlightB, color: HIGHLIGHT_B_COLOR },
    ]
      .filter((x) => x.prefix !== "")
      .map(({ prefix, color }) => {
        const samples: TelemetrySample[] = [];
        for (const frame of sceneData.frames) {
          const ann = frame.annotations.find((a) => a.token.startsWith(prefix));
          if (ann) samples.push({ frameIdx: frame.frame_idx, speed: ann.speed, accel: ann.accel });
        }
        return { token: prefix, samples, color };
      });
  }, [sceneData, highlightA, highlightB]);

  const egoTelemetryColor = useMemo(() => {
    if (highlightA && "ego".startsWith(highlightA)) return HIGHLIGHT_A_COLOR;
    if (highlightB && "ego".startsWith(highlightB)) return HIGHLIGHT_B_COLOR;
    return EGO_TELEMETRY_DEFAULT_COLOR;
  }, [highlightA, highlightB]);

  const mountRef = useThreeViewport({ sceneData, frameIdx, followCam, highlightA, highlightB, trajectoryA, trajectoryB });

  useLayoutEffect(() => {
    if (!showCameras || cameraHeightInitialized.current) return;
    const rect = mainContainerRef.current?.getBoundingClientRect();
    if (!rect || rect.height === 0) return;
    setCameraHeight(rect.height * 0.45);
    cameraHeightInitialized.current = true;
  }, [showCameras]);

  useEffect(() => {
    if (visibleScenes.length > 0 && !visibleScenes.some((s) => s.scene_name === selectedScene)) {
      setSelectedScene(visibleScenes[0].scene_name);
    }
  }, [visibleScenes]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setActiveMatchIdx(-1); }, [selectedScene]);

  useEffect(() => {
    fetch(apiUrl("/api/scenes"))
      .then((r) => r.json())
      .then((data: SceneListEntry[]) => {
        setSceneList(data);
        if (data.length > 0) setSelectedScene(data[0].scene_name);
      })
      .catch(() => setSceneList([]));
  }, []);

  useEffect(() => {
    if (!selectedScene) return;
    setFrameIdx(0);
    setPlaying(false);
    setVisibility(null);
    fetch(apiUrl(`/api/scene/${encodeURIComponent(selectedScene)}`))
      .then((r) => r.json())
      .then((data: SceneData) => setSceneData(data))
      .catch((e) => console.error("Failed to load scene:", e));
  }, [selectedScene]);

  useEffect(() => {
    const fetchTrajectory = async (prefix: string, setter: (pts: TrajectoryPoint3D[]) => void) => {
      const p = prefix.trim();
      if (!selectedScene || !p) { setter([]); return; }
      const key = `${selectedScene}:${p}`;
      const cached = trajectoryCacheRef.current.get(key);
      if (cached) { setter(cached); return; }
      try {
        const r = await fetch(apiUrl(`/api/scene/${encodeURIComponent(selectedScene)}/trajectory/${encodeURIComponent(p)}?stride=1`));
        if (!r.ok) { setter([]); return; }
        const d = await r.json();
        const points = Array.isArray(d.trajectory) ? d.trajectory : [];
        trajectoryCacheRef.current.set(key, points);
        setter(points);
      } catch {
        setter([]);
      }
    };
    fetchTrajectory(highlightA, setTrajectoryA);
    fetchTrajectory(highlightB, setTrajectoryB);
  }, [selectedScene, highlightA, highlightB]);

  useEffect(() => {
    if (!showCameras || !selectedScene) { setVisibility(null); return; }
    fetch(apiUrl(`/api/scene/${encodeURIComponent(selectedScene)}/visibility/${frameIdx}`))
      .then((r) => r.json())
      .then((data: FrameVisibility) => setVisibility(data))
      .catch(() => setVisibility(null));
  }, [showCameras, selectedScene, frameIdx]);

  useEffect(() => {
    if (playIntervalRef.current) { clearInterval(playIntervalRef.current); playIntervalRef.current = null; }
    if (!playing || !sceneData) return;
    playIntervalRef.current = setInterval(() => {
      setFrameIdx((i) => {
        if (i + 1 >= sceneData.frames.length) { setPlaying(false); return i; }
        return i + 1;
      });
    }, 200);
    return () => { if (playIntervalRef.current) clearInterval(playIntervalRef.current); };
  }, [playing, sceneData]);

  const handleSceneChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedScene(e.target.value);
  }, []);

  const handleVehicleClick = useCallback((token: string) => {
    setHighlightB("");
    setHighlightA(token.slice(0, 8));
  }, []);

  const handleQueryComplete = useCallback((results: QueryResult[]) => {
    setQueryResults(results);
    setActiveMatchIdx(-1);
    setHighlightA("");
    setHighlightB("");
  }, []);

  const handleClearQuery = useCallback(() => {
    setQueryResults([]);
    setActiveMatchIdx(-1);
    setHighlightA("");
    setHighlightB("");
  }, []);

  const goToMatch = useCallback((idx: number) => {
    if (currentSceneMatches.length === 0) return;
    const i = ((idx % currentSceneMatches.length) + currentSceneMatches.length) % currentSceneMatches.length;
    setActiveMatchIdx(i);
    const match = currentSceneMatches[i];
    setFrameIdx(match.start_frame);
    setHighlightA(match.objects[0]?.token ?? "");
    setHighlightB(match.objects[1]?.token ?? "");
  }, [currentSceneMatches]);

  const stepFrame = useCallback((delta: number) => {
    if (totalFrames === 0) return;
    setPlaying(false);
    setFrameIdx((i) => Math.max(0, Math.min(totalFrames - 1, i + delta)));
  }, [totalFrames]);

  const handleSeek = useCallback((frame: number) => {
    setPlaying(false);
    setFrameIdx(frame);
  }, []);

  const handlePlayPause = useCallback(() => {
    if (totalFrames === 0) return;
    if (!playing && frameIdx >= totalFrames - 1) setFrameIdx(activeMatch?.start_frame ?? 0);
    setPlaying((p) => !p);
  }, [frameIdx, activeMatch, playing, totalFrames]);

  const startPanelResize = useCallback(
    (panel: "telemetry" | "camera") => (e: React.MouseEvent) => {
      e.preventDefault();
      const startY = e.clientY;
      const startTel = telemetryHeight;
      const startCam = cameraHeight;
      const sign = panel === "telemetry" ? 1 : -1;
      document.body.style.cursor = "ns-resize";
      document.body.style.userSelect = "none";
      const onMove = (ev: MouseEvent) => {
        const rect = mainContainerRef.current?.getBoundingClientRect();
        if (!rect) return;
        const otherH = panel === "telemetry" ? (showCameras ? startCam : 0) : (showTelemetry ? startTel : 0);
        const start = panel === "telemetry" ? startTel : startCam;
        const newH = Math.max(60, Math.min(rect.height - otherH - 8, start + sign * (ev.clientY - startY)));
        if (panel === "telemetry") setTelemetryHeight(newH);
        else setCameraHeight(newH);
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [telemetryHeight, cameraHeight, showTelemetry, showCameras],
  );

  return (
    <div className="flex flex-col w-screen h-screen bg-[#0a0a0a] text-white">
      <Toolbar
        selectedScene={selectedScene}
        visibleScenes={visibleScenes}
        sceneData={sceneData}
        selectedSceneEntry={selectedSceneEntry}
        matchedSceneCount={matchesByScene.size}
        totalSceneCount={sceneList.length}
        frameIdx={frameIdx}
        totalFrames={totalFrames}
        playing={playing}
        activeMatch={activeMatch}
        currentSceneMatches={currentSceneMatches}
        activeMatchIdx={activeMatchIdx}
        highlightA={highlightA}
        highlightB={highlightB}
        showQuery={showQuery}
        showTelemetry={showTelemetry}
        onSceneChange={handleSceneChange}
        onPlayPause={handlePlayPause}
        onStep={stepFrame}
        onSeek={handleSeek}
        onHighlightAChange={setHighlightA}
        onHighlightBChange={setHighlightB}
        onGoToMatch={goToMatch}
        onToggleQuery={() => setShowQuery((v) => !v)}
        onToggleTelemetry={() => setShowTelemetry((v) => !v)}
      />

      {showQuery && (
        <QueryPanel
          onQueryComplete={handleQueryComplete}
          onClearQuery={handleClearQuery}
          hasResults={queryResults.length > 0}
          activeMatch={activeMatch}
        />
      )}

      <div ref={mainContainerRef} className="flex-1 flex flex-col min-h-0">
        {showTelemetry && sceneData && (
          <>
            <div className="bg-[#070a10] px-2 pt-1 pb-0 shrink-0 overflow-hidden" style={{ height: telemetryHeight }}>
              <TelemetryChart
                egoSamples={egoSamples}
                trackSamples={selectedTrackSamples}
                egoColor={egoTelemetryColor}
                frameRange={[0, Math.max(totalFrames - 1, 0)]}
                cursorFrame={frameIdx}
              />
            </div>
            <div onMouseDown={startPanelResize("telemetry")} className="h-1 shrink-0 bg-[#333] hover:bg-cyan-500 cursor-ns-resize" />
          </>
        )}
        <div ref={mountRef} className="flex-1 min-w-0 min-h-0" />
        {showCameras && sceneData && (
          <>
            <div onMouseDown={startPanelResize("camera")} className="h-1 shrink-0 bg-[#333] hover:bg-cyan-500 cursor-ns-resize" />
            <div className="overflow-hidden shrink-0" style={{ height: cameraHeight }}>
              <CameraPanel
                sceneName={sceneData.scene_name}
                frameIdx={frameIdx}
                visibility={visibility}
                showAllBboxes={showAllBboxes}
                highlightTokens={{ [highlightA]: HIGHLIGHT_A_COLOR, [highlightB]: HIGHLIGHT_B_COLOR }}
                onVehicleClick={handleVehicleClick}
              />
            </div>
          </>
        )}
      </div>

      <Footer
        showCameras={showCameras}
        followCam={followCam}
        showAllBboxes={showAllBboxes}
        onToggleCameras={() => setShowCameras((v) => !v)}
        onToggleFollowCam={setFollowCam}
        onToggleAllBboxes={() => setShowAllBboxes((v) => !v)}
      />
    </div>
  );
}
