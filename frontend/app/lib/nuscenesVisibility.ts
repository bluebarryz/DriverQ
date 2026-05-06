/**
 * nuScenes stores annotation visibility in visibility.json as v0-40, v40-60,
 * v60-80, v80-100. Some code paths use numeric "1"-"4"; support both.
 */
export type VisibilityBadge = { text: string; bg: string; fg: string };

const BADGE: Record<
  "high" | "medHigh" | "med" | "low" | "unknown",
  VisibilityBadge & { bar: string }
> = {
  high: { text: "80-100%", bg: "#14532d", fg: "#86efac", bar: "#22c55e" },
  medHigh: { text: "60-80%", bg: "#365314", fg: "#bef264", bar: "#84cc16" },
  med: { text: "40-60%", bg: "#713f12", fg: "#fde68a", bar: "#eab308" },
  low: { text: "0-40%", bg: "#7f1d1d", fg: "#fca5a5", bar: "#ef4444" },
  unknown: { text: "-", bg: "#334155", fg: "#94a3b8", bar: "#334155" },
};

function tier(level: string | null | undefined): keyof typeof BADGE {
  const l = (level ?? "").trim();
  switch (l) {
    case "4":
    case "v80-100":
      return "high";
    case "3":
    case "v60-80":
      return "medHigh";
    case "2":
    case "v40-60":
      return "med";
    case "1":
    case "v0-40":
      return "low";
    default:
      return "unknown";
  }
}

export function nuscenesVisibilityBadge(level: string | null | undefined): VisibilityBadge {
  const t = BADGE[tier(level)];
  return { text: t.text, bg: t.bg, fg: t.fg };
}

export function nuscenesVisibilityBarColor(level: string | null | undefined): string {
  return BADGE[tier(level)].bar;
}
