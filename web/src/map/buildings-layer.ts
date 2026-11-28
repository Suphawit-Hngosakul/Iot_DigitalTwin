import type maplibregl from "maplibre-gl";
import { api } from "../api";
import type { BuildingsGeoJson, GridCellDetail } from "../types";
import { fmt } from "../utils/format";
import { byId, esc } from "../utils/html";
import { boundsOf, pointerOn, setVisible, type MapContext } from "./context";
import { buildVerdictHtml } from "./heat-verdict";

/** ไฟล์อาคารที่แปลงจาก building.shp (D26) — วางไว้ที่ web/public/data/ ก่อน build */
const BUILDINGS_URL = "./data/buildings.geojson";

/** อาคาร 3D (fill-extrusion) + Dashboard อาคารรายหลังเมื่อคลิก */
export class BuildingsLayer {
  private pitched = true;

  constructor(private ctx: MapContext) {
    const { map } = ctx;
    byId<HTMLInputElement>("bldg-on").onchange = (e) =>
      setVisible(map, ["bldg-3d", "bldg-outline"], (e.target as HTMLInputElement).checked);

    const tiltBtn = byId<HTMLButtonElement>("bldg-tilt");
    tiltBtn.onclick = () => {
      this.pitched = !this.pitched;
      map.easeTo({ pitch: this.pitched ? 45 : 0, bearing: this.pitched ? -15 : 0, duration: 500 });
      tiltBtn.textContent = this.pitched ? "มุมมองบน 2D" : "เอียงมุมมอง 3D";
    };

    map.on("click", "bldg-3d", (e) => {
      const feat = e.features?.[0];
      ctx.drawer.highlight((feat?.geometry as GeoJSON.Geometry) || null);
      // อาคารจาก building.shp ไม่มี cell_id → หาเซลล์กริดที่อยู่ใต้จุดที่คลิก
      const cell = map.queryRenderedFeatures(e.point, { layers: map.getLayer("grid-hit") ? ["grid-hit"] : [] })[0];
      this.openDashboard(feat?.properties || {}, e.lngLat, (cell?.properties?.cell_id as string) || null);
    });
    pointerOn(map, "bldg-3d");
  }

  private async fetchBuildings(): Promise<BuildingsGeoJson | null> {
    try {
      const r = await fetch(BUILDINGS_URL, { cache: "no-store" });
      if (r.ok) return await r.json();
    } catch {
      // ไม่มีไฟล์ หรือไฟล์ไม่ใช่ JSON
    }
    return null;
  }

  async load() {
    const { map } = this.ctx;
    const data = await this.fetchBuildings();
    const note = byId("bldg-note");
    if (!data) {
      note.innerHTML = `ยังไม่มีไฟล์ <code>data/buildings.geojson</code> (แปลงจาก building.shp ด้วย scripts/convert_buildings.py)`;
      return;
    }
    note.innerHTML = `โหลด <b>${data.features.length.toLocaleString()} อาคาร</b> จาก <code>building.shp</code>`;

    map.addSource("buildings", { type: "geojson", data: data as unknown as GeoJSON.FeatureCollection });
    if (data.features.length > 0 && !this.ctx.fitted) {
      const pts = data.features.flatMap((f) => f.geometry.coordinates[0] || []);
      if (pts.length) {
        map.fitBounds(boundsOf(pts), { padding: 40, duration: 0, pitch: 35 });
        this.ctx.fitted = true;
      }
    }
    map.addLayer({
      id: "bldg-3d",
      type: "fill-extrusion",
      source: "buildings",
      paint: {
        "fill-extrusion-height": ["coalesce", ["get", "height_m"], 6],
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 1,
        "fill-extrusion-vertical-gradient": true,
        "fill-extrusion-color": ["step", ["coalesce", ["get", "floors"], 2], "#fde68a", 3, "#f97316", 6, "#b91c1c"],
      },
    });
    map.addLayer({
      id: "bldg-outline",
      type: "line",
      source: "buildings",
      paint: { "line-color": "#7c2d12", "line-width": 0.5, "line-opacity": 0.6 },
    });
  }

  private async openDashboard(props: Record<string, unknown>, lngLat: maplibregl.LngLat, gridCell: string | null) {
    const cid = (props.cell_id as string) || gridCell;
    const bldgSrc = String(props.bldg_source || "building.shp");

    this.ctx.drawer.open({
      theme: "amber",
      badge: "อาคารรายหลัง 3D · building.shp",
      title: String(props.bldg_id || "อาคารทั่วไป"),
      sub: `ประเภท: ${String(props.bldg_type || "อาคารทั่วไป")} · พิกัด ${lngLat.lat.toFixed(5)}, ${lngLat.lng.toFixed(5)}`,
      html: `
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
    `,
    });

    if (!cid) return;
    try {
      const res = await api<GridCellDetail>("/grid/" + cid, { period: this.ctx.getPeriod() });
      const d = res.match?.[this.ctx.getMatch()] || res.match?.all || {};
      const box = document.getElementById("bldg-grid-context");
      if (!box) return;
      box.innerHTML = `
        <h4>บริบทความร้อนรอบอาคาร · บล็อก ${esc(cid)} (90×90 ม.)</h4>
        ${buildVerdictHtml(d.lst_mean, d.air_temp_mean, true)}
        <div class="d-kpis d-kpis-spaced">
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
}
