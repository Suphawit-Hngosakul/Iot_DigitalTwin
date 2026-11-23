import maplibregl from "maplibre-gl";
import * as echarts from "echarts";
import { api, toMinuteObjects } from "./api";
import { AOI, cellOf, getMockBuildingsGeoJson, LEGENDS } from "./mock-api";
import type {
  AnalyticsHotspot,
  BuildingsGeoJson,
  DeviceInfo,
  GridCellDetail,
  GridFeatureCollection,
  LayersResponse,
  LayerLegend,
  LiveDeviceState,
  ReadingsResponse,
} from "./types";

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

const fmt = (v: unknown, d = 1) => (v == null ? "—" : typeof v === "number" ? v.toFixed(d) : String(v));
const dayOf = (s?: string | null) => (s || "").slice(0, 10);
const nextDay = (day: string) => new Date(Date.parse(day + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);
const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

export class MapController {
  map: maplibregl.Map;
  grid: GridFeatureCollection | null = null;
  devices: DeviceInfo[] = [];
  liveMarkers: Record<string, maplibregl.Marker> = {};
  fitted = false;
  pitched = false;

  constructor(
    private getPeriod: () => string | null,
    private setPeriod: (p: string, periods: LayersResponse["periods"]) => void,
    private getMatch: () => "all" | "window"
  ) {
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
    this.pitched = true;

    this.map.addControl(new maplibregl.NavigationControl(), "top-right");
    this.map.addControl(new maplibregl.ScaleControl(), "bottom-left");
    this.bindUi();
  }

  private bindUi() {
    (document.getElementById("basemap") as HTMLSelectElement).onchange = (e) => {
      const val = (e.target as HTMLSelectElement).value;
      this.map.setLayoutProperty("osm", "visibility", val === "osm" ? "visible" : "none");
      this.map.setLayoutProperty("imagery", "visibility", val === "imagery" ? "visible" : "none");
    };

    (document.getElementById("bldg-on") as HTMLInputElement).onchange = (e) => {
      const vis = (e.target as HTMLInputElement).checked ? "visible" : "none";
      ["bldg-3d", "bldg-outline"].filter((l) => this.map.getLayer(l)).forEach((l) => this.map.setLayoutProperty(l, "visibility", vis));
    };

    const tiltBtn = document.getElementById("bldg-tilt") as HTMLButtonElement;
    tiltBtn.textContent = "มุมมองบน 2D";
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
      const vis = (e.target as HTMLInputElement).checked ? "visible" : "none";
      ["track-pts", "track-line"].filter((l) => this.map.getLayer(l)).forEach((l) => this.map.setLayoutProperty(l, "visibility", vis));
    };

    (document.getElementById("live-on") as HTMLInputElement).onchange = (e) => {
      const show = (e.target as HTMLInputElement).checked;
      Object.values(this.liveMarkers).forEach((mk) => (mk.getElement().style.display = show ? "" : "none"));
    };

    this.map.on("click", "bldg-3d", (e) => {
      const props = e.features?.[0]?.properties || {};
      this.buildingPopup(props, e.lngLat);
    });
    this.map.on("mouseenter", "bldg-3d", () => (this.map.getCanvas().style.cursor = "pointer"));
    this.map.on("mouseleave", "bldg-3d", () => (this.map.getCanvas().style.cursor = ""));

    this.map.on("click", "grid-hit", (e) => {
      // ถ้าคลิกโดนอาคาร ให้ buildingPopup ทำงานแทน
      const bldgHits = this.map.queryRenderedFeatures(e.point, { layers: this.map.getLayer("bldg-3d") ? ["bldg-3d"] : [] });
      if (bldgHits.length > 0) return;
      const cellId = e.features?.[0]?.properties?.cell_id;
      if (cellId) this.cellPopup(String(cellId), e.lngLat);
    });
  }

  async initData(onReadyLive: () => void) {
    this.map.on("load", async () => {
      await this.loadLayers();
      await this.loadGrid();
      await this.loadBuildings();
      await this.loadHotspot();
      await this.loadDevicesOnMap();
      await this.loadTrack();
      onReadyLive();
    });
  }

  async refreshPeriod() {
    await this.loadLayers();
    await this.loadGrid();
    await this.loadHotspot();
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
      ? `โหลด <b>${data.features.length.toLocaleString()} อาคาร</b> จาก <code>building.shp</code> จริงแล้ว`
      : `แสดง <b>${data.features.length.toLocaleString()} อาคารจำลอง</b> (รัน <code>scripts/convert_buildings.py</code> เพื่อใช้ <code>building.shp</code> จริง)`;

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
        "fill-extrusion-opacity": 0.85,
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

  private async buildingPopup(props: Record<string, unknown>, lngLat: maplibregl.LngLat) {
    const cid = (props.cell_id as string) || cellOf(lngLat.lng, lngLat.lat);
    const pop = new maplibregl.Popup({ maxWidth: "350px" })
      .setLngLat(lngLat)
      .setHTML(`<div class="pop"><b>${esc(props.bldg_id || "อาคาร")}</b> · ${esc(props.bldg_type || "")}<br>กำลังดึงค่าความร้อนบล็อก ${esc(cid || "")}…</div>`)
      .addTo(this.map);

    let cellHtml = "";
    if (cid) {
      try {
        const res = await api<GridCellDetail>("/grid/" + cid, { period: this.getPeriod() });
        const d = res.match?.[this.getMatch()] || res.match?.all || {};
        cellHtml = `<tr><td colspan="2" style="padding-top:6px"><b>บริบทความร้อนกริด ${esc(cid)} (90×90 m)</b></td></tr>
          <tr><td>อุณหภูมิพื้นผิว (LST)</td><td><b>${fmt(d.lst_mean, 2)} °C</b></td></tr>
          <tr><td>อุณหภูมิอากาศจริง (IoT)</td><td><b>${fmt(d.air_temp_mean, 2)} °C</b></td></tr>
          <tr><td>พื้นที่สีเขียว (NDVI)</td><td>${fmt(d.ndvi_mean, 3)}</td></tr>
          <tr><td>ความหนาแน่นอาคารรวม</td><td>${fmt(d.builtup_pct, 1)} % (${fmt(d.building_count, 0)} หลัง)</td></tr>`;
      } catch {
        cellHtml = "";
      }
    }

    pop.setHTML(`<div class="pop">
      <b>${esc(props.bldg_id || "อาคาร")}</b> · ${esc(props.bldg_type || "อาคารทั่วไป")}<br>
      <small>แหล่งข้อมูล: ${esc(props.bldg_source || "building.shp")}</small>
      <table>
        <tr><td>จำนวนชั้น</td><td><b>${fmt(props.floors, 1)} ชั้น</b></td></tr>
        <tr><td>ความสูงประมาณ</td><td>${fmt(props.height_m, 1)} ม.</td></tr>
        <tr><td>พื้นที่ฐานอาคาร</td><td>${fmt(props.area_sqm, 0)} ตร.ม.</td></tr>
        ${cellHtml}
      </table>
    </div>`);
  }

  async loadLayers() {
    const note = document.getElementById("layer-note")!;
    const period = this.getPeriod();
    let res: LayersResponse;
    try {
      res = await api<LayersResponse>("/layers", { period: period && !period.startsWith("month_") ? period : null });
      note.textContent = "";
    } catch (e: unknown) {
      note.innerHTML = `<span class="err">${esc(e instanceof Error ? e.message : String(e))}</span>`;
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
    const beforeId = this.map.getLayer("grid-fill") ? "grid-fill" : this.map.getLayer("bldg-3d") ? "bldg-3d" : undefined;

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
      note.innerHTML = `<span class="err">${esc(e instanceof Error ? e.message : String(e))}</span>`;
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

  async cellPopup(id: string, lngLat: maplibregl.LngLat) {
    const pop = new maplibregl.Popup({ maxWidth: "360px" })
      .setLngLat(lngLat)
      .setHTML(`<div class="pop">กำลังโหลดบล็อก <code>${esc(id)}</code>…</div>`)
      .addTo(this.map);
    try {
      const res = await api<GridCellDetail>("/grid/" + id, { period: this.getPeriod() });
      const allObj = res.match?.all || {};
      const winObj = res.match?.window || {};
      const keys = ["lst_mean", "air_temp_mean", "ndvi_mean", "ndbi_mean", "builtup_pct", "building_count", "floors_mean", "far", "db_spl_mean", "n_iot_minutes"];
      const rows = keys.map((k) => `<tr><td>${k}</td><td>${fmt(allObj[k], 2)}</td><td>${fmt(winObj[k], 2)}</td></tr>`).join("");
      pop.setHTML(`<div class="pop"><b>บล็อก ${esc(res.cell_id)}</b> (90 × 90 ม.)<br>
        <table><tr><td>ตัวแปร</td><td><b>ทุกเวลา</b></td><td><b>09:30–11:30</b></td></tr>${rows}</table></div>`);
    } catch (e: unknown) {
      pop.setHTML(`<div class="pop err">${esc(e instanceof Error ? e.message : String(e))}</div>`);
    }
  }

  async loadHotspot() {
    const note = document.getElementById("hot-note")!;
    if (!this.grid) {
      note.textContent = "ไม่มีข้อมูลกริดสำหรับเชื่อม Hot spot";
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
      info.textContent = "ไม่มีเครื่องแบบ Mobile ใน /devices";
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
    info.innerHTML = `เส้นทางเดิน ${rows.length} นาที` + (res.rows.length ? "" : " — วันนี้ยังไม่มีข้อมูลเดินวัด");

    const src = this.map.getSource("track") as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(fc as unknown as GeoJSON.FeatureCollection);
      (this.map.getSource("track-line") as maplibregl.GeoJSONSource).setData(line as unknown as GeoJSON.Feature);
    } else {
      this.map.addSource("track-line", { type: "geojson", data: line as unknown as GeoJSON.Feature });
      this.map.addSource("track", { type: "geojson", data: fc as unknown as GeoJSON.FeatureCollection });
      this.map.addLayer({ id: "track-line", type: "line", source: "track-line", paint: { "line-color": "#0f172a", "line-width": 2, "line-opacity": 0.65 } });
      this.map.addLayer({ id: "track-pts", type: "circle", source: "track", paint: { "circle-radius": 4.5, "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
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
  }

  styleTrack() {
    if (!this.map.getLayer("track-pts")) return;
    const v = (document.getElementById("track-color") as HTMLSelectElement).value;
    this.map.setPaintProperty("track-pts", "circle-color", interp(v, TRACK_STOPS[v]));
    document.getElementById("track-legend")!.innerHTML = legendHtml(TRACK_STOPS[v]);
  }

  async loadDevicesOnMap() {
    try {
      const res = await api<{ devices: DeviceInfo[] }>("/devices");
      this.devices = res.devices;
      const mobiles = res.devices.filter((x) => x.device_type !== "Stationary");
      const sel = document.getElementById("track-dev") as HTMLSelectElement;
      sel.innerHTML = mobiles.map((d) => `<option value="${esc(d.device_id)}">${esc(d.device_id)}</option>`).join("");
      const first = mobiles.find((d) => d.latest_hour_local) || mobiles[0];
      if (first) {
        sel.value = first.device_id;
        if (first.latest_hour_local) (document.getElementById("track-date") as HTMLInputElement).value = dayOf(first.latest_hour_local);
      }
      for (const d of res.devices.filter((x) => x.device_type === "Stationary" && x.fixed_lat != null && x.fixed_lon != null)) {
        const el = document.createElement("div");
        el.className = "pin";
        el.title = d.site_name || d.device_id;
        new maplibregl.Marker({ element: el }).setLngLat([d.fixed_lon!, d.fixed_lat!]).addTo(this.map);
        el.onclick = (ev) => {
          ev.stopPropagation();
          this.stationPopup(d);
        };
      }
    } catch (e: unknown) {
      document.getElementById("live-note")!.innerHTML = `<span class="err">${esc(e instanceof Error ? e.message : String(e))}</span>`;
    }
  }

  private async stationPopup(d: DeviceInfo) {
    const div = document.createElement("div");
    div.className = "pop";
    div.style.width = "300px";
    div.innerHTML = `<b>${esc(d.device_id)}</b> · ${esc(d.site_name || "")}<div class="kv small" id="sp-kv">…</div><div style="height:150px" id="sp-chart"></div>`;
    new maplibregl.Popup({ maxWidth: "320px" }).setLngLat([d.fixed_lon!, d.fixed_lat!]).setDOMContent(div).addTo(this.map);

    try {
      const latest = await api<{ row: Record<string, unknown> }>("/latest", { device: d.device_id });
      const r = latest.row;
      div.querySelector("#sp-kv")!.innerHTML = `<span>เวลา</span><b>${esc(r.minute_local)}</b><span>อุณหภูมิ</span><b>${esc(r.temp_c_mean)} °C</b><span>ความชื้น</span><b>${esc(r.humidity_pct_mean)} %</b><span>เสียง</span><b>${esc(r.db_spl_mean)} dB</b>`;
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

  moveLiveMarker(m: LiveDeviceState) {
    if (m.lat == null || m.lon == null) return;
    let mk = this.liveMarkers[m.device_id];
    if (!mk) {
      const el = document.createElement("div");
      el.className = "live-dot";
      el.title = m.device_id + " (realtime)";
      mk = this.liveMarkers[m.device_id] = new maplibregl.Marker({ element: el }).setLngLat([m.lon, m.lat]).addTo(this.map);
      if (!(document.getElementById("live-on") as HTMLInputElement).checked) el.style.display = "none";
    }
    mk.setLngLat([m.lon, m.lat]);
  }
}