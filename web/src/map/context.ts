import type maplibregl from "maplibre-gl";
import type { DeviceInfo, GridFeatureCollection } from "../types";
import type { DetailDrawer } from "./drawer";

export type MatchMode = "all" | "window";

/** สถานะที่ layer ต่างๆ บนแผนที่ใช้ร่วมกัน (สร้างครั้งเดียวใน MapController) */
export interface MapContext {
  map: maplibregl.Map;
  drawer: DetailDrawer;
  getPeriod: () => string | null;
  getMatch: () => MatchMode;
  /** กริดล่าสุดจาก /grid — hotspot ใช้ geometry จากที่นี่ */
  grid: GridFeatureCollection | null;
  devices: DeviceInfo[];
  /** ซูมไปที่ข้อมูลแล้วหรือยัง (ทำครั้งเดียวตอนโหลดแรก) */
  fitted: boolean;
}

/** เปิด/ปิดหลาย layer พร้อมกัน (ข้าม layer ที่ยังไม่ถูกสร้าง) */
export function setVisible(map: maplibregl.Map, ids: string[], visible: boolean) {
  ids.filter((l) => map.getLayer(l)).forEach((l) => map.setLayoutProperty(l, "visibility", visible ? "visible" : "none"));
}

/** หา bounding box ของชุดพิกัด */
export function boundsOf(pts: [number, number][] | number[][]): [[number, number], [number, number]] {
  return [
    [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1]))],
    [Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))],
  ];
}

/** เปลี่ยน cursor เป็นมือเมื่อชี้ layer ที่คลิกได้ */
export function pointerOn(map: maplibregl.Map, layerId: string) {
  map.on("mouseenter", layerId, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layerId, () => (map.getCanvas().style.cursor = ""));
}
