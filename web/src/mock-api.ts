import type { ApiResult, BuildingsGeoJson } from "./types";

function mulberry32(a: number) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

const hrand = (key: string) => mulberry32(hashStr(key))();
function hnorm(key: string): number {
  const r = mulberry32(hashStr(key));
  const u = Math.max(r(), 1e-9);
  const v = r();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
const round = (v: number | null | undefined, d = 2): number | null =>
  v == null || Number.isNaN(v) ? null : Math.round(v * 10 ** d) / 10 ** d;
const pad = (n: number, w = 2) => String(n).padStart(w, "0");
const gauss = (x: number, y: number, cx: number, cy: number, r: number) =>
  Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * r * r));

function parseLocal(s: string): number {
  if (!s) return NaN;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(s.trim());
  if (!m) return NaN;
  let t = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  if (m[7]) {
    let off = 0;
    if (m[7] !== "Z") {
      const sg = m[7][0] === "-" ? -1 : 1;
      const hh = +m[7].slice(1, 3);
      const mm = +m[7].slice(-2);
      off = sg * (hh * 60 + mm);
    }
    t = t - off * 60000 + 7 * 3600000;
  }
  return t;
}

function fmtLocal(t: number): string {
  const d = new Date(t);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}
const fmtUtc = (t: number) => fmtLocal(t - 7 * 3600000);
const MIN = 60000;
const HOUR = 3600000;
const DAY = 86400000;

export const AOI = { west: 100.555, north: 13.9, east: 0, south: 0 };
export const D_LON = 90 / 108070;
export const D_LAT = 90 / 110600;
export const COLS = 48;
export const ROWS = 42;
AOI.east = AOI.west + COLS * D_LON;
AOI.south = AOI.north - ROWS * D_LAT;

const cellId = (c: number, r: number) => `c${pad(c, 4)}_r${pad(r, 4)}`;
export function cellOf(lon: number | null, lat: number | null): string | null {
  if (lon == null || lat == null) return null;
  const c = Math.floor((lon - AOI.west) / D_LON);
  const r = Math.floor((AOI.north - lat) / D_LAT);
  if (c < 0 || r < 0 || c >= COLS || r >= ROWS) return null;
  return cellId(c, r);
}

function parseCell(id: string): { c: number; r: number } | null {
  const m = /^c(\d{4})_r(\d{4})$/.exec(id || "");
  if (!m) return null;
  const c = +m[1];
  const r = +m[2];
  if (c >= COLS || r >= ROWS) return null;
  return { c, r };
}

function cellPolygon(c: number, r: number) {
  const w = AOI.west + c * D_LON;
  const e = w + D_LON;
  const n = AOI.north - r * D_LAT;
  const s = n - D_LAT;
  const f = (v: number) => Math.round(v * 1e6) / 1e6;
  return {
    type: "Polygon" as const,
    coordinates: [[[f(w), f(n)], [f(e), f(n)], [f(e), f(s)], [f(w), f(s)], [f(w), f(n)]] as [number, number][]],
  };
}

const PARKS = [
  [100.564, 13.886, 0.0035],
  [100.585, 13.872, 0.003],
  [100.592, 13.893, 0.0022],
  [100.559, 13.87, 0.002],
];
const DENSE = [
  [100.577, 13.889, 0.0055],
  [100.562, 13.874, 0.004],
  [100.588, 13.882, 0.0035],
];
const ROAD_LON = 100.5725;
const ROAD_LAT = 13.8855;

function smooth(x: number, y: number, seed: number): number {
  const r = mulberry32(seed);
  let s = 0;
  for (let i = 0; i < 4; i++) {
    const fx = (r() * 2 + 1) * 320;
    const fy = (r() * 2 + 1) * 320;
    const p1 = r() * 6.283;
    const p2 = r() * 6.283;
    s += Math.sin(x * fx + p1) * Math.cos(y * fy + p2);
  }
  return s / 2;
}

const roadness = (lon: number, lat: number) =>
  Math.max(Math.exp(-Math.abs(lon - ROAD_LON) / 0.0009), Math.exp(-Math.abs(lat - ROAD_LAT) / 0.0009));

function staticField(lon: number, lat: number) {
  let green = 0;
  let dense = 0;
  for (const [x, y, r] of PARKS) green += gauss(lon, lat, x, y, r);
  for (const [x, y, r] of DENSE) dense += gauss(lon, lat, x, y, r);
  dense += 0.55 * roadness(lon, lat);
  return { green: clamp(green, 0, 1), dense: clamp(dense, 0, 1.2) };
}

interface PeriodDef {
  period: string;
  period_type: string;
  date_start: string;
  date_end: string;
  base: number;
  seed: number;
  sat_period?: string;
}

const PERIODS: PeriodDef[] = [
  { period: "season_2025_03-05", period_type: "season", date_start: "2025-03-01", date_end: "2025-05-31", base: 37.5, seed: 11 },
  { period: "scene_2025-04-14", period_type: "scene", date_start: "2025-04-14", date_end: "2025-04-14", base: 39.2, seed: 12 },
  { period: "scene_2025-03-29", period_type: "scene", date_start: "2025-03-29", date_end: "2025-03-29", base: 36.4, seed: 13 },
  { period: "season_2024_03-05", period_type: "season", date_start: "2024-03-01", date_end: "2024-05-31", base: 36.8, seed: 14 },
  { period: "scene_2024-04-27", period_type: "scene", date_start: "2024-04-27", date_end: "2024-04-27", base: 38.6, seed: 15 },
];
const MONTH_PERIODS = [{ period: "month_2026-11", period_type: "month", sat_period: "season_2025_03-05" }];

function findPeriod(p?: string): PeriodDef | null {
  if (!p) return PERIODS[0];
  const a = PERIODS.find((x) => x.period === p);
  if (a) return a;
  const m = MONTH_PERIODS.find((x) => x.period === p);
  if (m) {
    const baseP = PERIODS.find((x) => x.period === m.sat_period)!;
    return { ...baseP, period: m.period, period_type: "month", sat_period: m.sat_period };
  }
  return null;
}

function satAt(lon: number, lat: number, P: PeriodDef) {
  const { green, dense } = staticField(lon, lat);
  const x = lon - AOI.west;
  const y = lat - AOI.south;
  const n1 = smooth(x, y, P.seed * 7 + 1);
  const n2 = smooth(x, y, P.seed * 7 + 2);
  const n3 = smooth(x, y, 99);
  const ndvi = clamp(0.18 + 0.58 * green - 0.12 * dense + 0.06 * n1, -0.1, 0.85);
  const ndbi = clamp(-0.04 + 0.32 * dense - 0.3 * green + 0.05 * n2, -0.45, 0.45);
  const px = hnorm("px" + Math.round(lon * 36000) + "," + Math.round(lat * 36000) + P.seed);
  const lst = P.base + 5.0 * dense - 5.0 * green + 1.6 * n3 + 0.7 * n1 + 1.1 * px;
  return { lst, ndvi, ndbi };
}

function buildingsAt(c: number, r: number) {
  const lon = AOI.west + (c + 0.5) * D_LON;
  const lat = AOI.north - (r + 0.5) * D_LAT;
  const { green, dense } = staticField(lon, lat);
  const k = `${c},${r}`;
  const builtup = clamp(18 + 62 * dense - 28 * green + 9 * hnorm("b" + k), 0, 100);
  const count = Math.max(0, Math.round(builtup / 4 + 3 * hnorm("n" + k)));
  const floors = count ? clamp(1.6 + 3.5 * dense + 0.6 * hnorm("f" + k), 1, 12) : null;
  const in_cover = hrand("cov" + k) < 0.86 ? 1 : 0;
  return {
    builtup_pct: round(builtup, 1),
    building_count: count,
    floors_mean: round(floors, 1),
    far: round(count && floors != null ? (builtup / 100) * floors : 0, 2),
    in_cover,
    bldg_source: in_cover ? "bma" : "open_buildings",
  };
}

interface MockDevice {
  device_id: string;
  device_type: "Mobile" | "Stationary";
  site_name: string | null;
  fixed_lat: number | null;
  fixed_lon: number | null;
  hours: number[] | null;
  site_off?: number;
}

const DEVICES: MockDevice[] = [
  { device_id: "IOTW-A001", device_type: "Mobile", site_name: null, fixed_lat: null, fixed_lon: null, hours: [8, 9, 10, 11] },
  { device_id: "IOTW-A002", device_type: "Mobile", site_name: null, fixed_lat: null, fixed_lon: null, hours: [15, 16, 17] },
  { device_id: "IOTW-S001", device_type: "Stationary", site_name: "สวนสาธารณะ (mock)", fixed_lat: 13.8861, fixed_lon: 100.5641, hours: null, site_off: -1.1 },
  { device_id: "IOTW-S002", device_type: "Stationary", site_name: "ริมถนนใหญ่ (mock)", fixed_lat: 13.8889, fixed_lon: 100.5727, hours: null, site_off: 1.3 },
];

const ROUTES: Record<string, [number, number][]> = {
  "IOTW-A001": [[100.56, 13.872], [100.5655, 13.8845], [100.5725, 13.889], [100.58, 13.893], [100.588, 13.886], [100.5845, 13.875], [100.574, 13.87], [100.56, 13.872]],
  "IOTW-A002": [[100.57, 13.896], [100.578, 13.889], [100.5725, 13.878], [100.565, 13.868], [100.582, 13.868], [100.59, 13.88], [100.57, 13.896]],
};

const DATA_FIRST_DAY = parseLocal("2026-11-14T00:00:00");
const MOCK_NOW = parseLocal("2026-11-21T12:10:00");
const LATEST_HOUR = parseLocal("2026-11-21T11:00:00");

function pointAlong(route: [number, number][], frac: number): [number, number] {
  const seg: number[] = [];
  let total = 0;
  for (let i = 1; i < route.length; i++) {
    const d = Math.hypot(route[i][0] - route[i - 1][0], route[i][1] - route[i - 1][1]);
    seg.push(d);
    total += d;
  }
  let want = (((frac % 1) + 1) % 1) * total;
  for (let i = 0; i < seg.length; i++) {
    if (want <= seg[i]) {
      const k = want / seg[i];
      return [route[i][0] + k * (route[i + 1][0] - route[i][0]), route[i][1] + k * (route[i + 1][1] - route[i][1])];
    }
    want -= seg[i];
  }
  return route[route.length - 1];
}

const tempBase = (h: number) => 28.3 + 4.2 * Math.sin((2 * Math.PI * (h - 9)) / 24);

function devicePos(dev: MockDevice, t: number): [number, number] {
  if (dev.device_type === "Stationary") return [dev.fixed_lon!, dev.fixed_lat!];
  return pointAlong(ROUTES[dev.device_id], (t % HOUR) / HOUR);
}

function isActive(dev: MockDevice, t: number): boolean {
  if (t < DATA_FIRST_DAY || t >= MOCK_NOW) return false;
  if (!dev.hours) return true;
  return dev.hours.includes(new Date(t).getUTCHours());
}

function sensorAt(dev: MockDevice, lon: number, lat: number, t: number, key: string) {
  const dt = new Date(t);
  const h = dt.getUTCHours() + dt.getUTCMinutes() / 60 + dt.getUTCSeconds() / 3600;
  const day = Math.floor(t / DAY);
  const sat = satAt(lon, lat, PERIODS[0]);
  const { green, dense } = staticField(lon, lat);
  const siteAmp = dev.site_off ? dev.site_off * Math.max(0, Math.sin((Math.PI * (h - 7)) / 12)) : 0;
  const temp = tempBase(h) + 0.22 * (sat.lst - 37.5) + siteAmp + 0.7 * hnorm("day" + day) + 0.25 * hnorm("t" + key);
  const hum = clamp(84 - 2.3 * (temp - 25) + 1.5 * hnorm("h" + key), 30, 99);
  const spl = 55 + 17 * roadness(lon, lat) + 4 * dense + (h > 7 && h < 20 ? 3 : -4) + 2.2 * hnorm("s" + key);
  const sun = Math.max(0, Math.sin((Math.PI * (h - 6.2)) / 12.4));
  const lux = sun * 72000 * (1 - 0.55 * green) * (0.65 + 0.35 * hrand("cl" + day + Math.floor(h))) + 30 + 40 * hrand("l" + key);
  return { temp, hum, spl, dba: spl - 5.8 + 0.8 * hnorm("a" + key), lux };
}

function minuteRow(dev: MockDevice, t: number): Record<string, unknown> {
  const key = dev.device_id + t;
  let [lon, lat] = devicePos(dev, t);
  let gpsOk = true;
  if (dev.device_type === "Mobile") {
    lon += 0.00003 * hnorm("x" + key);
    lat += 0.00003 * hnorm("y" + key);
    gpsOk = hrand("g" + key) > 0.03;
  }
  const v = sensorAt(dev, lon, lat, t, key);
  const n = hrand("nr" + key) < 0.9 ? 60 : 55 + Math.floor(hrand("nr2" + key) * 5);
  return {
    minute_local: fmtLocal(t),
    minute_utc: fmtUtc(t),
    n_rows: n,
    db_spl_mean: round(v.spl, 1),
    db_spl_max: round(v.spl + 5 + 2 * hrand("mx" + key), 1),
    db_spl_min: round(v.spl - 4 - 2 * hrand("mn" + key), 1),
    dba_mean: round(v.dba, 1),
    temp_c_mean: round(v.temp, 2),
    humidity_pct_mean: round(v.hum, 1),
    lux_mean: round(v.lux, 0),
    lat: gpsOk ? round(lat, 6) : null,
    lon: gpsOk ? round(lon, 6) : null,
    cell_id: gpsOk ? cellOf(lon, lat) : null,
  };
}

const COLS_1MIN = ["minute_local", "minute_utc", "n_rows", "db_spl_mean", "db_spl_max", "db_spl_min", "dba_mean", "temp_c_mean", "humidity_pct_mean", "lux_mean", "lat", "lon", "cell_id"];
const COLS_1S = ["device_type", "session", "record_id", "ts_local", "ts_utc", "db_spl", "dba", "lux", "temp_c", "humidity_pct", "lat", "lon", "alt_m", "speed_kmh", "satellites", "hdop"];
const publishedUntil = () => LATEST_HOUR + HOUR;

function minuteRows(dev: MockDevice, from: number, to: number) {
  const out: Record<string, unknown>[] = [];
  const end = Math.min(to, publishedUntil());
  for (let t = Math.ceil(from / MIN) * MIN; t < end; t += MIN) {
    if (isActive(dev, t)) out.push(minuteRow(dev, t));
  }
  return out;
}

function secondRows(dev: MockDevice, from: number, to: number) {
  const out: unknown[][] = [];
  const end = Math.min(to, publishedUntil());
  const session = 1000 + (Math.floor(from / DAY) % 1000);
  for (let t = Math.ceil(from / 1000) * 1000; t < end; t += 1000) {
    if (!isActive(dev, t)) continue;
    const key = dev.device_id + "s" + t;
    if (hrand("drop" + key) < 0.01) continue;
    const [lon, lat] = devicePos(dev, t);
    const v = sensorAt(dev, lon, lat, t, key);
    const rec = Math.floor((t % DAY) / 1000);
    out.push([
      dev.device_type, session, rec, fmtLocal(t), fmtUtc(t),
      round(v.spl, 1), round(v.dba, 1), round(v.lux, 0), round(v.temp, 1), round(v.hum, 1),
      round(lat, 6), round(lon, 6), round(4 + hnorm("al" + key), 1),
      dev.device_type === "Mobile" ? round(3.8 + 0.6 * hnorm("sp" + key), 1) : 0,
      7 + Math.floor(hrand("sv" + key) * 5), round(0.8 + 0.4 * hrand("hd" + key), 2),
    ]);
  }
  return out;
}

const IOT_VARS = ["air_temp_mean", "humidity_mean", "lux_mean", "db_spl_mean", "n_iot_minutes"];
const SAT_VARS = ["lst_mean", "lst_p90", "ndvi_mean", "ndbi_mean"];
const BLD_VARS = ["builtup_pct", "building_count", "floors_mean", "far", "in_cover", "bldg_source"];
const ALLOWED_VARS = new Set([...SAT_VARS, ...BLD_VARS, ...IOT_VARS]);
const DEFAULT_VARS = ["lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct"];
const MIN_IOT_MINUTES = 5;

let _iotCache: Record<"all" | "window", Record<string, Record<string, number | undefined>>> | null = null;
function iotByCell() {
  if (_iotCache) return _iotCache;
  const acc: Record<"all" | "window", Record<string, { n: number; t: number; h: number; l: number; s: number }>> = { all: {}, window: {} };
  for (const dev of DEVICES.filter((d) => d.device_type === "Mobile")) {
    for (const row of minuteRows(dev, DATA_FIRST_DAY, MOCK_NOW)) {
      const cid = row.cell_id as string | null;
      if (!cid) continue;
      const ml = row.minute_local as string;
      const hm = +ml.slice(11, 13) * 60 + +ml.slice(14, 16);
      const sets: ("all" | "window")[] = hm >= 570 && hm < 690 ? ["all", "window"] : ["all"];
      for (const s of sets) {
        const a = (acc[s][cid] ||= { n: 0, t: 0, h: 0, l: 0, s: 0 });
        a.n++;
        a.t += row.temp_c_mean as number;
        a.h += row.humidity_pct_mean as number;
        a.l += row.lux_mean as number;
        a.s += row.db_spl_mean as number;
      }
    }
  }
  const out: Record<"all" | "window", Record<string, Record<string, number | undefined>>> = { all: {}, window: {} };
  for (const s of ["all", "window"] as const) {
    for (const id in acc[s]) {
      const a = acc[s][id];
      out[s][id] =
        a.n < MIN_IOT_MINUTES
          ? { n_iot_minutes: a.n }
          : {
              air_temp_mean: round(a.t / a.n, 2) ?? undefined,
              humidity_mean: round(a.h / a.n, 1) ?? undefined,
              lux_mean: round(a.l / a.n, 0) ?? undefined,
              db_spl_mean: round(a.s / a.n, 1) ?? undefined,
              n_iot_minutes: a.n,
            };
    }
  }
  return (_iotCache = out);
}

const _cellCache: Record<string, Record<string, unknown>[]> = {};
function cellRows(P: PeriodDef, match: "all" | "window") {
  const key = P.period + "|" + match;
  if (_cellCache[key]) return _cellCache[key];
  const iot = iotByCell()[match];
  const rows: Record<string, unknown>[] = [];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      let lst = 0, ndvi = 0, ndbi = 0, mx = -99;
      for (let i = 0; i < 3; i++) {
        for (let j = 0; j < 3; j++) {
          const s = satAt(AOI.west + (c + (i + 0.5) / 3) * D_LON, AOI.north - (r + (j + 0.5) / 3) * D_LAT, P);
          lst += s.lst;
          ndvi += s.ndvi;
          ndbi += s.ndbi;
          mx = Math.max(mx, s.lst);
        }
      }
      const id = cellId(c, r);
      const io = iot[id] || {};
      rows.push({
        cell_id: id,
        period: P.period,
        match_type: match,
        sat_period: P.sat_period || P.period,
        lon: round(AOI.west + (c + 0.5) * D_LON, 6),
        lat: round(AOI.north - (r + 0.5) * D_LAT, 6),
        lst_mean: round(lst / 9, 2),
        lst_p90: round(mx, 2),
        ndvi_mean: round(ndvi / 9, 3),
        ndbi_mean: round(ndbi / 9, 3),
        ...buildingsAt(c, r),
        air_temp_mean: io.air_temp_mean ?? null,
        humidity_mean: io.humidity_mean ?? null,
        lux_mean: io.lux_mean ?? null,
        db_spl_mean: io.db_spl_mean ?? null,
        n_iot_minutes: io.n_iot_minutes ?? 0,
      });
    }
  }
  return (_cellCache[key] = rows);
}

export const LEGENDS: Record<string, { column: string; min: number; max: number; unit: string; stops: [number, string][] }> = {
  lst: { column: "lst", min: 30, max: 46, unit: "°C", stops: [[30, "#2c7bb6"], [34, "#abd9e9"], [38, "#ffffbf"], [42, "#fdae61"], [46, "#d7191c"]] },
  ndvi: { column: "ndvi", min: -0.1, max: 0.8, unit: "", stops: [[-0.1, "#8c510a"], [0.15, "#d8b365"], [0.3, "#f6e8c3"], [0.5, "#a1d76a"], [0.8, "#1a9641"]] },
  ndbi: { column: "ndbi", min: -0.4, max: 0.4, unit: "", stops: [[-0.4, "#4d9221"], [0, "#f7f7f7"], [0.4, "#c51b7d"]] },
  building_density: { column: "builtup_pct", min: 0, max: 100, unit: "%", stops: [[0, "#fff7ec"], [30, "#fdbb84"], [65, "#d7301f"], [100, "#7f0000"]] },
};

function hex2rgb(h: string): [number, number, number] {
  return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}
function rampColor(stops: [number, string][], v: number): [number, number, number] {
  if (v <= stops[0][0]) return hex2rgb(stops[0][1]);
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const k = (v - stops[i - 1][0]) / (stops[i][0] - stops[i - 1][0]);
      const a = hex2rgb(stops[i - 1][1]);
      const b = hex2rgb(stops[i][1]);
      return [Math.round(a[0] + k * (b[0] - a[0])), Math.round(a[1] + k * (b[1] - a[1])), Math.round(a[2] + k * (b[2] - a[2]))];
    }
  }
  return hex2rgb(stops[stops.length - 1][1]);
}

const _pngCache: Record<string, string> = {};
function makePng(P: PeriodDef, layer: "lst" | "ndvi" | "ndbi" | "building_density"): string {
  const key = P.period + "|" + layer;
  if (_pngCache[key]) return _pngCache[key];
  const per = layer === "building_density" ? 1 : 3;
  const W = COLS * per;
  const H = ROWS * per;
  const cv = document.createElement("canvas");
  cv.width = W;
  cv.height = H;
  const ctx = cv.getContext("2d")!;
  const img = ctx.createImageData(W, H);
  const stops = LEGENDS[layer].stops;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const v =
        layer === "building_density"
          ? (buildingsAt(x, y).builtup_pct as number)
          : satAt(AOI.west + ((x + 0.5) / per) * D_LON, AOI.north - ((y + 0.5) / per) * D_LAT, P)[layer];
      const [r, g, b] = rampColor(stops, v);
      const o = (y * W + x) * 4;
      img.data[o] = r;
      img.data[o + 1] = g;
      img.data[o + 2] = b;
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return (_pngCache[key] = cv.toDataURL("image/png"));
}

const CORNERS = () =>
  [[AOI.west, AOI.north], [AOI.east, AOI.north], [AOI.east, AOI.south], [AOI.west, AOI.south]].map((p) => p.map((v) => round(v, 6)!));

// ---------------------------------------------------------------- Statistics helpers
const mean = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
const sd = (a: number[]) => {
  const m = mean(a);
  return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - 1));
};
const pct = (a: number[], p: number) => {
  const s = [...a].sort((x, y) => x - y);
  const i = (s.length - 1) * p;
  const lo = Math.floor(i);
  return s[lo] + (s[Math.ceil(i)] - s[lo]) * (i - lo);
};
function pearson(x: number[], y: number[]) {
  const mx = mean(x), my = mean(y);
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < x.length; i++) {
    sxy += (x[i] - mx) * (y[i] - my);
    sxx += (x[i] - mx) ** 2;
    syy += (y[i] - my) ** 2;
  }
  return sxy / Math.sqrt(sxx * syy);
}
function phi(z: number) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp((-z * z) / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z > 0 ? 1 - p : p;
}
const pFromT = (t: number) => clamp(2 * (1 - phi(Math.abs(t))), 0, 1);
const pFromR = (r: number, n: number) => pFromT((r * Math.sqrt(n - 2)) / Math.sqrt(Math.max(1e-12, 1 - r * r)));

function solve(A: number[][], b: number[]): number[] {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let i = 0; i < n; i++) {
    let p = i;
    for (let k = i + 1; k < n; k++) if (Math.abs(M[k][i]) > Math.abs(M[p][i])) p = k;
    [M[i], M[p]] = [M[p], M[i]];
    for (let k = i + 1; k < n; k++) {
      const f = M[k][i] / M[i][i];
      for (let j = i; j <= n; j++) M[k][j] -= f * M[i][j];
    }
  }
  const x = Array(n).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    let s = M[i][n];
    for (let j = i + 1; j < n; j++) s -= M[i][j] * x[j];
    x[i] = s / M[i][i];
  }
  return x;
}
function inv(A: number[][]): number[][] {
  return A.map((_, i) => solve(A, A.map((__, j) => (i === j ? 1 : 0)))).map((col, i, cols) => cols.map((c) => c[i]));
}
function ols(y: number[], Xs: number[][]) {
  const n = y.length, k = Xs.length + 1;
  const X = y.map((_, i) => [1, ...Xs.map((x) => x[i])]);
  const XtX = Array.from({ length: k }, (_, a) => Array.from({ length: k }, (_, b) => X.reduce((s, r) => s + r[a] * r[b], 0)));
  const Xty = Array.from({ length: k }, (_, a) => X.reduce((s, r, i) => s + r[a] * y[i], 0));
  const beta = solve(XtX, Xty);
  const yhat = X.map((r) => r.reduce((s, v, j) => s + v * beta[j], 0));
  const res = y.map((v, i) => v - yhat[i]);
  const my = mean(y);
  const sst = y.reduce((s, v) => s + (v - my) ** 2, 0);
  const sse = res.reduce((s, v) => s + v * v, 0);
  const s2 = sse / (n - k);
  const C = inv(XtX);
  const se = beta.map((_, j) => Math.sqrt(s2 * C[j][j]));
  return { beta, se, r2: 1 - sse / sst, adj: 1 - (1 - (1 - sse / sst)) * ((n - 1) / (n - k)), res, n };
}

function regress(rows: Record<string, unknown>[], yName: string, xNames: string[], model: string) {
  const use = rows.filter((r) => [yName, ...xNames].every((k) => r[k] != null));
  if (use.length < xNames.length + 3) {
    return { model, formula: `${yName} ~ ${xNames.join(" + ")}`, n: use.length, r2: null, terms: [], note: "ข้อมูลไม่พอ", _res: [] as number[], _use: [] as Record<string, unknown>[] };
  }
  const y = use.map((r) => r[yName] as number);
  const Xs = xNames.map((k) => use.map((r) => r[k] as number));
  const fit = ols(y, Xs);
  const sy = sd(y);
  const terms = xNames.map((k, j) => {
    const others = xNames.filter((o) => o !== k);
    const vif = others.length ? 1 / (1 - ols(Xs[j], others.map((o) => use.map((r) => r[o] as number))).r2) : 1;
    return {
      term: k,
      coef: round(fit.beta[j + 1], 4)!,
      std_coef: round((fit.beta[j + 1] * sd(Xs[j])) / sy, 3)!,
      p_value: round(pFromT(fit.beta[j + 1] / fit.se[j + 1]), 4)!,
      vif: round(vif, 2)!,
    };
  });
  return { model, formula: `${yName} ~ ${xNames.join(" + ")}`, n: use.length, r2: round(fit.r2, 3), adj_r2: round(fit.adj, 3), terms, _res: fit.res, _use: use };
}

function neighbors(rows: Record<string, unknown>[], radiusCells: number): number[][] {
  const idx: Record<string, number> = {};
  rows.forEach((r, i) => (idx[r.cell_id as string] = i));
  return rows.map((r) => {
    const pc = parseCell(r.cell_id as string)!;
    const out: number[] = [];
    for (let dc = -radiusCells; dc <= radiusCells; dc++) {
      for (let dr = -radiusCells; dr <= radiusCells; dr++) {
        if (dc * dc + dr * dr > radiusCells * radiusCells) continue;
        const j = idx[cellId(pc.c + dc, pc.r + dr)];
        if (j != null) out.push(j);
      }
    }
    return out;
  });
}

function moran(vals: number[], nb: number[][]) {
  const n = vals.length, m = mean(vals);
  let num = 0, W = 0, den = 0;
  for (let i = 0; i < n; i++) {
    den += (vals[i] - m) ** 2;
    for (const j of nb[i]) {
      if (j !== i) {
        num += (vals[i] - m) * (vals[j] - m);
        W++;
      }
    }
  }
  const I = (n / W) * (num / den);
  const EI = -1 / (n - 1);
  const z = (I - EI) / Math.sqrt((1 / W) * 2);
  return { I, p: pFromT(z) };
}

const _anCache: Record<string, Record<string, unknown>> = {};
function analytics(P: PeriodDef, match: "all" | "window") {
  const key = P.period + "|" + match;
  if (_anCache[key]) return _anCache[key];
  const all = cellRows(P, match).filter((r) => r.in_cover === 1);
  const VARS = ["lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct", "air_temp_mean", "db_spl_mean"];

  const summary = VARS.map((v) => {
    const a = all.map((r) => r[v] as number | null).filter((x): x is number => x != null);
    return { variable: v, n: a.length, mean: round(mean(a), 3), sd: round(sd(a), 3), min: round(Math.min(...a), 3), max: round(Math.max(...a), 3), p90: round(pct(a, 0.9), 3) };
  });

  const r: number[][] = [], p: number[][] = [], n: number[][] = [];
  for (const a of VARS) {
    const rr: number[] = [], pp: number[] = [], nn: number[] = [];
    for (const b of VARS) {
      const pr = all.filter((x) => x[a] != null && x[b] != null);
      const v = a === b ? 1 : pearson(pr.map((x) => x[a] as number), pr.map((x) => x[b] as number));
      rr.push(round(v, 3)!);
      pp.push(a === b ? 0 : round(pFromR(v, pr.length), 4)!);
      nn.push(pr.length);
    }
    r.push(rr); p.push(pp); n.push(nn);
  }

  const s3 = regress(all, "lst_mean", ["ndvi_mean", "ndbi_mean", "builtup_pct"], "s3");
  const s4 = regress(all, "air_temp_mean", ["lst_mean", "ndvi_mean", "builtup_pct"], "s4");

  const tot = s3.terms.reduce((s, t) => s + Math.abs(t.std_coef), 0);
  const importance = s3.terms.map((t) => ({
    feature: t.term,
    importance: round((Math.abs(t.std_coef) / tot) * (0.9 + 0.2 * hrand(P.period + t.term)), 3),
    std: round(0.01 + 0.02 * hrand("sd" + t.term), 3),
  }));

  const nb = neighbors(all, 3);
  const x = all.map((row) => row.lst_mean as number);
  const N = x.length, xm = mean(x), S = Math.sqrt(x.reduce((s, v) => s + v * v, 0) / N - xm * xm);
  const hotspot = all.map((row, i) => {
    const w = nb[i].length;
    const sx = nb[i].reduce((s, j) => s + x[j], 0);
    const z = (sx - xm * w) / (S * Math.sqrt((N * w - w * w) / (N - 1)));
    const pv = pFromT(z);
    const cls = pv < 0.01 ? (z > 0 ? "hot_99" : "cold_99") : pv < 0.05 ? (z > 0 ? "hot_95" : "cold_95") : "not_significant";
    return { cell_id: row.cell_id, gi_z: round(z, 2), gi_p: round(pv, 4), class: cls };
  });

  const nb1 = neighbors(all, 1);
  const mi1 = moran(x, nb1);
  const resIdx: Record<string, number> = {};
  s3._use.forEach((u, i) => (resIdx[u.cell_id as string] = s3._res[i]));
  const mi2 = moran(all.map((row) => resIdx[row.cell_id as string] ?? 0), nb1);

  const sorted = [...all].sort((a, b) => (b.ndvi_mean as number) - (a.ndvi_mean as number));
  const k = Math.floor(sorted.length / 3);
  const grp = ([["green", sorted.slice(0, k)], ["medium", sorted.slice(k, 2 * k)], ["dense", sorted.slice(2 * k)]] as const).map(([g, rows]) => {
    const l = rows.map((row) => row.lst_mean as number);
    const nd = rows.map((row) => row.ndvi_mean as number);
    return { group: g, n: rows.length, ndvi_range: [round(Math.min(...nd), 2)!, round(Math.max(...nd), 2)!], lst_mean: round(mean(l), 2)!, lst_sd: round(sd(l), 2)! };
  });

  const stations = DEVICES.filter((d) => d.device_type === "Stationary").map((d) => {
    const acc = Array.from({ length: 24 }, () => [0, 0]);
    for (let t = DATA_FIRST_DAY; t < LATEST_HOUR; t += 10 * MIN) {
      const row = minuteRow(d, t);
      const hh = new Date(t).getUTCHours();
      acc[hh][0] += row.temp_c_mean as number;
      acc[hh][1]++;
    }
    return { device_id: d.device_id, site_name: d.site_name, hours: acc.map(([s, c], hh) => ({ hour: hh, air_temp_mean: c ? round(s / c, 2) : null })) };
  });

  const strip = (m: typeof s3) => {
    const { _res, _use, ...rest } = m;
    return rest;
  };
  const base = { period: P.period, match };
  return (_anCache[key] = {
    summary: { ...base, rows: summary },
    correlation: { ...base, method: "pearson", variables: VARS, r, p_value: p, n },
    regression: { ...base, models: [strip(s3), strip(s4)] },
    importance: { ...base, model: "random_forest", trees: 300, random_seed: 42, method: "permutation", features: importance },
    hotspot: { ...base, variable: "lst_mean", distance_m: 300, cells: hotspot },
    spatial_autocorr: { ...base, rows: [{ target: "lst_mean", morans_i: round(mi1.I, 3), p_value: round(mi1.p, 4) }, { target: "s3_residual", morans_i: round(mi2.I, 3), p_value: round(mi2.p, 4) }] },
    groups: { ...base, by: "ndvi_mean", groups: grp, diff_dense_minus_green: round(grp[2].lst_mean - grp[0].lst_mean, 2), test: { name: "kruskal_wallis", p_value: 0.0001 } },
    station_diurnal: { ...base, stations },
  });
}
const ANALYTICS_NAMES = ["summary", "correlation", "regression", "importance", "hotspot", "spatial_autocorr", "groups", "station_diurnal"];

const ok = (body: unknown): ApiResult => ({ status: 200, body });
const err = (status: number, msg: string): ApiResult => ({ status, body: { error: msg } });

function needDevice(q: Record<string, string>): [MockDevice | null, ApiResult | null] {
  if (!q.device) return [null, err(400, "missing query parameter: device")];
  const d = DEVICES.find((x) => x.device_id === q.device);
  if (!d) return [null, err(404, `unknown device: ${q.device}`)];
  return [d, null];
}

function needRange(q: Record<string, string>, maxMs: number, label: string): [[number, number] | null, ApiResult | null] {
  if (!q.from || !q.to) return [null, err(400, "missing query parameter: from/to")];
  const f = parseLocal(q.from);
  const t = parseLocal(q.to);
  if (Number.isNaN(f) || Number.isNaN(t)) return [null, err(400, "invalid datetime, use ISO 8601")];
  if (t <= f) return [null, err(400, "to must be after from")];
  if (t - f > maxMs) return [null, err(400, `range too long: max ${label}`)];
  return [[f, t], null];
}

function lastBatch(d: MockDevice): number | null {
  let t = LATEST_HOUR;
  while (t >= DATA_FIRST_DAY && !isActive(d, t)) t -= HOUR;
  return t < DATA_FIRST_DAY ? null : t;
}

function route(path: string, q: Record<string, string>): ApiResult {
  if (path === "/devices") {
    return ok({
      devices: DEVICES.map((d) => {
        const t = lastBatch(d);
        return {
          device_id: d.device_id,
          device_type: d.device_type,
          site_name: d.site_name,
          fixed_lat: d.fixed_lat,
          fixed_lon: d.fixed_lon,
          latest_batch_id: t ? `${d.device_id}__${fmtLocal(t).slice(0, 10)}__${fmtLocal(t).slice(11, 13)}` : null,
          latest_hour_local: t ? fmtLocal(t) : null,
          published_at: t ? fmtUtc(t + HOUR + 5 * MIN + 31000) + "+00:00" : null,
        };
      }),
    });
  }
  if (path === "/latest") {
    const [d, e] = needDevice(q);
    if (e) return e;
    const t = lastBatch(d!);
    if (!t) return err(404, "no published data");
    const rows = minuteRows(d!, t, t + HOUR);
    return ok({ device: d!.device_id, row: rows[rows.length - 1] });
  }
  if (path === "/readings") {
    const [d, e] = needDevice(q);
    if (e) return e;
    const interval = q.interval || "1min";
    if (!["1s", "1min"].includes(interval)) return err(400, "interval must be 1s or 1min");
    const [rg, e2] = interval === "1s" ? needRange(q, 6 * HOUR, "6 hours") : needRange(q, 31 * DAY, "31 days");
    if (e2) return e2;
    const columns = interval === "1s" ? COLS_1S : COLS_1MIN;
    const rows = interval === "1s" ? secondRows(d!, rg![0], rg![1]) : minuteRows(d!, rg![0], rg![1]).map((o) => columns.map((c) => o[c]));
    return ok({ device: d!.device_id, interval, from: fmtLocal(rg![0]), to: fmtLocal(rg![1]), columns, rows });
  }
  if (path === "/quality") {
    const [d, e] = needDevice(q);
    if (e) return e;
    const [rg, e2] = needRange(q, 31 * DAY, "31 days");
    if (e2) return e2;
    const hours = [];
    for (let t = Math.ceil(rg![0] / HOUR) * HOUR; t < Math.min(rg![1], publishedUntil()); t += HOUR) {
      if (!isActive(d!, t)) continue;
      const k = d!.device_id + t;
      const bad = hrand("bad" + k) < 0.08;
      const expected = 3600;
      const missing = Math.floor(bad ? 150 + 400 * hrand("m2" + k) : 40 * hrand("m" + k));
      const dup = Math.floor(6 * hrand("du" + k));
      const inval = Math.floor(25 * hrand("iv" + k));
      hours.push({
        batch_id: `${d!.device_id}__${fmtLocal(t).slice(0, 10)}__${fmtLocal(t).slice(11, 13)}`,
        hour_local: fmtLocal(t),
        sessions: bad ? 2 : 1,
        expected_rows: expected,
        received_rows: expected - missing + dup,
        missing_rows: missing,
        duplicate_rows: dup,
        invalid_rows: inval,
        dropped_rows: dup + inval,
        loss_rate: round(missing / expected, 4),
      });
    }
    return ok({ device: d!.device_id, from: fmtLocal(rg![0]), to: fmtLocal(rg![1]), hours });
  }
  if (path === "/layers") {
    const P = q.period ? PERIODS.find((x) => x.period === q.period) : PERIODS[0];
    if (!P) return err(404, `period not published: ${q.period}`);
    const entry = (layer: "lst" | "ndvi" | "ndbi" | "building_density", P2: PeriodDef) => ({
      layer,
      url: makePng(P2, layer),
      url_expires_in_s: 3600,
      coordinates: CORNERS(),
      legend: LEGENDS[layer],
    });
    return ok({
      period: P.period,
      period_type: P.period_type,
      periods: PERIODS.map(({ period, period_type, date_start, date_end }) => ({ period, period_type, date_start, date_end })),
      layers: (["lst", "ndvi", "ndbi"] as const).map((l) => entry(l, P)),
      static_layers: [entry("building_density", PERIODS[0])],
    });
  }
  if (path === "/grid") {
    const P = findPeriod(q.period);
    if (!P) return err(404, `period not published: ${q.period}`);
    const match = (q.match || "all") as "all" | "window";
    if (!["all", "window"].includes(match)) return err(400, "match must be window or all");
    const vars = q.vars ? q.vars.split(",").map((s) => s.trim()).filter(Boolean) : DEFAULT_VARS;
    const bad = vars.filter((v) => !ALLOWED_VARS.has(v));
    if (bad.length) return err(400, `unknown vars: ${bad.join(",")}`);
    const features = cellRows(P, match).map((row) => {
      const pc = parseCell(row.cell_id as string)!;
      const props: Record<string, unknown> = { cell_id: row.cell_id };
      for (const v of vars) props[v] = row[v];
      return { type: "Feature", id: row.cell_id, geometry: cellPolygon(pc.c, pc.r), properties: props };
    });
    return ok({ type: "FeatureCollection", period: P.period, match, vars, features });
  }
  const mCell = /^\/grid\/([^/]+)$/.exec(path);
  if (mCell) {
    const pc = parseCell(mCell[1]);
    if (!pc) return err(404, `unknown cell: ${mCell[1]}`);
    const P = findPeriod(q.period);
    if (!P) return err(404, `period not published: ${q.period}`);
    const pick = (m: "all" | "window") => {
      const { cell_id, period, match_type, ...rest } = cellRows(P, m).find((x) => x.cell_id === mCell[1])!;
      return rest;
    };
    return ok({ cell_id: mCell[1], period: P.period, geometry: cellPolygon(pc.c, pc.r), match: { window: pick("window"), all: pick("all") } });
  }
  const mAn = /^\/analytics\/([^/]+)$/.exec(path);
  if (mAn) {
    if (!ANALYTICS_NAMES.includes(mAn[1])) return err(404, `unknown analytics: ${mAn[1]}`);
    const P = findPeriod(q.period) || PERIODS[0];
    const match = (q.match === "window" ? "window" : "all") as "all" | "window";
    return ok(analytics(P, match)[mAn[1]]);
  }
  return err(404, `not found: ${path}`);
}

export function handleMockApi(path: string, params?: Record<string, unknown>): Promise<ApiResult> {
  const q: Record<string, string> = {};
  for (const k in params || {}) {
    if (params![k] != null && params![k] !== "") q[k] = String(params![k]);
  }
  return new Promise((resolve) => {
    const athena = /^\/(latest|readings|quality|grid|analytics)/.test(path);
    const delay = (athena ? 120 : 50) + Math.random() * (athena ? 180 : 70);
    setTimeout(() => {
      try {
        resolve(route(path, q));
      } catch (e: unknown) {
        resolve(err(502, "mock error: " + (e instanceof Error ? e.message : String(e))));
      }
    }, delay);
  });
}

export class FakeLiveSocket {
  url: string;
  readyState = 0;
  binaryType: BinaryType = "arraybuffer";
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  private devices: MockDevice[];
  private session = 123456;
  private record = 4500;
  private listen: Record<string, { on: boolean; seq: number; lost: number }> = {};
  private timers: number[] = [];

  constructor(url: string) {
    this.url = url;
    const m = /device=([^&]+)/.exec(url);
    const want = m ? decodeURIComponent(m[1]) : "all";
    this.devices = DEVICES.filter((d) => want === "all" || d.device_id === want);
    window.setTimeout(() => {
      this.readyState = 1;
      this.onopen?.({});
      for (const d of this.devices) {
        this.emit({ t: "status", device_id: d.device_id, online: d.device_type === "Stationary" || d.device_id === "IOTW-A001", at: new Date().toISOString() });
      }
      this.timers.push(window.setInterval(() => this.tick(), 1000));
      this.timers.push(window.setInterval(() => this.audio(), 100));
      this.timers.push(window.setInterval(() => this.audioStats(), 10000));
    }, 250);
  }

  private emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }

  private tick() {
    const now = Date.now();
    const localMs = now + 7 * HOUR;
    this.record++;
    for (const d of this.devices) {
      if (!(d.device_type === "Stationary" || d.device_id === "IOTW-A001")) continue;
      const [lon, lat] =
        d.device_type === "Mobile"
          ? pointAlong(ROUTES[d.device_id], (((now / 1000) * 20) % 3600) / 3600)
          : [d.fixed_lon!, d.fixed_lat!];
      const v = sensorAt(d, lon, lat, localMs, d.device_id + now);
      this.emit({
        t: "reading",
        device_id: d.device_id,
        session: this.session,
        record_id: this.record,
        ts: fmtLocal(localMs).replace("T", " "),
        dba: round(v.dba, 1),
        db_spl: round(v.spl + 3 * Math.sin(now / 1700), 1),
        lux: round(v.lux, 0),
        temp_c: round(v.temp, 1),
        humidity_pct: round(v.hum, 1),
        lat: round(lat, 6),
        lon: round(lon, 6),
        gps_valid: 1,
        health: { heap_free: 81000 + Math.floor(Math.random() * 3000), rssi: -55 - Math.floor(Math.random() * 15), sd_errors: 0, backlog_rows: 0 },
        received_at: new Date(now).toISOString(),
      });
    }
  }

  private audio() {
    for (const id in this.listen) {
      const L = this.listen[id];
      if (!L.on) continue;
      if (Math.random() < 0.02) {
        L.seq++;
        L.lost++;
        continue;
      }
      const buf = new ArrayBuffer(804);
      const dv = new DataView(buf);
      dv.setUint8(0, 0x01);
      dv.setUint8(1, 1);
      dv.setUint16(2, 1600, true);
      dv.setUint32(4, this.session, true);
      dv.setUint32(8, L.seq++, true);
      const u8 = new Uint8Array(buf);
      for (let i = 12; i < 804; i++) u8[i] = (Math.random() * 256) | 0;
      this.onmessage?.({ data: buf });
    }
  }

  private audioStats() {
    for (const id in this.listen) {
      if (this.listen[id].on) {
        this.emit({ t: "audio_stats", device_id: id, lost: this.listen[id].lost });
        this.listen[id].lost = 0;
      }
    }
  }

  send(text: string) {
    const m = JSON.parse(text);
    if (m.t === "listen") this.listen[m.device_id] = { on: true, seq: 0, lost: 0 };
    if (m.t === "unlisten" && this.listen[m.device_id]) this.listen[m.device_id].on = false;
  }

  close() {
    this.timers.forEach(clearInterval);
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
}
let _bldgCache: BuildingsGeoJson | null = null;
export function getMockBuildingsGeoJson(): BuildingsGeoJson {
  if (_bldgCache) return _bldgCache;
  const features: BuildingsGeoJson["features"] = [];
  let idx = 1;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const cLon = AOI.west + (c + 0.5) * D_LON;
      const cLat = AOI.north - (r + 0.5) * D_LAT;
      const { green, dense } = staticField(cLon, cLat);
      if (green > 0.55 && hrand(`skip${c}_${r}`) < 0.75) continue;

      const countInCell = dense > 0.55 ? 4 : dense > 0.25 ? 3 : 1;
      for (let b = 0; b < countInCell; b++) {
        const key = `bldg_${c}_${r}_${b}`;
        if (hrand(key) < 0.22) continue;
        const ox = ((b % 2) - 0.5) * 0.42 * D_LON + (hrand(key + "x") - 0.5) * 0.14 * D_LON;
        const oy = (Math.floor(b / 2) - 0.5) * 0.42 * D_LAT + (hrand(key + "y") - 0.5) * 0.14 * D_LAT;
        const w = (0.14 + 0.14 * hrand(key + "w")) * D_LON;
        const h = (0.14 + 0.14 * hrand(key + "h")) * D_LAT;
        const floors = Math.max(1, Math.round(1 + 5.5 * dense + 2.5 * hrand(key + "f")));
        const f = (v: number) => Math.round(v * 1e6) / 1e6;
        const lon0 = cLon + ox - w / 2;
        const lon1 = cLon + ox + w / 2;
        const lat0 = cLat + oy - h / 2;
        const lat1 = cLat + oy + h / 2;
        features.push({
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[f(lon0), f(lat1)], [f(lon1), f(lat1)], [f(lon1), f(lat0)], [f(lon0), f(lat0)], [f(lon0), f(lat1)]]],
          },
          properties: {
            bldg_id: `BLDG-${String(idx++).padStart(5, "0")}`,
            floors,
            height_m: round(floors * 3.2, 1)!,
            area_sqm: Math.round((w * 108070) * (h * 110600)),
            bldg_type: floors >= 5 ? "อาคารพาณิชย์/สำนักงาน" : floors >= 3 ? "ตึกแถว/หอพัก" : "บ้านพักอาศัย",
            bldg_source: hrand(key + "src") < 0.85 ? "BMA (building.shp)" : "Open Buildings",
            cell_id: cellId(c, r),
          },
        });
      }
    }
  }
  return (_bldgCache = { type: "FeatureCollection", features });
}