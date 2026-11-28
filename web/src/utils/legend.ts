import type { LayerLegend } from "../types";

export type Stops = [number, string][];

const hex = (c: [number, number, number]) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");

/** legend จาก /layers (stops แบบ [t 0..1, [r,g,b]]) → stops แบบ [ค่าจริง, "#hex"] */
export function normStops(lg?: LayerLegend): Stops | null {
  if (!lg?.stops) return null;
  return lg.stops.map(([t, c]) => (Array.isArray(c) ? [Math.round((lg.min + t * (lg.max - lg.min)) * 100) / 100, hex(c)] : [t, c]));
}

/** แถบสี legend: "min [gradient] max" */
export function legendHtml(stops: Stops, unit = "") {
  const g = `linear-gradient(90deg, ${stops.map(([, c]) => c).join(",")})`;
  return `<span>${stops[0][0]}${unit}</span><span class="bar" style="background:${g}"></span><span>${stops[stops.length - 1][0]}${unit}</span>`;
}
