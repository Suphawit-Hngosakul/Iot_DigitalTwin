import type maplibregl from "maplibre-gl";
import { api, errMsg } from "../api";
import type { LayersResponse } from "../types";
import { byId, errHtml } from "../utils/html";
import { legendHtml, normStops } from "../utils/legend";
import { boundsOf, type MapContext } from "./context";
import { GRID_STOPS, LAYER_NAMES, LAYER_TO_GRID_VAR } from "./stops";

type ImageCoords = [[number, number], [number, number], [number, number], [number, number]];

/** ภาพ PNG 30 m ของ LST / NDVI / NDBI / ความหนาแน่นอาคาร จาก /layers พร้อมแผงเปิด-ปิดและความโปร่งใส */
export class SatelliteLayers {
  constructor(
    private ctx: MapContext,
    /** แจ้ง period ปัจจุบัน + รายการ period ทั้งหมด ให้ตัวเลือกช่วงเวลา (ครั้งแรกที่ยังไม่มี period) */
    private onPeriods: (period: string, periods: LayersResponse["periods"]) => void,
  ) {}

  async load() {
    const { map } = this.ctx;
    const note = byId("layer-note");
    const period = this.ctx.getPeriod();
    let res: LayersResponse;
    try {
      // period รายเดือนไม่มี layer ดาวเทียมของตัวเอง → ให้ API เลือก composite ล่าสุด
      res = await api<LayersResponse>("/layers", { period: period && !period.startsWith("month_") ? period : null });
      note.textContent = "";
    } catch (e: unknown) {
      note.innerHTML = errHtml(`ไม่สามารถโหลดชั้นข้อมูลดาวเทียม (${errMsg(e)})`);
      return;
    }

    const all = [...res.layers, ...res.static_layers].filter((L) => L.url && L.coordinates);
    for (const L of all) {
      const st = normStops(L.legend);
      if (!st) continue;
      L.stops = st;
      const g = LAYER_TO_GRID_VAR[L.layer];
      if (g) GRID_STOPS[g] = st;
    }

    if (!this.ctx.fitted && all.length) {
      map.fitBounds(boundsOf(all[0].coordinates), { padding: 30, duration: 0, pitch: 30 });
      this.ctx.fitted = true;
    }

    const box = byId("layer-controls");
    const beforeId = map.getLayer("grid-fill") ? "grid-fill" : map.getLayer("bldg-3d") ? "bldg-3d" : undefined;

    for (const L of all) {
      const id = "img-" + L.layer;
      const coordinates = L.coordinates as ImageCoords;
      const src = map.getSource(id) as maplibregl.ImageSource | undefined;
      if (src) {
        src.updateImage({ url: L.url, coordinates });
        continue;
      }
      map.addSource(id, { type: "image", url: L.url, coordinates });
      const on = L.layer === "lst";
      map.addLayer(
        {
          id,
          type: "raster",
          source: id,
          paint: { "raster-opacity": 0.65, "raster-resampling": "nearest" },
          layout: { visibility: on ? "visible" : "none" },
        },
        beforeId,
      );

      const div = document.createElement("div");
      div.innerHTML = `<div class="layer-row"><label class="row"><input type="checkbox" ${on ? "checked" : ""}/> ${LAYER_NAMES[L.layer] || L.layer}</label><input type="range" min="0" max="100" value="65" title="ความโปร่งใส"/></div>
        <div class="legend">${L.stops ? legendHtml(L.stops, L.legend.unit) : ""}</div>`;
      const [cb, rg] = div.querySelectorAll("input");
      cb.onchange = () => map.setLayoutProperty(id, "visibility", cb.checked ? "visible" : "none");
      rg.oninput = () => map.setPaintProperty(id, "raster-opacity", Number(rg.value) / 100);
      box.appendChild(div);
    }

    if (!this.ctx.getPeriod()) this.onPeriods(res.period, res.periods);
  }
}
