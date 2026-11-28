import type maplibregl from "maplibre-gl";
import { api, errMsg } from "../api";
import type { AnalyticsHotspot } from "../types";
import { byId, errHtml } from "../utils/html";
import type { MapContext } from "./context";

const HOT_COLORS = [
  "match",
  ["get", "class"],
  "hot_99",
  "#b2182b",
  "hot_95",
  "#ef8a62",
  "cold_95",
  "#67a9cf",
  "cold_99",
  "#2166ac",
  "#ccc",
];

/** จุดร้อน/เย็นที่มีนัยสำคัญ (Gi*) จาก /analytics/hotspot วาดบน geometry ของกริด */
export class HotspotLayer {
  constructor(private ctx: MapContext) {
    byId<HTMLInputElement>("hot-on").onchange = (e) => {
      if (ctx.map.getLayer("hot-fill")) {
        ctx.map.setLayoutProperty("hot-fill", "visibility", (e.target as HTMLInputElement).checked ? "visible" : "none");
      }
    };
  }

  async load() {
    const { map, grid } = this.ctx;
    const note = byId("hot-note");
    if (!grid) {
      note.textContent = "รอข้อมูลกริดสำหรับวิเคราะห์จุดร้อน";
      return;
    }
    try {
      const res = await api<AnalyticsHotspot>("/analytics/hotspot", { period: this.ctx.getPeriod(), match: this.ctx.getMatch() });
      note.textContent = "";
      const geom = Object.fromEntries(grid.features.map((f) => [f.id, f.geometry]));
      const fc = {
        type: "FeatureCollection",
        features: res.cells
          .filter((c) => c.class !== "not_significant" && geom[c.cell_id])
          .map((c) => ({ type: "Feature", geometry: geom[c.cell_id], properties: c })),
      } as unknown as GeoJSON.FeatureCollection;
      const src = map.getSource("hot") as maplibregl.GeoJSONSource | undefined;
      if (src) {
        src.setData(fc);
      } else {
        map.addSource("hot", { type: "geojson", data: fc });
        map.addLayer(
          {
            id: "hot-fill",
            type: "fill",
            source: "hot",
            layout: { visibility: "none" },
            paint: { "fill-opacity": 0.65, "fill-color": HOT_COLORS as maplibregl.ExpressionSpecification },
          },
          "grid-hit",
        );
      }
    } catch (e: unknown) {
      note.innerHTML = errHtml(errMsg(e));
    }
  }
}
