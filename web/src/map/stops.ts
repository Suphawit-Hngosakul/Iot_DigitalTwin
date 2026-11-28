import type maplibregl from "maplibre-gl";
import type { Stops } from "../utils/legend";

export interface LegendDef {
  column: string;
  min: number;
  max: number;
  unit: string;
  stops: Stops;
}

/** สี legend เริ่มต้นของ layer ดาวเทียม (ถ้า /layers ส่ง legend มา จะใช้ของ API แทน) */
export const LEGENDS: Record<"lst" | "ndvi" | "ndbi" | "building_density", LegendDef> = {
  lst: {
    column: "lst",
    min: 30,
    max: 46,
    unit: "°C",
    stops: [
      [30, "#2c7bb6"],
      [34, "#abd9e9"],
      [38, "#ffffbf"],
      [42, "#fdae61"],
      [46, "#d7191c"],
    ],
  },
  ndvi: {
    column: "ndvi",
    min: -0.1,
    max: 0.8,
    unit: "",
    stops: [
      [-0.1, "#8c510a"],
      [0.15, "#d8b365"],
      [0.3, "#f6e8c3"],
      [0.5, "#a1d76a"],
      [0.8, "#1a9641"],
    ],
  },
  ndbi: {
    column: "ndbi",
    min: -0.4,
    max: 0.4,
    unit: "",
    stops: [
      [-0.4, "#4d9221"],
      [0, "#f7f7f7"],
      [0.4, "#c51b7d"],
    ],
  },
  building_density: {
    column: "builtup_pct",
    min: 0,
    max: 100,
    unit: "%",
    stops: [
      [0, "#fff7ec"],
      [30, "#fdbb84"],
      [65, "#d7301f"],
      [100, "#7f0000"],
    ],
  },
};

/** สีของกริด 90 m ต่อตัวแปร — ค่าของ layer ดาวเทียมจะถูกแทนด้วย legend จาก /layers (ดู satellite-layers.ts) */
export const GRID_STOPS: Record<string, Stops> = {
  lst_mean: LEGENDS.lst.stops,
  ndvi_mean: LEGENDS.ndvi.stops,
  ndbi_mean: LEGENDS.ndbi.stops,
  builtup_pct: LEGENDS.building_density.stops,
  air_temp_mean: [
    [28, "#2c7bb6"],
    [30, "#ffffbf"],
    [32.5, "#d7191c"],
  ],
  db_spl_mean: [
    [55, "#1a9850"],
    [65, "#fee08b"],
    [78, "#d73027"],
  ],
};

/** layer ดาวเทียม → ตัวแปรกริดที่ใช้สีชุดเดียวกัน */
export const LAYER_TO_GRID_VAR: Record<string, string> = {
  lst: "lst_mean",
  ndvi: "ndvi_mean",
  ndbi: "ndbi_mean",
  building_density: "builtup_pct",
};

export const TRACK_STOPS: Record<string, Stops> = {
  temp_c_mean: [
    [27, "#2c7bb6"],
    [30, "#ffffbf"],
    [33, "#d7191c"],
  ],
  db_spl_mean: GRID_STOPS.db_spl_mean,
  dba_mean: [
    [50, "#1a9850"],
    [60, "#fee08b"],
    [72, "#d73027"],
  ],
};

export const LAYER_NAMES: Record<string, string> = {
  lst: "LST อุณหภูมิพื้นผิว (°C)",
  ndvi: "NDVI พื้นที่สีเขียว",
  ndbi: "NDBI สิ่งปลูกสร้าง",
  building_density: "ความหนาแน่นอาคาร 90 m (%)",
};

/** stops → expression ไล่สีของ MapLibre ตาม property */
export const interp = (prop: string, stops: Stops): maplibregl.ExpressionSpecification =>
  ["interpolate", ["linear"], ["get", prop], ...stops.flat()] as unknown as maplibregl.ExpressionSpecification;
