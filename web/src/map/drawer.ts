import type maplibregl from "maplibre-gl";
import type * as echarts from "echarts";
import { byId } from "../utils/html";
import { buildGlossaryHtml } from "./heat-verdict";

export type DrawerTheme = "amber" | "indigo" | "red" | "emerald" | "teal";

export interface DrawerContent {
  theme: DrawerTheme;
  badge: string;
  title: string;
  sub: string;
  html: string;
  /** เรียกหลังวาด HTML แล้ว (ใช้สร้างกราฟใน drawer) — คืนกราฟมาเพื่อให้ drawer dispose ตอนปิด */
  onReady?: () => echarts.ECharts | void;
}

/** แผง Dashboard ด้านขวาของแผนที่ (คลิกอาคาร / เซลล์กริด) + ไฮไลต์รูปที่เลือกบนแผนที่ */
export class DetailDrawer {
  private chart: echarts.ECharts | null = null;

  constructor(private map: maplibregl.Map) {
    byId("drawer-close").onclick = () => this.close();
  }

  open(opts: DrawerContent) {
    this.disposeChart();
    byId("drawer-head").className = `drawer-head theme-${opts.theme}`;
    byId("drawer-badge").textContent = opts.badge;
    byId("drawer-title").textContent = opts.title;
    byId("drawer-sub").textContent = opts.sub;

    const body = byId("drawer-body");
    body.innerHTML = opts.html + buildGlossaryHtml();
    body.scrollTop = 0;

    byId("detail-drawer").classList.remove("hidden");
    this.map.resize();
    if (opts.onReady) {
      requestAnimationFrame(() => {
        this.chart = opts.onReady!() || null;
      });
    }
  }

  close() {
    this.disposeChart();
    byId("detail-drawer").classList.add("hidden");
    this.highlight(null);
    this.map.resize();
  }

  /** วาดกรอบรอบอาคาร/เซลล์ที่เลือก (null = ล้าง) */
  highlight(geom: GeoJSON.Geometry | null) {
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: geom ? [{ type: "Feature", geometry: geom, properties: {} }] : [],
    };
    const src = this.map.getSource("selected-hl") as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(fc);
    } else if (this.map.isStyleLoaded()) {
      this.map.addSource("selected-hl", { type: "geojson", data: fc });
      this.map.addLayer({
        id: "selected-hl-fill",
        type: "fill",
        source: "selected-hl",
        paint: { "fill-color": "#06b6d4", "fill-opacity": 0.18 },
      });
      this.map.addLayer({
        id: "selected-hl-line",
        type: "line",
        source: "selected-hl",
        paint: { "line-color": "#0284c7", "line-width": 3 },
      });
    }
  }

  private disposeChart() {
    this.chart?.dispose();
    this.chart = null;
  }
}
