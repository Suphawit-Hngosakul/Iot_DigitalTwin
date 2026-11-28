import * as echarts from "echarts";
import type maplibregl from "maplibre-gl";
import { api, errMsg } from "../api";
import type { GridCellDetail, GridFeatureCollection } from "../types";
import { fmt } from "../utils/format";
import { byId, errHtml, esc } from "../utils/html";
import { legendHtml } from "../utils/legend";
import { pointerOn, setVisible, type MapContext } from "./context";
import { buildVerdictHtml } from "./heat-verdict";
import { GRID_STOPS, interp } from "./stops";

const GRID_VARS = ["lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct", "air_temp_mean", "db_spl_mean"];

/** แถวของตาราง "ค่าตัวแปรทั้งหมดในบล็อก" ใน Dashboard ของเซลล์ */
const CELL_ROWS: { key: string; label: string; unit: string; dec: number }[] = [
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

const CELL_BADGE = "กริดวิเคราะห์เชิงพื้นที่ · 90 × 90 ม.";

/** กริดวิเคราะห์ 90 m จาก /grid (ระบายสีตามตัวแปรที่เลือก) + Dashboard ของเซลล์เมื่อคลิก */
export class GridLayer {
  constructor(private ctx: MapContext) {
    const { map } = ctx;
    byId<HTMLInputElement>("grid-on").onchange = (e) => setVisible(map, ["grid-fill", "grid-line"], (e.target as HTMLInputElement).checked);
    byId<HTMLSelectElement>("grid-var").onchange = () => this.style();

    map.on("click", "grid-hit", (e) => {
      // คลิกโดนอาคาร → ให้ Dashboard อาคารทำงานแทน
      const bldgHits = map.queryRenderedFeatures(e.point, { layers: map.getLayer("bldg-3d") ? ["bldg-3d"] : [] });
      if (bldgHits.length > 0) return;
      const cellId = e.features?.[0]?.properties?.cell_id;
      if (cellId) this.openDashboard(String(cellId));
    });
    pointerOn(map, "grid-hit");
  }

  async load() {
    const { map } = this.ctx;
    const note = byId("grid-note");
    let res: GridFeatureCollection;
    try {
      res = await api<GridFeatureCollection>("/grid", {
        period: this.ctx.getPeriod(),
        vars: GRID_VARS.join(","),
        match: this.ctx.getMatch(),
      });
      note.textContent = `${res.features.length} บล็อก · ช่วงเวลา ${res.period}`;
    } catch (e: unknown) {
      note.innerHTML = errHtml(`ไม่สามารถโหลดกริด (${errMsg(e)})`);
      this.ctx.grid = null;
      return;
    }

    this.ctx.grid = res;
    const data = res as unknown as GeoJSON.FeatureCollection;
    const src = map.getSource("grid") as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(data);
    } else {
      map.addSource("grid", { type: "geojson", data, promoteId: "cell_id" });
      map.addLayer({ id: "grid-fill", type: "fill", source: "grid", layout: { visibility: "none" }, paint: { "fill-opacity": 0.7 } });
      map.addLayer({
        id: "grid-line",
        type: "line",
        source: "grid",
        layout: { visibility: "none" },
        paint: { "line-color": "#334155", "line-width": 0.4, "line-opacity": 0.5 },
      });
      // layer โปร่งใสสำหรับรับคลิก แม้ตอนปิดการแสดงกริด
      map.addLayer({ id: "grid-hit", type: "fill", source: "grid", paint: { "fill-opacity": 0 } });
    }
    this.style();
  }

  style() {
    const { map } = this.ctx;
    if (!map.getLayer("grid-fill")) return;
    const v = byId<HTMLSelectElement>("grid-var").value;
    const stops = GRID_STOPS[v];
    map.setPaintProperty("grid-fill", "fill-color", ["case", ["==", ["get", v], null], "rgba(0,0,0,0)", interp(v, stops)]);
    byId("grid-legend").innerHTML =
      legendHtml(stops) + (v.includes("air") || v.includes("db_") ? " <small>(ช่องว่าง = ไม่มีข้อมูล IoT ≥ 5 นาที)</small>" : "");
  }

  async openDashboard(id: string) {
    const { drawer } = this.ctx;
    drawer.open({
      theme: "indigo",
      badge: CELL_BADGE,
      title: `บล็อก ${id}`,
      sub: `กำลังดึงข้อมูลช่วงเวลา ${this.ctx.getPeriod() || "ล่าสุด"}…`,
      html: `<div class="d-card"><p class="note">กำลังโหลดข้อมูลสถิติของบล็อก <code>${esc(id)}</code>…</p></div>`,
    });

    let res: GridCellDetail;
    try {
      res = await api<GridCellDetail>("/grid/" + id, { period: this.ctx.getPeriod() });
    } catch (e: unknown) {
      drawer.open({
        theme: "indigo",
        badge: CELL_BADGE,
        title: `บล็อก ${id}`,
        sub: "ไม่สามารถโหลดข้อมูลบล็อกนี้ได้",
        html: `<div class="d-card"><p class="err">${esc(errMsg(e))}</p></div>`,
      });
      return;
    }

    drawer.highlight(res.geometry as unknown as GeoJSON.Geometry);
    const all = res.match?.all || {};
    const win = res.match?.window || {};
    const active = res.match?.[this.ctx.getMatch()] || all;
    const cell = (o: Record<string, unknown>, r: (typeof CELL_ROWS)[number]) => (o[r.key] != null ? fmt(o[r.key], r.dec) + r.unit : "—");

    const tableRows = CELL_ROWS.map(
      (r) => `<tr>
            <td>${r.label}<span class="var-code">${r.key}</span></td>
            <td><b>${cell(all, r)}</b></td>
            <td>${cell(win, r)}</td>
          </tr>`,
    ).join("");

    drawer.open({
      theme: "indigo",
      badge: CELL_BADGE,
      title: `บล็อก ${res.cell_id}`,
      sub: `ช่วงเวลา ${res.period} · พื้นที่มาตรฐาน 8,100 ตร.ม.`,
      html: `
        ${buildVerdictHtml(active.lst_mean, active.air_temp_mean, true)}

        <div class="d-kpis">
          <div class="d-kpi warm">
            <div class="lbl">อุณหภูมิพื้นผิว (LST)</div>
            <div class="val">${fmt(active.lst_mean, 2)} °C</div>
            <div class="sub">ช่วงพีค p90: ${fmt(active.lst_p90, 2)} °C</div>
          </div>
          <div class="d-kpi blue">
            <div class="lbl">อุณหภูมิอากาศจริง (IoT)</div>
            <div class="val">${fmt(active.air_temp_mean, 2)} °C</div>
            <div class="sub">บันทึก ${fmt(active.n_iot_minutes, 0)} นาที</div>
          </div>
          <div class="d-kpi cool">
            <div class="lbl">ดัชนีพืชพรรณ (NDVI)</div>
            <div class="val">${fmt(active.ndvi_mean, 3)}</div>
            <div class="sub">สิ่งปลูกสร้าง NDBI: ${fmt(active.ndbi_mean, 3)}</div>
          </div>
          <div class="d-kpi amber">
            <div class="lbl">ความหนาแน่นอาคาร</div>
            <div class="val">${fmt(active.builtup_pct, 1)} %</div>
            <div class="sub">${fmt(active.building_count, 0)} หลัง · FAR ${fmt(active.far, 2)}</div>
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
      `,
      onReady: () => {
        const el = document.getElementById("drawer-chart");
        if (!el) return;
        const bars = (o: Record<string, unknown>) => [Number(o.lst_mean ?? 0), Number(o.air_temp_mean ?? 0), Number(o.builtup_pct ?? 0)];
        const chart = echarts.init(el);
        chart.setOption({
          tooltip: { trigger: "axis" },
          legend: { top: 0, textStyle: { fontSize: 11 } },
          grid: { left: 40, right: 12, top: 28, bottom: 24 },
          xAxis: { type: "category", data: ["LST (°C)", "อากาศ IoT (°C)", "อาคาร (%)"] },
          yAxis: { type: "value" },
          series: [
            { name: "ทุกเวลา (All)", type: "bar", data: bars(all), itemStyle: { color: "#4f46e5", borderRadius: [4, 4, 0, 0] } },
            { name: "แดดจัด 09:30–11:30", type: "bar", data: bars(win), itemStyle: { color: "#f97316", borderRadius: [4, 4, 0, 0] } },
          ],
        });
        return chart;
      },
    });
  }
}
