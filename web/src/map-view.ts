import maplibregl from "maplibre-gl";
import * as echarts from "echarts";
import { api, toMinuteObjects } from "./api";
import { AOI, cellOf, getMockBuildingsGeoJson, LEGENDS } from "./mock-api";
import {
  bindCommunitySimulationEvents,
  buildCommunitySimulationHtml,
  evaluateAllCommunities,
  type CommunityPriorityResult,
} from "./community-priority";
import type {
  AnalyticsHotspot,
  BuildingsGeoJson,
  DeviceInfo,
  GridCellDetail,
  GridFeatureCollection,
  LayersResponse,
  LayerLegend,
  ReadingsResponse,
} from "./types";

export type ProjectMode = "project2" | "cs341";

const GRID_STOPS: Record<string, [number, string][]> = {
  lst_mean: LEGENDS.lst.stops,
  ndvi_mean: LEGENDS.ndvi.stops,
  ndbi_mean: LEGENDS.ndbi.stops,
  builtup_pct: LEGENDS.building_density.stops,
  air_temp_mean: [[28, "#2c7bb6"], [30, "#ffffbf"], [32.5, "#d7191c"]],
  db_spl_mean: [[55, "#1a9850"], [65, "#fee08b"], [78, "#d73027"]],
};

const TRACK_STOPS: Record<string, [number, string][]> = {
  temp_c_mean: [[27, "#2c7bb6"], [30, "#ffffbf"], [33, "#d7191c"]],
  db_spl_mean: GRID_STOPS.db_spl_mean,
  dba_mean: [[50, "#1a9850"], [60, "#fee08b"], [72, "#d73027"]],
};

const LAYER_NAMES: Record<string, string> = {
  lst: "LST อุณหภูมิพื้นผิว (°C)",
  ndvi: "NDVI พื้นที่สีเขียว",
  ndbi: "NDBI สิ่งปลูกสร้าง",
  building_density: "ความหนาแน่นอาคาร 90 m (%)",
};

const GLOSSARY_ITEMS: { code: string; name: string; desc: string }[] = [
  {
    code: "lst_mean",
    name: "อุณหภูมิพื้นผิวเฉลี่ย (Land Surface Temperature)",
    desc: "ค่าเฉลี่ยอุณหภูมิพื้นผิวโลก (°C) จากภาพถ่ายดาวเทียม Landsat 8/9 สะท้อนความร้อนสะสมของหลังคาอาคาร ถนน และพื้นดิน",
  },
  {
    code: "lst_p90",
    name: "อุณหภูมิพื้นผิวสูงสุด 90th Percentile",
    desc: "ค่าอุณหภูมิพื้นผิวที่เปอร์เซ็นไทล์ที่ 90 (°C) ใช้ดูระดับความร้อนจัดในช่วงพีคของพื้นที่",
  },
  {
    code: "air_temp_mean",
    name: "อุณหภูมิอากาศจริงระดับคนเดินเท้า (IoT)",
    desc: "ค่าเฉลี่ยอุณหภูมิอากาศ (°C) ที่ตรวจวัดจากอุปกรณ์ IoT ระดับความสูง ~1.5 เมตร ซึ่งเป็นอุณหภูมิที่คนเดินเท้าสัมผัสจริง",
  },
  {
    code: "ndvi_mean",
    name: "ดัชนีพืชพรรณ (NDVI: -1 ถึง +1)",
    desc: "วัดความหนาแน่นของต้นไม้และพื้นที่สีเขียว ยิ่งค่าสูง (เข้าใกล้ +1) แปลว่ามีต้นไม้มาก ช่วยคายความชื้นลดความร้อนได้ดี",
  },
  {
    code: "ndbi_mean",
    name: "ดัชนีสิ่งปลูกสร้าง (NDBI: -1 ถึง +1)",
    desc: "วัดสัดส่วนสิ่งปลูกสร้างและพื้นผิวคอนกรีต ยิ่งค่าสูง (เป็นบวก) แปลว่ามีอาคารและพื้นผิวแข็งที่ดูดซับความร้อนหนาแน่น",
  },
  {
    code: "green_ratio",
    name: "สัดส่วนพื้นที่สีเขียว (%)",
    desc: "ร้อยละของพื้นที่ปกคลุมด้วยพืชพรรณและเรือนยอดไม้เทียบกับพื้นที่ทั้งหมดในรัศมีที่ศึกษา",
  },
  {
    code: "builtup_pct",
    name: "ความหนาแน่นพื้นที่อาคาร (Building Coverage Ratio)",
    desc: "ร้อยละของพื้นที่ฐานอาคารทั้งหมดเทียบกับพื้นที่บล็อก 90×90 ม. (8,100 ตร.ม.) สะท้อนการบังลมและการสะสมความร้อน",
  },
  {
    code: "building_count",
    name: "จำนวนอาคาร (หลัง)",
    desc: "จำนวนหลังคาเรือนและสิ่งปลูกสร้างทั้งหมดจากชั้นข้อมูล building.shp ที่อยู่ในขอบเขตพื้นที่ศึกษา",
  },
  {
    code: "floors / floors_mean",
    name: "จำนวนชั้นอาคาร / จำนวนชั้นเฉลี่ย",
    desc: "ความสูงในหน่วยชั้นของอาคารรายหลัง หรือค่าเฉลี่ยจำนวนชั้นของกลุ่มอาคารภายในบล็อก",
  },
  {
    code: "height_m / area_sqm",
    name: "ความสูงอาคาร (ม.) และพื้นที่ฐาน (ตร.ม.)",
    desc: "ความสูงโดยประมาณจากพื้นถึงหลังคา (เมตร) และขนาดพื้นที่หน้าตัดฐานของตัวอาคาร (ตารางเมตร)",
  },
  {
    code: "far",
    name: "อัตราส่วนพื้นที่อาคารรวมต่อพื้นที่ดิน (FAR)",
    desc: "Floor Area Ratio คำนวณจากผลรวมพื้นที่ใช้สอยทุกชั้นหารด้วยพื้นที่ดิน สะท้อนความหนาแน่นแนวตั้งและมวลความร้อน (Thermal Mass)",
  },
  {
    code: "db_spl_mean",
    name: "ระดับความดังเสียงเฉลี่ย (dB SPL)",
    desc: "ค่าเฉลี่ยระดับความดังเสียงสภาพแวดล้อมจากไมโครโฟน IoT สะท้อนความหนาแน่นของการจราจรและกิจกรรมเมือง",
  },
  {
    code: "n_iot_minutes",
    name: "จำนวนนาทีที่มีข้อมูล IoT",
    desc: "จำนวนตัวอย่างข้อมูลรายนาทีที่บันทึกได้จริงภายในบล็อกกริดนั้น (ใช้กรองความน่าเชื่อถือของค่าอากาศเฉลี่ย)",
  },
];

function buildGlossaryHtml(): string {
  const rows = GLOSSARY_ITEMS.map(
    (g) => `<div class="gloss-item">
      <div class="g-head"><span class="g-tag">${g.code}</span><strong>${g.name}</strong></div>
      <p>${g.desc}</p>
    </div>`
  ).join("");

  return `<details class="glossary-card">
    <summary>
      <span>📖 คำอธิบายตัวแปรและตัวชี้วัดคืออะไร?</span>
      <span class="gloss-hint">กดเพื่อดูความหมาย</span>
    </summary>
    <div class="glossary-list">${rows}</div>
  </details>`;
}

interface HeatStatus {
  level: "cool" | "moderate" | "warn" | "danger" | "none";
  badge: string;
  desc: string;
}

function evalSurfaceLst(lst: unknown): HeatStatus {
  const v = Number(lst);
  if (lst == null || Number.isNaN(v)) {
    return { level: "none", badge: "ไม่มีข้อมูล LST", desc: "ยังไม่มีค่าอุณหภูมิพื้นผิวจากดาวเทียมในบล็อกนี้" };
  }
  if (v >= 44) {
    return {
      level: "danger",
      badge: "ร้อนจัดวิกฤต (Extreme Hotspot)",
      desc: `พื้นผิวร้อนจัด ${v.toFixed(2)} °C (สูงกว่าโซนสีเขียว >10 °C) สะสมความร้อนรุนแรง`,
    };
  }
  if (v >= 40) {
    return {
      level: "warn",
      badge: "ร้อนสะสมสูง (UHI Hotspot)",
      desc: `พื้นผิวหลังคา/คอนกรีต ${v.toFixed(2)} °C สูงกว่าโซนสีเขียวทั่วไป ~6–9 °C`,
    };
  }
  if (v >= 35) {
    return {
      level: "moderate",
      badge: "สะสมความร้อนปานกลาง",
      desc: `อุณหภูมิพื้นผิว ${v.toFixed(2)} °C เริ่มมีการสะสมความร้อนจากสิ่งปลูกสร้างและพื้นผิวดาดแข็ง`,
    };
  }
  return {
    level: "cool",
    badge: "โซนเย็น / ระบายความร้อนดี",
    desc: `อุณหภูมิพื้นผิว ${v.toFixed(2)} °C อยู่ในเกณฑ์เย็นสบาย`,
  };
}

function evalAirTemp(air: unknown): HeatStatus {
  const v = Number(air);
  if (air == null || Number.isNaN(v)) {
    return { level: "none", badge: "ไม่มีข้อมูลอากาศ IoT", desc: "จุดนี้ยังไม่มีเส้นทางเดินวัดหรือสถานี IoT ในช่วงเวลาที่เลือก" };
  }
  if (v >= 36) {
    return {
      level: "danger",
      badge: "ร้อนจัดอันตรายมาก (เสี่ยง Heatstroke)",
      desc: `อากาศระดับทางเดินเท้า ${v.toFixed(2)} °C อันตรายต่อสุขภาพ หลีกเลี่ยงการอยู่กลางแจ้ง`,
    };
  }
  if (v >= 33) {
    return {
      level: "danger",
      badge: "ร้อนอันตราย (เสี่ยงเพลียแดด)",
      desc: `อากาศระดับทางเดินเท้า ${v.toFixed(2)} °C เมื่อรวมความชื้นจะรู้สึกร้อนจัด ควรพักในที่ร่มเป็นระยะ`,
    };
  }
  if (v >= 30) {
    return {
      level: "warn",
      badge: "เริ่มร้อนอบอ้าว (เฝ้าระวัง)",
      desc: `อากาศระดับทางเดินเท้า ${v.toFixed(2)} °C (ความชื้นแบบไทยทำให้รู้สึกร้อนเทียบเท่า ~37–40 °C)`,
    };
  }
  return {
    level: "cool",
    badge: "อากาศสบาย / ปกติ",
    desc: `อุณหภูมิอากาศระดับทางเดินเท้า ${v.toFixed(2)} °C อยู่ในเกณฑ์สบายตัว`,
  };
}

function buildVerdictHtml(lst: unknown, air?: unknown, includeAir = true): string {
  const s = evalSurfaceLst(lst);
  const a = includeAir ? evalAirTemp(air) : null;
  return `
    <div class="heat-verdict">
      <div class="verdict-row">
        <div class="verdict-top">
          <span class="verdict-label">ประเมินความร้อนพื้นผิว (LST)</span>
          <span class="v-pill ${s.level}">${s.badge}</span>
        </div>
        <p class="verdict-desc ${s.level === "danger" ? "danger-text" : ""}">${s.desc}</p>
      </div>
      ${a
      ? `<div class="verdict-row">
              <div class="verdict-top">
                <span class="verdict-label">ประเมินความร้อนคนเดินเท้า (IoT Air)</span>
                <span class="v-pill ${a.level}">${a.badge}</span>
              </div>
              <p class="verdict-desc ${a.level === "danger" ? "danger-text" : ""}">${a.desc}</p>
            </div>`
      : ""
    }
    </div>
  `;
}

function getGeometryCenter(geom: GeoJSON.Geometry | null | undefined): [number, number] | null {
  if (!geom) return null;
  const coords: [number, number][] = [];
  const collect = (c: unknown) => {
    if (Array.isArray(c) && c.length >= 2 && typeof c[0] === "number" && typeof c[1] === "number") {
      coords.push([c[0], c[1]]);
    } else if (Array.isArray(c)) {
      c.forEach(collect);
    }
  };
  if ("coordinates" in geom) collect(geom.coordinates);
  if (!coords.length) return null;
  const lngs = coords.map((p) => p[0]);
  const lats = coords.map((p) => p[1]);
  return [(Math.min(...lngs) + Math.max(...lngs)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
}

const hex = (c: [number, number, number]) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
function normStops(lg?: LayerLegend): [number, string][] | null {
  if (!lg?.stops) return null;
  return lg.stops.map(([t, c]) =>
    Array.isArray(c) ? [Math.round((lg.min + t * (lg.max - lg.min)) * 100) / 100, hex(c)] : [t, c]
  );
}

const interp = (prop: string, stops: [number, string][]): maplibregl.ExpressionSpecification => [
  "interpolate",
  ["linear"],
  ["get", prop],
  ...stops.flat(),
] as unknown as maplibregl.ExpressionSpecification;

function legendHtml(stops: [number, string][], unit = "") {
  const g = `linear-gradient(90deg, ${stops.map(([, c]) => c).join(",")})`;
  return `<span>${stops[0][0]}${unit}</span><span class="bar" style="background:${g}"></span><span>${stops[stops.length - 1][0]}${unit}</span>`;
}

const fmt = (v: unknown, d = 1) => (v == null || Number.isNaN(Number(v)) ? "—" : typeof v === "number" ? v.toFixed(d) : String(v));
const dayOf = (s?: string | null) => (s || "").slice(0, 10);
const nextDay = (day: string) => new Date(Date.parse(day + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);
const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

export class MapController {
  map: maplibregl.Map;
  grid: GridFeatureCollection | null = null;
  devices: DeviceInfo[] = [];
  stationMarkers: maplibregl.Marker[] = [];
  communityRankings: CommunityPriorityResult[] = [];
  private communityFeatures: GeoJSON.Feature[] = [];
  private liveMarker: maplibregl.Marker | null = null;
  private liveCoords: [number, number] | null = null;
  private liveRefreshTimer: number | null = null;
  private liveInfo: { devId: string; ts: string; row: Record<string, unknown> } | null = null;
  projectMode: ProjectMode = "project2";
  fitted = false;
  pitched = true;
  private drawerChart: echarts.ECharts | null = null;
  private onCommunitiesLoaded?: (features: GeoJSON.Feature[]) => void;

  constructor(
    private getPeriod: () => string | null,
    private setPeriod: (p: string, periods: LayersResponse["periods"]) => void,
    private getMatch: () => "all" | "window",
    onCommunitiesLoaded?: (features: GeoJSON.Feature[]) => void
  ) {
    this.onCommunitiesLoaded = onCommunitiesLoaded;
    this.map = new maplibregl.Map({
      container: "map",
      style: {
        version: 8,
        sources: {
          osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" },
          imagery: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, maxzoom: 19, attribution: "Imagery © Esri" },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#eef0f2" } },
          { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6, "raster-opacity": 0.9 } },
          { id: "imagery", type: "raster", source: "imagery", layout: { visibility: "none" } },
        ],
      },
      center: [AOI.west + (AOI.east - AOI.west) / 2, AOI.south + (AOI.north - AOI.south) / 2],
      zoom: 14.2,
      pitch: 30,
    });

    this.map.addControl(new maplibregl.NavigationControl(), "top-right");
    this.map.addControl(new maplibregl.ScaleControl(), "bottom-left");
    this.bindUi();
  }

  private openDrawer(opts: {
    theme: "amber" | "indigo" | "red" | "emerald" | "teal";
    badge: string;
    title: string;
    sub: string;
    html: string;
    onReady?: () => void;
  }) {
    if (this.drawerChart) {
      this.drawerChart.dispose();
      this.drawerChart = null;
    }

    const drawer = document.getElementById("detail-drawer")!;
    const head = document.getElementById("drawer-head")!;
    head.className = `drawer-head theme-${opts.theme}`;
    document.getElementById("drawer-badge")!.textContent = opts.badge;
    document.getElementById("drawer-title")!.textContent = opts.title;
    document.getElementById("drawer-sub")!.textContent = opts.sub;

    const body = document.getElementById("drawer-body")!;
    body.innerHTML = opts.html + buildGlossaryHtml();
    body.scrollTop = 0;

    drawer.classList.remove("hidden");
    this.map.resize();
    if (opts.onReady) requestAnimationFrame(opts.onReady);
  }

  closeDrawer() {
    if (this.drawerChart) {
      this.drawerChart.dispose();
      this.drawerChart = null;
    }
    document.getElementById("detail-drawer")?.classList.add("hidden");
    this.highlightFeature(null);
    this.map.resize();
  }

  private highlightFeature(geom: GeoJSON.Geometry | null) {
    const fc: GeoJSON.FeatureCollection = geom
      ? { type: "FeatureCollection", features: [{ type: "Feature", geometry: geom, properties: {} }] }
      : { type: "FeatureCollection", features: [] };

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

  setProjectMode(mode: ProjectMode) {
    this.projectMode = mode;
    const isCs341 = mode === "cs341";

    document.getElementById("grp-community")?.classList.toggle("hidden", !isCs341);
    document.getElementById("grp-iot-track")?.classList.toggle("hidden", isCs341);

    const commChecked = (document.getElementById("comm-on") as HTMLInputElement | null)?.checked !== false;
    const commVis = isCs341 && commChecked ? "visible" : "none";
    ["comm-fill", "comm-outline"]
      .filter((l) => this.map.getLayer(l))
      .forEach((l) => this.map.setLayoutProperty(l, "visibility", commVis));

    const trackChecked = (document.getElementById("track-on") as HTMLInputElement | null)?.checked !== false;
    const iotVis = !isCs341 && trackChecked ? "visible" : "none";
    ["track-pts", "track-line"]
      .filter((l) => this.map.getLayer(l))
      .forEach((l) => this.map.setLayoutProperty(l, "visibility", iotVis));

    this.stationMarkers.forEach((mk) => {
      mk.getElement().style.display = isCs341 ? "none" : "";
    });

    if (this.liveMarker) {
      this.liveMarker.getElement().style.display = isCs341 ? "none" : "";
    }
  }

  async focusCommunityById(communityId: string) {
    if (!this.communityFeatures.length) {
      await this.loadCommunities();
    }
    const feat = this.communityFeatures.find(
      (f) => String(f.properties?.community_id || "") === communityId
    );
    if (!feat) return;

    const commCb = document.getElementById("comm-on") as HTMLInputElement | null;
    if (commCb && !commCb.checked) commCb.checked = true;
    ["comm-fill", "comm-outline"]
      .filter((l) => this.map.getLayer(l))
      .forEach((l) => this.map.setLayoutProperty(l, "visibility", "visible"));

    const center = getGeometryCenter(feat.geometry);
    if (center) {
      this.map.easeTo({ center, zoom: 16, duration: 600 });
    }
    this.highlightFeature(feat.geometry);
    this.openCommunityDashboard(feat.properties || {});
  }

  private bindUi() {
    document.getElementById("drawer-close")!.onclick = () => this.closeDrawer();

    (document.getElementById("basemap") as HTMLSelectElement).onchange = (e) => {
      const val = (e.target as HTMLSelectElement).value;
      this.map.setLayoutProperty("osm", "visibility", val === "osm" ? "visible" : "none");
      this.map.setLayoutProperty("imagery", "visibility", val === "imagery" ? "visible" : "none");
    };

    const commCb = document.getElementById("comm-on") as HTMLInputElement | null;
    if (commCb) {
      commCb.onchange = (e) => {
        const show = this.projectMode === "cs341" && (e.target as HTMLInputElement).checked;
        ["comm-fill", "comm-outline"]
          .filter((l) => this.map.getLayer(l))
          .forEach((l) => this.map.setLayoutProperty(l, "visibility", show ? "visible" : "none"));
      };
    }

    (document.getElementById("bldg-on") as HTMLInputElement).onchange = (e) => {
      const vis = (e.target as HTMLInputElement).checked ? "visible" : "none";
      ["bldg-3d", "bldg-outline"].filter((l) => this.map.getLayer(l)).forEach((l) => this.map.setLayoutProperty(l, "visibility", vis));
    };

    const tiltBtn = document.getElementById("bldg-tilt") as HTMLButtonElement;
    tiltBtn.onclick = () => {
      this.pitched = !this.pitched;
      this.map.easeTo({ pitch: this.pitched ? 45 : 0, bearing: this.pitched ? -15 : 0, duration: 500 });
      tiltBtn.textContent = this.pitched ? "มุมมองบน 2D" : "เอียงมุมมอง 3D";
    };

    (document.getElementById("grid-on") as HTMLInputElement).onchange = (e) => {
      const vis = (e.target as HTMLInputElement).checked ? "visible" : "none";
      ["grid-fill", "grid-line"].filter((l) => this.map.getLayer(l)).forEach((l) => this.map.setLayoutProperty(l, "visibility", vis));
    };
    (document.getElementById("grid-var") as HTMLSelectElement).onchange = () => this.styleGrid();

    (document.getElementById("hot-on") as HTMLInputElement).onchange = (e) => {
      if (this.map.getLayer("hot-fill")) {
        this.map.setLayoutProperty("hot-fill", "visibility", (e.target as HTMLInputElement).checked ? "visible" : "none");
      }
    };

    (document.getElementById("track-color") as HTMLSelectElement).onchange = () => this.styleTrack();
    (document.getElementById("track-dev") as HTMLSelectElement).onchange = () => {
      const id = (document.getElementById("track-dev") as HTMLSelectElement).value;
      const d = this.devices.find((x) => x.device_id === id);
      if (d?.latest_hour_local) (document.getElementById("track-date") as HTMLInputElement).value = dayOf(d.latest_hour_local);
      this.loadTrack();
    };
    (document.getElementById("track-date") as HTMLInputElement).onchange = () => this.loadTrack();
    (document.getElementById("track-on") as HTMLInputElement).onchange = (e) => {
      const show = this.projectMode === "project2" && (e.target as HTMLInputElement).checked;
      ["track-pts", "track-line"].filter((l) => this.map.getLayer(l)).forEach((l) => this.map.setLayoutProperty(l, "visibility", show ? "visible" : "none"));
    };

    document.getElementById("iot-focus-now")?.addEventListener("click", () => {
      if (this.liveCoords) {
        this.map.easeTo({ center: this.liveCoords, zoom: Math.max(this.map.getZoom(), 16.5), duration: 500 });
      }
    });

    this.map.on("click", "bldg-3d", (e) => {
      const feat = e.features?.[0];
      const props = feat?.properties || {};
      this.highlightFeature((feat?.geometry as GeoJSON.Geometry) || null);
      this.openBuildingDashboard(props, e.lngLat);
    });
    this.map.on("mouseenter", "bldg-3d", () => (this.map.getCanvas().style.cursor = "pointer"));
    this.map.on("mouseleave", "bldg-3d", () => (this.map.getCanvas().style.cursor = ""));

    this.map.on("click", "comm-fill", (e) => {
      if (this.projectMode !== "cs341") return;
      const bldgHits = this.map.queryRenderedFeatures(e.point, {
        layers: this.map.getLayer("bldg-3d") ? ["bldg-3d"] : [],
      });
      if (bldgHits.length > 0) return;
      const feat = e.features?.[0];
      this.highlightFeature((feat?.geometry as GeoJSON.Geometry) || null);
      this.openCommunityDashboard(feat?.properties || {});
    });
    this.map.on("mouseenter", "comm-fill", () => (this.map.getCanvas().style.cursor = "pointer"));
    this.map.on("mouseleave", "comm-fill", () => (this.map.getCanvas().style.cursor = ""));

    this.map.on("click", "grid-hit", (e) => {
      const bldgHits = this.map.queryRenderedFeatures(e.point, { layers: this.map.getLayer("bldg-3d") ? ["bldg-3d"] : [] });
      if (bldgHits.length > 0) return;
      if (this.projectMode === "cs341") {
        const commHits = this.map.queryRenderedFeatures(e.point, { layers: this.map.getLayer("comm-fill") ? ["comm-fill"] : [] });
        if (commHits.length > 0) return;
      }
      const cellId = e.features?.[0]?.properties?.cell_id;
      if (cellId) this.openCellDashboard(String(cellId));
    });
    this.map.on("mouseenter", "grid-hit", () => (this.map.getCanvas().style.cursor = "pointer"));
    this.map.on("mouseleave", "grid-hit", () => (this.map.getCanvas().style.cursor = ""));
  }

  async initData() {
    this.map.on("load", async () => {
      await this.loadLayers();
      await this.loadGrid();
      await this.loadCommunities();
      await this.loadBuildings();
      await this.loadHotspot();
      await this.loadDevicesOnMap();
      await this.loadTrack();
      this.setProjectMode(this.projectMode);
    });
  }

  async refreshPeriod() {
    await this.loadLayers();
    await this.loadGrid();
    await this.loadHotspot();
  }

  async loadCommunities() {
    const note = document.getElementById("comm-note");
    const data = await api<GeoJSON.FeatureCollection>("/community").catch(() => null);
    if (!data?.features?.length) {
      if (note) note.textContent = "ยังไม่มีข้อมูลชุมชนในระบบ API";
      return;
    }

    this.communityFeatures = data.features;
    this.communityRankings = evaluateAllCommunities(data.features);
    if (this.onCommunitiesLoaded) {
      this.onCommunitiesLoaded(data.features);
    }

    if (note) {
      note.innerHTML = `พบ <b>${data.features.length} ชุมชน</b> · สีวงกลมแสดงระดับความเปราะบาง (ไม่ใช่ °C)`;
    }

    const existing = this.map.getSource("communities") as maplibregl.GeoJSONSource | undefined;
    if (existing) {
      existing.setData(data);
      return;
    }

    const visible = this.projectMode === "cs341" ? "visible" : "none";
    this.map.addSource("communities", { type: "geojson", data });
    const colorExpr = [
      "match",
      ["get", "risk_level"],
      "high", "#ef4444",
      "medium", "#f59e0b",
      "low", "#10b981",
      "#6366f1",
    ] as unknown as maplibregl.ExpressionSpecification;

    this.map.addLayer({
      id: "comm-fill",
      type: "fill",
      source: "communities",
      layout: { visibility: visible },
      paint: { "fill-color": colorExpr, "fill-opacity": 0.32 },
    });
    this.map.addLayer({
      id: "comm-outline",
      type: "line",
      source: "communities",
      layout: { visibility: visible },
      paint: { "line-color": colorExpr, "line-width": 2 },
    });
  }

  private openCommunityDashboard(props: Record<string, unknown>) {
    const cid = String(props.community_id || "");
    const evaluated =
      this.communityRankings.find((c) => c.id === cid) ||
      evaluateAllCommunities([{ properties: props }])[0];

    const pct = (v: unknown) => (v != null && v !== "" ? `${fmt(Number(v) * 100, 1)} %` : "—");

    const html = `
      ${buildCommunitySimulationHtml(evaluated, 30)}
      ${buildVerdictHtml(props.lst_mean_300, null, false)}

      <div class="d-kpis">
        <div class="d-kpi warm">
          <div class="lbl">อุณหภูมิพื้นผิว (300 ม.)</div>
          <div class="val">${fmt(props.lst_mean_300, 2)} °C</div>
          <div class="sub">ช่วงพีค p90: ${fmt(props.lst_p90_300, 2)} °C</div>
        </div>
        <div class="d-kpi amber">
          <div class="lbl">ความหนาแน่นอาคาร (300 ม.)</div>
          <div class="val">${fmt(props.builtup_pct_300, 1)} %</div>
          <div class="sub">จำนวน ${fmt(props.building_count_300, 0)} หลัง</div>
        </div>
        <div class="d-kpi cool">
          <div class="lbl">พื้นที่สีเขียว (300 ม.)</div>
          <div class="val">${pct(props.green_ratio_300)}</div>
          <div class="sub">ดัชนี NDVI: ${fmt(props.ndvi_mean_300, 3)}</div>
        </div>
        <div class="d-kpi blue">
          <div class="lbl">อุณหภูมิพื้นผิว (500 ม.)</div>
          <div class="val">${fmt(props.lst_mean_500, 2)} °C</div>
          <div class="sub">อาคาร ${fmt(props.builtup_pct_500, 1)} %</div>
        </div>
      </div>

      <div class="d-card">
        <h4>เปรียบเทียบสภาพแวดล้อม รัศมี 300 ม. vs 500 ม.</h4>
        <div id="drawer-chart" class="d-chart"></div>
      </div>

      <div class="d-card">
        <h4>ตารางตัวชี้วัดรายรัศมี (ปี 2026)</h4>
        <table class="d-table">
          <thead><tr><th>ตัวชี้วัด</th><th>รัศมี 300 ม.</th><th>รัศมี 500 ม.</th></tr></thead>
          <tbody>
            <tr><td>อุณหภูมิพื้นผิวเฉลี่ย<span class="var-code">lst_mean</span></td><td><b>${fmt(props.lst_mean_300, 2)} °C</b></td><td>${fmt(props.lst_mean_500, 2)} °C</td></tr>
            <tr><td>อุณหภูมิพื้นผิวสูงสุด 90th<span class="var-code">lst_p90</span></td><td>${fmt(props.lst_p90_300, 2)} °C</td><td>${fmt(props.lst_p90_500, 2)} °C</td></tr>
            <tr><td>ดัชนีพืชพรรณ<span class="var-code">ndvi_mean</span></td><td>${fmt(props.ndvi_mean_300, 3)}</td><td>${fmt(props.ndvi_mean_500, 3)}</td></tr>
            <tr><td>สัดส่วนพื้นที่สีเขียว<span class="var-code">green_ratio</span></td><td>${pct(props.green_ratio_300)}</td><td>${pct(props.green_ratio_500)}</td></tr>
            <tr><td>ความหนาแน่นอาคาร<span class="var-code">builtup_pct</span></td><td><b>${fmt(props.builtup_pct_300, 1)} %</b></td><td>${fmt(props.builtup_pct_500, 1)} %</td></tr>
            <tr><td>จำนวนอาคารโดยรอบ<span class="var-code">building_count</span></td><td>${fmt(props.building_count_300, 0)} หลัง</td><td>${fmt(props.building_count_500, 0)} หลัง</td></tr>
          </tbody>
        </table>
      </div>
    `;

    this.openDrawer({
      theme: evaluated.theme,
      badge: `อันดับ #${evaluated.rank}/${evaluated.total} · ${String(props.community_id || "CS341")}`,
      title: String(props.name || props.community_id || "ชุมชน"),
      sub: `เขต${String(props.district || "—")} · ${evaluated.tierLabel}`,
      html,
      onReady: () => {
        bindCommunitySimulationEvents(document.getElementById("comm-sim-box"), evaluated);

        const el = document.getElementById("drawer-chart");
        if (!el) return;
        this.drawerChart = echarts.init(el);
        this.drawerChart.setOption({
          tooltip: { trigger: "axis" },
          legend: { top: 0, textStyle: { fontSize: 11 } },
          grid: { left: 40, right: 12, top: 28, bottom: 24 },
          xAxis: { type: "category", data: ["LST (°C)", "อาคาร (%)", "สีเขียว (%)"] },
          yAxis: { type: "value" },
          series: [
            {
              name: "รัศมี 300 ม.",
              type: "bar",
              data: [
                Number(props.lst_mean_300 ?? 0),
                Number(props.builtup_pct_300 ?? 0),
                Number(props.green_ratio_300 ?? 0) * 100,
              ],
              itemStyle: { color: "#f97316", borderRadius: [4, 4, 0, 0] },
            },
            {
              name: "รัศมี 500 ม.",
              type: "bar",
              data: [
                Number(props.lst_mean_500 ?? 0),
                Number(props.builtup_pct_500 ?? 0),
                Number(props.green_ratio_500 ?? 0) * 100,
              ],
              itemStyle: { color: "#0284c7", borderRadius: [4, 4, 0, 0] },
            },
          ],
        });
      },
    });
  }

  async loadBuildings() {
    const note = document.getElementById("bldg-note")!;
    let data: BuildingsGeoJson;
    let isRealShp = false;
    try {
      const r = await fetch("./data/buildings.geojson", { cache: "no-store" });
      if (r.ok) {
        data = await r.json();
        isRealShp = true;
      } else {
        data = getMockBuildingsGeoJson();
      }
    } catch {
      data = getMockBuildingsGeoJson();
    }

    note.innerHTML = isRealShp
      ? `โหลด <b>${data.features.length.toLocaleString()} อาคาร</b> จาก <code>building.shp</code>`
      : `แสดง <b>${data.features.length.toLocaleString()} อาคารจำลอง</b>`;

    this.map.addSource("buildings", { type: "geojson", data: data as unknown as GeoJSON.FeatureCollection });
    if (isRealShp && data.features.length > 0 && !this.fitted) {
      const pts = data.features.flatMap((f) => f.geometry.coordinates[0] || []);
      if (pts.length) {
        this.map.fitBounds(
          [
            [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1]))],
            [Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))],
          ],
          { padding: 40, duration: 0, pitch: 35 }
        );
        this.fitted = true;
      }
    }
    this.map.addLayer({
      id: "bldg-3d",
      type: "fill-extrusion",
      source: "buildings",
      paint: {
        "fill-extrusion-height": ["coalesce", ["get", "height_m"], 6],
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 1,
        "fill-extrusion-vertical-gradient": true,
        "fill-extrusion-color": [
          "step",
          ["coalesce", ["get", "floors"], 2],
          "#fde68a",
          3,
          "#f97316",
          6,
          "#b91c1c",
        ],
      },
    });
    this.map.addLayer({
      id: "bldg-outline",
      type: "line",
      source: "buildings",
      paint: { "line-color": "#7c2d12", "line-width": 0.5, "line-opacity": 0.6 },
    });
  }

  private async openBuildingDashboard(props: Record<string, unknown>, lngLat: maplibregl.LngLat) {
    const cid = (props.cell_id as string) || cellOf(lngLat.lng, lngLat.lat);
    const bldgTitle = String(props.bldg_id || "อาคารทั่วไป");
    const bldgType = String(props.bldg_type || "อาคารทั่วไป");
    const bldgSrc = String(props.bldg_source || "building.shp");

    const baseHtml = `
      <div class="d-kpis">
        <div class="d-kpi amber">
          <div class="lbl">จำนวนชั้น (floors)</div>
          <div class="val">${fmt(props.floors, 1)} ชั้น</div>
          <div class="sub">ความสูง ~${fmt(props.height_m, 1)} ม.</div>
        </div>
        <div class="d-kpi blue">
          <div class="lbl">พื้นที่ฐานอาคาร</div>
          <div class="val">${fmt(props.area_sqm, 0)} ตร.ม.</div>
          <div class="sub">แหล่งข้อมูล: ${esc(bldgSrc)}</div>
        </div>
      </div>
      <div class="d-card" id="bldg-grid-context">
        <h4>บริบทความร้อนรอบอาคาร (กริด ${esc(cid || "—")})</h4>
        <p class="note">กำลังดึงข้อมูลอุณหภูมิและสิ่งปกคลุมดินรอบอาคาร…</p>
      </div>
    `;

    this.openDrawer({
      theme: "amber",
      badge: "อาคารรายหลัง 3D · building.shp",
      title: bldgTitle,
      sub: `ประเภท: ${bldgType} · พิกัด ${lngLat.lat.toFixed(5)}, ${lngLat.lng.toFixed(5)}`,
      html: baseHtml,
    });

    if (!cid) return;
    try {
      const res = await api<GridCellDetail>("/grid/" + cid, { period: this.getPeriod() });
      const d = res.match?.[this.getMatch()] || res.match?.all || {};
      const box = document.getElementById("bldg-grid-context");
      if (!box) return;
      box.innerHTML = `
        <h4>บริบทความร้อนรอบอาคาร · บล็อก ${esc(cid)} (90×90 ม.)</h4>
        ${buildVerdictHtml(d.lst_mean, d.air_temp_mean, true)}
        <div class="d-kpis" style="margin-top:8px; margin-bottom:8px;">
          <div class="d-kpi warm">
            <div class="lbl">อุณหภูมิพื้นผิว (LST)</div>
            <div class="val">${fmt(d.lst_mean, 2)} °C</div>
            <div class="sub">ช่วงพีค p90: ${fmt(d.lst_p90, 2)} °C</div>
          </div>
          <div class="d-kpi cool">
            <div class="lbl">อากาศจริง (IoT)</div>
            <div class="val">${fmt(d.air_temp_mean, 2)} °C</div>
            <div class="sub">พื้นที่สีเขียว NDVI: ${fmt(d.ndvi_mean, 3)}</div>
          </div>
        </div>
        <table class="d-table">
          <thead><tr><th>ตัวแปรสภาพแวดล้อมรอบอาคาร</th><th>ค่าที่วัดได้</th></tr></thead>
          <tbody>
            <tr><td>ความหนาแน่นอาคารในบล็อก<span class="var-code">builtup_pct</span></td><td><b>${fmt(d.builtup_pct, 1)} %</b> (${fmt(d.building_count, 0)} หลัง)</td></tr>
            <tr><td>อัตราส่วนพื้นที่อาคารต่อที่ดิน<span class="var-code">far</span></td><td>${fmt(d.far, 2)}</td></tr>
            <tr><td>จำนวนชั้นเฉลี่ยในบล็อก<span class="var-code">floors_mean</span></td><td>${fmt(d.floors_mean, 1)} ชั้น</td></tr>
            <tr><td>ดัชนีสิ่งปลูกสร้าง<span class="var-code">ndbi_mean</span></td><td>${fmt(d.ndbi_mean, 3)}</td></tr>
            <tr><td>ระดับเสียงเฉลี่ย<span class="var-code">db_spl_mean</span></td><td>${fmt(d.db_spl_mean, 1)} dB</td></tr>
          </tbody>
        </table>
      `;
    } catch {
      const box = document.getElementById("bldg-grid-context");
      if (box) box.innerHTML = `<h4>บริบทความร้อนรอบอาคาร</h4><p class="note">ยังไม่มีข้อมูลกริดสำหรับบล็อก ${esc(cid)}</p>`;
    }
  }

  async openCellDashboard(id: string) {
    this.openDrawer({
      theme: "indigo",
      badge: "กริดวิเคราะห์เชิงพื้นที่ · 90 × 90 ม.",
      title: `บล็อก ${id}`,
      sub: `กำลังดึงข้อมูลช่วงเวลา ${this.getPeriod() || "ล่าสุด"}…`,
      html: `<div class="d-card"><p class="note">กำลังโหลดข้อมูลสถิติของบล็อก <code>${esc(id)}</code>…</p></div>`,
    });

    try {
      const res = await api<GridCellDetail>("/grid/" + id, { period: this.getPeriod() });
      this.highlightFeature(res.geometry as unknown as GeoJSON.Geometry);

      const allObj = res.match?.all || {};
      const winObj = res.match?.window || {};
      const activeObj = res.match?.[this.getMatch()] || allObj;

      const rowsSpec: { key: string; label: string; unit: string; dec: number }[] = [
        { key: "lst_mean", label: "อุณหภูมิพื้นผิวเฉลี่ย", unit: " °C", dec: 2 },
        { key: "lst_p90", label: "อุณหภูมิพื้นผิวสูงสุด 90th", unit: " °C", dec: 2 },
        { key: "air_temp_mean", label: "อุณหภูมิอากาศจริง (IoT)", unit: " °C", dec: 2 },
        { key: "ndvi_mean", label: "ดัชนีพืชพรรณ", unit: "", dec: 3 },
        { key: "ndbi_mean", label: "ดัชนีสิ่งปลูกสร้าง", unit: "", dec: 3 },
        { key: "builtup_pct", label: "ความหนาแน่นอาคาร", unit: " %", dec: 1 },
        { key: "building_count", label: "จำนวนอาคารในบล็อก", unit: " หลัง", dec: 0 },
        { key: "floors_mean", label: "จำนวนชั้นเฉลี่ย", unit: " ชั้น", dec: 1 },
        { key: "far", label: "อัตราส่วนพื้นที่อาคาร (FAR)", unit: "", dec: 2 },
        { key: "db_spl_mean", label: "ระดับเสียงเฉลี่ย", unit: " dB", dec: 1 },
        { key: "n_iot_minutes", label: "จำนวนนาทีข้อมูล IoT", unit: " นาที", dec: 0 },
      ];

      const tableRows = rowsSpec
        .map(
          (r) => `<tr>
            <td>${r.label}<span class="var-code">${r.key}</span></td>
            <td><b>${allObj[r.key] != null ? fmt(allObj[r.key], r.dec) + r.unit : "—"}</b></td>
            <td>${winObj[r.key] != null ? fmt(winObj[r.key], r.dec) + r.unit : "—"}</td>
          </tr>`
        )
        .join("");

      const html = `
        ${buildVerdictHtml(activeObj.lst_mean, activeObj.air_temp_mean, true)}

        <div class="d-kpis">
          <div class="d-kpi warm">
            <div class="lbl">อุณหภูมิพื้นผิว (LST)</div>
            <div class="val">${fmt(activeObj.lst_mean, 2)} °C</div>
            <div class="sub">ช่วงพีค p90: ${fmt(activeObj.lst_p90, 2)} °C</div>
          </div>
          <div class="d-kpi blue">
            <div class="lbl">อุณหภูมิอากาศจริง (IoT)</div>
            <div class="val">${fmt(activeObj.air_temp_mean, 2)} °C</div>
            <div class="sub">บันทึก ${fmt(activeObj.n_iot_minutes, 0)} นาที</div>
          </div>
          <div class="d-kpi cool">
            <div class="lbl">ดัชนีพืชพรรณ (NDVI)</div>
            <div class="val">${fmt(activeObj.ndvi_mean, 3)}</div>
            <div class="sub">สิ่งปลูกสร้าง NDBI: ${fmt(activeObj.ndbi_mean, 3)}</div>
          </div>
          <div class="d-kpi amber">
            <div class="lbl">ความหนาแน่นอาคาร</div>
            <div class="val">${fmt(activeObj.builtup_pct, 1)} %</div>
            <div class="sub">${fmt(activeObj.building_count, 0)} หลัง · FAR ${fmt(activeObj.far, 2)}</div>
          </div>
        </div>

        <div class="d-card">
          <h4>เปรียบเทียบทุกเวลา vs ช่วงแดดจัด (09:30–11:30)</h4>
          <div id="drawer-chart" class="d-chart"></div>
        </div>

        <div class="d-card">
          <h4>ค่าตัวแปรทั้งหมดในบล็อก ${esc(res.cell_id)}</h4>
          <table class="d-table">
            <thead><tr><th>ตัวแปร</th><th>ทุกเวลา (All)</th><th>09:30–11:30</th></tr></thead>
            <tbody>${tableRows}</tbody>
          </table>
        </div>
      `;

      this.openDrawer({
        theme: "indigo",
        badge: "กริดวิเคราะห์เชิงพื้นที่ · 90 × 90 ม.",
        title: `บล็อก ${res.cell_id}`,
        sub: `ช่วงเวลา ${res.period} · พื้นที่มาตรฐาน 8,100 ตร.ม.`,
        html,
        onReady: () => {
          const el = document.getElementById("drawer-chart");
          if (!el) return;
          this.drawerChart = echarts.init(el);
          this.drawerChart.setOption({
            tooltip: { trigger: "axis" },
            legend: { top: 0, textStyle: { fontSize: 11 } },
            grid: { left: 40, right: 12, top: 28, bottom: 24 },
            xAxis: { type: "category", data: ["LST (°C)", "อากาศ IoT (°C)", "อาคาร (%)"] },
            yAxis: { type: "value" },
            series: [
              {
                name: "ทุกเวลา (All)",
                type: "bar",
                data: [
                  Number(allObj.lst_mean ?? 0),
                  Number(allObj.air_temp_mean ?? 0),
                  Number(allObj.builtup_pct ?? 0),
                ],
                itemStyle: { color: "#4f46e5", borderRadius: [4, 4, 0, 0] },
              },
              {
                name: "แดดจัด 09:30–11:30",
                type: "bar",
                data: [
                  Number(winObj.lst_mean ?? 0),
                  Number(winObj.air_temp_mean ?? 0),
                  Number(winObj.builtup_pct ?? 0),
                ],
                itemStyle: { color: "#f97316", borderRadius: [4, 4, 0, 0] },
              },
            ],
          });
        },
      });
    } catch (e: unknown) {
      this.openDrawer({
        theme: "indigo",
        badge: "กริดวิเคราะห์เชิงพื้นที่ · 90 × 90 ม.",
        title: `บล็อก ${id}`,
        sub: "ไม่สามารถโหลดข้อมูลบล็อกนี้ได้",
        html: `<div class="d-card"><p class="err">${esc(e instanceof Error ? e.message : String(e))}</p></div>`,
      });
    }
  }

  async loadLayers() {
    const note = document.getElementById("layer-note")!;
    const period = this.getPeriod();
    let res: LayersResponse;
    try {
      res = await api<LayersResponse>("/layers", { period: period && !period.startsWith("month_") ? period : null });
      note.textContent = "";
    } catch (e: unknown) {
      note.innerHTML = `<span class="err">ไม่สามารถโหลดชั้นข้อมูลดาวเทียม (${esc(e instanceof Error ? e.message : String(e))})</span>`;
      return;
    }

    const all = [...res.layers, ...res.static_layers].filter((L) => L.url && L.coordinates);
    for (const L of all) {
      const st = normStops(L.legend);
      if (st) {
        L.stops = st;
        const g = { lst: "lst_mean", ndvi: "ndvi_mean", ndbi: "ndbi_mean", building_density: "builtup_pct" }[L.layer];
        if (g) GRID_STOPS[g] = st;
      }
    }

    if (!this.fitted && all.length) {
      const cs = all[0].coordinates;
      this.map.fitBounds(
        [
          [Math.min(...cs.map((c) => c[0])), Math.min(...cs.map((c) => c[1]))],
          [Math.max(...cs.map((c) => c[0])), Math.max(...cs.map((c) => c[1]))],
        ],
        { padding: 30, duration: 0, pitch: 30 }
      );
      this.fitted = true;
    }

    const box = document.getElementById("layer-controls")!;
    const beforeId = this.map.getLayer("grid-fill")
      ? "grid-fill"
      : this.map.getLayer("comm-fill")
        ? "comm-fill"
        : this.map.getLayer("bldg-3d")
          ? "bldg-3d"
          : undefined;

    for (const L of all) {
      const id = "img-" + L.layer;
      const src = this.map.getSource(id) as maplibregl.ImageSource | undefined;
      if (src) {
        src.updateImage({ url: L.url, coordinates: L.coordinates as [[number, number], [number, number], [number, number], [number, number]] });
        continue;
      }
      this.map.addSource(id, { type: "image", url: L.url, coordinates: L.coordinates as [[number, number], [number, number], [number, number], [number, number]] });
      const on = L.layer === "lst";
      this.map.addLayer(
        { id, type: "raster", source: id, paint: { "raster-opacity": 0.65, "raster-resampling": "nearest" }, layout: { visibility: on ? "visible" : "none" } },
        beforeId
      );

      const div = document.createElement("div");
      div.innerHTML = `<div class="layer-row"><label class="row"><input type="checkbox" ${on ? "checked" : ""}/> ${LAYER_NAMES[L.layer] || L.layer}</label><input type="range" min="0" max="100" value="65" title="ความโปร่งใส"/></div>
        <div class="legend">${L.stops ? legendHtml(L.stops, L.legend.unit) : ""}</div>`;
      const [cb, rg] = div.querySelectorAll("input");
      cb.onchange = () => this.map.setLayoutProperty(id, "visibility", cb.checked ? "visible" : "none");
      rg.oninput = () => this.map.setPaintProperty(id, "raster-opacity", Number(rg.value) / 100);
      box.appendChild(div);
    }

    if (!this.getPeriod()) {
      this.setPeriod(res.period, res.periods);
    }
  }

  async loadGrid() {
    const note = document.getElementById("grid-note")!;
    const vars = ["lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct", "air_temp_mean", "db_spl_mean"].join(",");
    let res: GridFeatureCollection;
    try {
      res = await api<GridFeatureCollection>("/grid", { period: this.getPeriod(), vars, match: this.getMatch() });
      note.textContent = `${res.features.length} บล็อก · ช่วงเวลา ${res.period}`;
    } catch (e: unknown) {
      note.innerHTML = `<span class="err">ไม่สามารถโหลดกริด (${esc(e instanceof Error ? e.message : String(e))})</span>`;
      this.grid = null;
      return;
    }

    this.grid = res;
    const src = this.map.getSource("grid") as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(res as unknown as GeoJSON.FeatureCollection);
    } else {
      this.map.addSource("grid", { type: "geojson", data: res as unknown as GeoJSON.FeatureCollection, promoteId: "cell_id" });
      this.map.addLayer({ id: "grid-fill", type: "fill", source: "grid", layout: { visibility: "none" }, paint: { "fill-opacity": 0.7 } });
      this.map.addLayer({ id: "grid-line", type: "line", source: "grid", layout: { visibility: "none" }, paint: { "line-color": "#334155", "line-width": 0.4, "line-opacity": 0.5 } });
      this.map.addLayer({ id: "grid-hit", type: "fill", source: "grid", paint: { "fill-opacity": 0 } });
    }
    this.styleGrid();
  }

  styleGrid() {
    if (!this.map.getLayer("grid-fill")) return;
    const v = (document.getElementById("grid-var") as HTMLSelectElement).value;
    const stops = GRID_STOPS[v];
    this.map.setPaintProperty("grid-fill", "fill-color", ["case", ["==", ["get", v], null], "rgba(0,0,0,0)", interp(v, stops)]);
    document.getElementById("grid-legend")!.innerHTML =
      legendHtml(stops) + (v.includes("air") || v.includes("db_") ? " <small>(ช่องว่าง = ไม่มีข้อมูล IoT ≥ 5 นาที)</small>" : "");
  }

  async loadHotspot() {
    const note = document.getElementById("hot-note")!;
    if (!this.grid) {
      note.textContent = "รอข้อมูลกริดสำหรับวิเคราะห์จุดร้อน";
      return;
    }
    try {
      const res = await api<AnalyticsHotspot>("/analytics/hotspot", { period: this.getPeriod(), match: this.getMatch() });
      note.textContent = "";
      const geom = Object.fromEntries(this.grid.features.map((f) => [f.id, f.geometry]));
      const fc = {
        type: "FeatureCollection",
        features: res.cells
          .filter((c) => c.class !== "not_significant" && geom[c.cell_id])
          .map((c) => ({ type: "Feature", geometry: geom[c.cell_id], properties: c })),
      };
      const src = this.map.getSource("hot") as maplibregl.GeoJSONSource | undefined;
      if (src) {
        src.setData(fc as unknown as GeoJSON.FeatureCollection);
      } else {
        this.map.addSource("hot", { type: "geojson", data: fc as unknown as GeoJSON.FeatureCollection });
        this.map.addLayer(
          {
            id: "hot-fill",
            type: "fill",
            source: "hot",
            layout: { visibility: "none" },
            paint: {
              "fill-opacity": 0.65,
              "fill-color": ["match", ["get", "class"], "hot_99", "#b2182b", "hot_95", "#ef8a62", "cold_95", "#67a9cf", "cold_99", "#2166ac", "#ccc"],
            },
          },
          "grid-hit"
        );
      }
    } catch (e: unknown) {
      note.innerHTML = `<span class="err">${esc(e instanceof Error ? e.message : String(e))}</span>`;
    }
  }

  async loadTrack() {
    const info = document.getElementById("track-info")!;
    const day = (document.getElementById("track-date") as HTMLInputElement).value;
    const dev = (document.getElementById("track-dev") as HTMLSelectElement).value;
    if (!dev) {
      info.textContent = "ไม่พบรายชื่ออุปกรณ์ในระบบ";
      return;
    }
    let res: ReadingsResponse;
    try {
      res = await api<ReadingsResponse>("/readings", { device: dev, from: day + "T00:00:00", to: nextDay(day) + "T00:00:00", interval: "1min" });
    } catch (e: unknown) {
      info.innerHTML = `<span class="err">${esc(e instanceof Error ? e.message : String(e))}</span>`;
      return;
    }

    const rows = toMinuteObjects(res).filter((r) => r.lat != null && r.lon != null);
    const fc = {
      type: "FeatureCollection",
      features: rows.map((r) => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon!, r.lat!] }, properties: r })),
    };
    const line = { type: "Feature", geometry: { type: "LineString", coordinates: rows.map((r) => [r.lon!, r.lat!]) }, properties: {} };
    info.innerHTML = rows.length > 0
      ? `พบข้อมูลเส้นทางเดิน <b>${rows.length} นาที</b> (คลิกจุดเพื่อดูค่าอากาศ)`
      : `วันที่ ${day} ยังไม่มีพิกัดเดินวัดสำหรับ ${dev}`;

    const src = this.map.getSource("track") as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(fc as unknown as GeoJSON.FeatureCollection);
      (this.map.getSource("track-line") as maplibregl.GeoJSONSource).setData(line as unknown as GeoJSON.Feature);
    } else {
      const vis = this.projectMode === "project2" ? "visible" : "none";
      this.map.addSource("track-line", { type: "geojson", data: line as unknown as GeoJSON.Feature });
      this.map.addSource("track", { type: "geojson", data: fc as unknown as GeoJSON.FeatureCollection });
      this.map.addLayer({
        id: "track-line",
        type: "line",
        source: "track-line",
        layout: { visibility: vis },
        paint: { "line-color": "#0f172a", "line-width": 1.5, "line-opacity": 0.35 },
      });
      this.map.addLayer({
        id: "track-pts",
        type: "circle",
        source: "track",
        layout: { visibility: vis },
        paint: {
          "circle-radius": 3.5,
          "circle-opacity": 0.55,
          "circle-stroke-color": "#fff",
          "circle-stroke-width": 1,
          "circle-stroke-opacity": 0.6,
        },
      });
      this.map.on("click", "track-pts", (e) => {
        const p = e.features?.[0]?.properties || {};
        new maplibregl.Popup()
          .setLngLat(e.lngLat)
          .setHTML(`<div class="pop"><b>เวลา ${esc(String(p.minute_local || "").slice(11, 16))}</b><br>
            <table>
              <tr><td>อุณหภูมิอากาศจริง</td><td><b>${esc(p.temp_c_mean)} °C</b></td></tr>
              <tr><td>ความชื้นสัมพัทธ์</td><td>${esc(p.humidity_pct_mean)} %</td></tr>
              <tr><td>ระดับเสียง</td><td>${esc(p.db_spl_mean)} dB(SPL)</td></tr>
              <tr><td>ความเข้มแสง</td><td>${esc(p.lux_mean)} lux</td></tr>
              <tr><td>บล็อกกริด</td><td>${esc(p.cell_id)}</td></tr>
            </table></div>`)
          .addTo(this.map);
      });
    }
    this.styleTrack();

    await this.refreshCurrentPosition(dev);
  }

  styleTrack() {
    if (!this.map.getLayer("track-pts")) return;
    const v = (document.getElementById("track-color") as HTMLSelectElement).value;
    this.map.setPaintProperty("track-pts", "circle-color", interp(v, TRACK_STOPS[v]));
    document.getElementById("track-legend")!.innerHTML = legendHtml(TRACK_STOPS[v]);
  }

  async refreshCurrentPosition(devId: string) {
    const nowText = document.getElementById("iot-now-text");
    const nowMeta = document.getElementById("iot-now-meta");
    if (!devId) return;

    try {
      const latest = await api<{ row?: Record<string, unknown> }>("/latest", { device: devId });
      const r = latest.row || {};
      const lat = r.lat != null ? Number(r.lat) : null;
      const lon = r.lon != null ? Number(r.lon) : null;
      const ts = String(r.minute_local || r.ts || "");

      if (lat != null && lon != null && !Number.isNaN(lat) && !Number.isNaN(lon)) {
        this.liveCoords = [lon, lat];
        this.liveInfo = { devId, ts, row: r };

        if (!this.liveMarker) {
          const wrapper = document.createElement("div");
          wrapper.className = "iot-live-marker";
          wrapper.innerHTML = `<div class="iot-live-badge" id="iot-live-pin-label">${esc(devId)} · ปัจจุบัน</div><div class="iot-live-dot"></div>`;
          wrapper.onclick = (ev) => {
            ev.stopPropagation();
            const cur = this.liveInfo;
            const pos = this.liveCoords;
            if (!cur || !pos) return;
            const cr = cur.row;
            new maplibregl.Popup()
              .setLngLat(pos)
              .setHTML(`<div class="pop"><b>🔴 พิกัดปัจจุบัน ${esc(cur.devId)}</b><br>
                <table>
                  <tr><td>เวลาล่าสุด</td><td><b>${esc(cur.ts || "ล่าสุด")}</b></td></tr>
                  <tr><td>อุณหภูมิอากาศ</td><td><b>${esc(cr.temp_c_mean ?? cr.temp_c ?? "—")} °C</b></td></tr>
                  <tr><td>ระดับเสียง</td><td>${esc(cr.db_spl_mean ?? cr.db_spl ?? "—")} dB</td></tr>
                  <tr><td>พิกัด</td><td>${pos[1].toFixed(5)}, ${pos[0].toFixed(5)}</td></tr>
                </table></div>`)
              .addTo(this.map);
          };
          this.liveMarker = new maplibregl.Marker({ element: wrapper, anchor: "bottom" })
            .setLngLat([lon, lat])
            .addTo(this.map);
        } else {
          this.liveMarker.setLngLat([lon, lat]);
          const lbl = this.liveMarker.getElement().querySelector("#iot-live-pin-label");
          if (lbl) lbl.textContent = `${devId} · ${ts ? ts.slice(11, 19) : "ปัจจุบัน"}`;
        }

        if (this.liveMarker) {
          this.liveMarker.getElement().style.display = this.projectMode === "cs341" ? "none" : "";
        }

        if (nowText) nowText.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
        if (nowMeta) {
          nowMeta.textContent = `อัปเดต ${ts || "ล่าสุด"} · อุณหภูมิ ${fmt(r.temp_c_mean ?? r.temp_c, 1)} °C`;
        }
      } else {
        this.liveCoords = null;
        this.liveInfo = null;
        this.liveMarker?.remove();
        this.liveMarker = null;
        if (nowText) nowText.textContent = "ไม่มีพิกัด GPS ในข้อมูลล่าสุด";
        if (nowMeta) nowMeta.textContent = "แสดงเฉพาะเส้นทางเดินย้อนหลังตามวันที่เลือก";
      }
    } catch {
      if (nowText) nowText.textContent = "ดึงพิกัดล่าสุดไม่สำเร็จ";
      if (nowMeta) nowMeta.textContent = "ตรวจสอบการเชื่อมต่อ /latest API";
    }

    if (this.liveRefreshTimer === null) {
      this.liveRefreshTimer = window.setInterval(() => {
        const curDev = (document.getElementById("track-dev") as HTMLSelectElement | null)?.value;
        if (curDev && this.projectMode === "project2") {
          this.refreshCurrentPosition(curDev);
        }
      }, 10000);
    }
  }

  async loadDevicesOnMap() {
    try {
      const res = await api<{ devices: DeviceInfo[] }>("/devices");
      this.devices = res.devices;
      const mobiles = res.devices.filter((x) => String(x.device_type || "").toLowerCase() !== "stationary");
      const list = mobiles.length ? mobiles : res.devices;
      const sel = document.getElementById("track-dev") as HTMLSelectElement;
      sel.innerHTML = list.map((d) => `<option value="${esc(d.device_id)}">${esc(d.device_id)}</option>`).join("");
      const first = list.find((d) => d.latest_hour_local) || list[0];
      if (first) {
        sel.value = first.device_id;
        if (first.latest_hour_local) (document.getElementById("track-date") as HTMLInputElement).value = dayOf(first.latest_hour_local);
      }

      this.stationMarkers.forEach((m) => m.remove());
      this.stationMarkers = [];
      for (const d of res.devices.filter((x) => String(x.device_type || "").toLowerCase() === "stationary" && x.fixed_lat != null && x.fixed_lon != null)) {
        const el = document.createElement("div");
        el.className = "pin";
        el.title = d.site_name || d.device_id;
        if (this.projectMode === "cs341") el.style.display = "none";
        const mk = new maplibregl.Marker({ element: el }).setLngLat([d.fixed_lon!, d.fixed_lat!]).addTo(this.map);
        this.stationMarkers.push(mk);
        el.onclick = (ev) => {
          ev.stopPropagation();
          this.stationPopup(d);
        };
      }
    } catch {
      // เงียบไว้หาก /devices ยังไม่มีข้อมูล
    }
  }

  private async stationPopup(d: DeviceInfo) {
    const div = document.createElement("div");
    div.className = "pop";
    div.style.width = "300px";
    div.innerHTML = `<b>${esc(d.device_id)}</b> · ${esc(d.site_name || "")}<div class="kv" id="sp-kv">…</div><div style="height:150px" id="sp-chart"></div>`;
    new maplibregl.Popup({ maxWidth: "320px" }).setLngLat([d.fixed_lon!, d.fixed_lat!]).setDOMContent(div).addTo(this.map);

    try {
      const latest = await api<{ row: Record<string, unknown> }>("/latest", { device: d.device_id });
      const r = latest.row;
      div.querySelector("#sp-kv")!.innerHTML = `<span>เวลาล่าสุด</span><b>${esc(r.minute_local)}</b><span>อุณหภูมิ</span><b>${esc(r.temp_c_mean)} °C</b><span>ความชื้น</span><b>${esc(r.humidity_pct_mean)} %</b><span>เสียง</span><b>${esc(r.db_spl_mean)} dB</b>`;
      const day = dayOf(String(r.minute_local));
      const res = await api<ReadingsResponse>("/readings", { device: d.device_id, from: day + "T00:00:00", to: nextDay(day) + "T00:00:00", interval: "1min" });
      const rows = toMinuteObjects(res).filter((_, i) => i % 10 === 0);
      const c = echarts.init(div.querySelector("#sp-chart") as HTMLElement);
      c.setOption({
        grid: { left: 34, right: 8, top: 20, bottom: 20 },
        title: { text: "อุณหภูมิอากาศ " + day, textStyle: { fontSize: 11 } },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: rows.map((row) => row.minute_local.slice(11, 16)) },
        yAxis: { type: "value", scale: true },
        series: [{ type: "line", data: rows.map((row) => row.temp_c_mean), showSymbol: false, lineStyle: { color: "#7c3aed" } }],
      });
    } catch (e: unknown) {
      div.querySelector("#sp-kv")!.innerHTML = `<span class="err">${esc(e instanceof Error ? e.message : String(e))}</span>`;
    }
  }
}