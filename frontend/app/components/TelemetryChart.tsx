"use client";

export interface TelemetrySample { frameIdx: number; speed: number; accel: number }

const EGO_DEFAULT_COLOR = "#9ca3af";

const PAD = { left: 34, right: 8, top: 8, bottom: 18 };
const VW = 320;
const VH = 240;
const PW = VW - PAD.left - PAD.right;
const PH = VH - PAD.top - PAD.bottom;

function SingleChart({
  series,
  frameRange,
  cursorFrame,
  metric,
}: {
  series: { id: string; color: string; points: [number, number][] }[];
  frameRange: [number, number];
  cursorFrame: number;
  metric: "speed" | "accel";
}) {
  const [f0, f1] = frameRange;
  const fSpan = Math.max(f1 - f0, 1);

  const allY = series.flatMap(s => s.points.map(p => p[1]));
  const yLo = Math.min(allY.length ? Math.min(...allY) : 0, 0);
  const yHi = Math.max(allY.length ? Math.max(...allY) : 1, yLo + 0.01);
  const ySpan = yHi - yLo;

  const sx = (f: number) => PAD.left + ((f - f0) / fSpan) * PW;
  const sy = (y: number) => PAD.top + (1 - (y - yLo) / ySpan) * PH;
  const cursorX = sx(Math.max(f0, Math.min(f1, cursorFrame)));

  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} className="w-full h-full" preserveAspectRatio="none">
      <rect x={PAD.left} y={PAD.top} width={PW} height={PH} fill="#0f172a" />

      {metric === "accel" && yLo < 0 && yHi > 0 && (
        <line x1={PAD.left} x2={PAD.left + PW} y1={sy(0)} y2={sy(0)}
          stroke="#334155" strokeWidth="1" strokeDasharray="4,3" />
      )}

      <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + PH} stroke="#334155" strokeWidth="1" />
      <line x1={PAD.left} y1={PAD.top + PH} x2={PAD.left + PW} y2={PAD.top + PH} stroke="#334155" strokeWidth="1" />

      <text x={PAD.left - 3} y={PAD.top + 5} fill="#64748b" fontSize="10" textAnchor="end">{yHi.toFixed(1)}</text>
      <text x={PAD.left - 3} y={PAD.top + PH + 1} fill="#64748b" fontSize="10" textAnchor="end">{yLo.toFixed(1)}</text>
      <text x={7} y={PAD.top + PH / 2} fill="#64748b" fontSize="10" textAnchor="middle"
        transform={`rotate(-90,7,${PAD.top + PH / 2})`}>
        {metric === "speed" ? "m/s" : "m/s²"}
      </text>

      <text x={PAD.left} y={VH - 3} fill="#64748b" fontSize="10">{f0}</text>
      <text x={PAD.left + PW} y={VH - 3} fill="#64748b" fontSize="10" textAnchor="end">{f1}</text>
      <text x={PAD.left + PW / 2} y={PAD.top - 1} fill="#94a3b8" fontSize="10" textAnchor="middle">
        {metric === "speed" ? "Speed" : "Acceleration"}
      </text>

      {series.map(s => (
        <polyline key={s.id}
          points={s.points.map(([f, y]) => `${sx(f).toFixed(1)},${sy(y).toFixed(1)}`).join(" ")}
          fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
      ))}

      <line x1={cursorX} x2={cursorX} y1={PAD.top} y2={PAD.top + PH}
        stroke="#ffffff" strokeWidth="1" strokeOpacity="0.4" />

      {series.map((s, i) => (
        <g key={s.id} transform={`translate(${PAD.left + 4 + i * 80}, ${PAD.top + 6})`}>
          <line x1="0" x2="12" y1="5" y2="5" stroke={s.color} strokeWidth="2" />
          <text x="15" y="9" fill={s.color} fontSize="10">{s.id}</text>
        </g>
      ))}
    </svg>
  );
}

export function TelemetryChart({
  egoSamples,
  trackSamples,
  egoColor,
  frameRange,
  cursorFrame,
}: {
  egoSamples: TelemetrySample[];
  trackSamples: { token: string; samples: TelemetrySample[]; color: string }[];
  egoColor?: string;
  frameRange: [number, number];
  cursorFrame: number;
}) {
  const [f0, f1] = frameRange;

  const buildSeries = (metric: "speed" | "accel") => {
    const series: { id: string; color: string; points: [number, number][] }[] = [];

    const egoPoints = egoSamples
      .filter(s => s.frameIdx >= f0 && s.frameIdx <= f1)
      .map(s => [s.frameIdx, metric === "speed" ? s.speed : s.accel] as [number, number]);
    if (egoPoints.length > 0) {
      series.push({ id: "ego", color: egoColor ?? EGO_DEFAULT_COLOR, points: egoPoints });
    }

    for (const track of trackSamples.slice(0, 2)) {
      const points = track.samples
        .filter(s => s.frameIdx >= f0 && s.frameIdx <= f1)
        .map(s => [s.frameIdx, metric === "speed" ? s.speed : s.accel] as [number, number]);
      if (points.length === 0) continue;
      series.push({ id: track.token.slice(0, 8), color: track.color, points });
    }

    return series;
  };

  return (
    <div className="flex h-full gap-2 justify-center">
      <div className="h-full aspect-[4/3]">
        <SingleChart series={buildSeries("speed")} frameRange={frameRange} cursorFrame={cursorFrame} metric="speed" />
      </div>
      <div className="h-full aspect-[4/3]">
        <SingleChart series={buildSeries("accel")} frameRange={frameRange} cursorFrame={cursorFrame} metric="accel" />
      </div>
    </div>
  );
}
