import maplibregl from "maplibre-gl";
import { api, errMsg, toMinuteObjects } from "../api";
import type { ReadingsResponse } from "../types";
import { dayOf, dayRange } from "../utils/format";
import { byId, errHtml, esc, maybeById } from "../utils/html";
import { legendHtml } from "../utils/legend";
import { setVisible, type MapContext } from "./context";
import type { LivePosition } from "./live-position";
import { TRACK_STOPS, interp } from "./stops";

/** เส้นทางเดินวัดของเครื่อง Mobile ในวันที่เลือก (/readings 1min) ระบายสีตามอุณหภูมิหรือเสียง */
export class TrackLayer {
  constructor(
    private ctx: MapContext,
    private live: LivePosition,
  ) {
    const devSel = byId<HTMLSelectElement>("track-dev");
    byId<HTMLSelectElement>("track-color").onchange = () => this.style();
    devSel.onchange = () => {
      const d = ctx.devices.find((x) => x.device_id === devSel.value);
      if (d?.latest_hour_local) byId<HTMLInputElement>("track-date").value = dayOf(d.latest_hour_local);
      this.load();
    };
    byId<HTMLInputElement>("track-date").onchange = () => this.load();
    byId<HTMLInputElement>("track-on").onchange = (e) =>
      setVisible(ctx.map, ["track-pts", "track-line"], (e.target as HTMLInputElement).checked);
  }

  async load() {
    const { map } = this.ctx;
    const info = byId("track-info");
    const day = byId<HTMLInputElement>("track-date").value;
    const dev = byId<HTMLSelectElement>("track-dev").value;
    if (!dev) {
      info.textContent = "ไม่พบรายชื่ออุปกรณ์ในระบบ";
      return;
    }
    let res: ReadingsResponse;
    try {
      res = await api<ReadingsResponse>("/readings", { device: dev, ...dayRange(day), interval: "1min" });
    } catch (e: unknown) {
      info.innerHTML = errHtml(errMsg(e));
      return;
    }

    const rows = toMinuteObjects(res).filter((r) => r.lat != null && r.lon != null);
    const fc = {
      type: "FeatureCollection",
      features: rows.map((r) => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon!, r.lat!] }, properties: r })),
    } as unknown as GeoJSON.FeatureCollection;
    const line = {
      type: "Feature",
      geometry: { type: "LineString", coordinates: rows.map((r) => [r.lon!, r.lat!]) },
      properties: {},
    } as GeoJSON.Feature;
    info.innerHTML =
      rows.length > 0 ? `พบข้อมูลเส้นทางเดิน <b>${rows.length} นาที</b> (คลิกจุดเพื่อดูค่าอากาศ)` : `วันที่ ${esc(day)} ยังไม่มีพิกัดเดินวัดสำหรับ ${esc(dev)}`;

    const src = map.getSource("track") as maplibregl.GeoJSONSource | undefined;
    if (src) {
      src.setData(fc);
      (map.getSource("track-line") as maplibregl.GeoJSONSource).setData(line);
    } else {
      this.addLayers(fc, line);
    }
    this.style();

    await this.live.refresh(dev);
  }

  private addLayers(fc: GeoJSON.FeatureCollection, line: GeoJSON.Feature) {
    const { map } = this.ctx;
    const vis = maybeById<HTMLInputElement>("track-on")?.checked !== false ? "visible" : "none";
    map.addSource("track-line", { type: "geojson", data: line });
    map.addSource("track", { type: "geojson", data: fc });
    map.addLayer({
      id: "track-line",
      type: "line",
      source: "track-line",
      layout: { visibility: vis },
      paint: { "line-color": "#0f172a", "line-width": 1.5, "line-opacity": 0.35 },
    });
    map.addLayer({
      id: "track-pts",
      type: "circle",
      source: "track",
      layout: { visibility: vis },
      paint: {
        "circle-radius": 5.5,
        "circle-opacity": 0.9,
        "circle-stroke-color": "#0f172a",
        "circle-stroke-width": 1.5,
        "circle-stroke-opacity": 0.95,
      },
    });
    map.on("click", "track-pts", (e) => {
      const p = e.features?.[0]?.properties || {};
      new maplibregl.Popup()
        .setLngLat(e.lngLat)
        .setHTML(
          `<div class="pop"><b>เวลา ${esc(String(p.minute_local || "").slice(11, 16))}</b><br>
            <table>
              <tr><td>อุณหภูมิอากาศจริง</td><td><b>${esc(p.temp_c_mean)} °C</b></td></tr>
              <tr><td>ความชื้นสัมพัทธ์</td><td>${esc(p.humidity_pct_mean)} %</td></tr>
              <tr><td>ระดับเสียง</td><td>${esc(p.db_spl_mean)} dB(SPL)</td></tr>
              <tr><td>ความเข้มแสง</td><td>${esc(p.lux_mean)} lux</td></tr>
              <tr><td>บล็อกกริด</td><td>${esc(p.cell_id)}</td></tr>
            </table></div>`,
        )
        .addTo(map);
    });
  }

  style() {
    if (!this.ctx.map.getLayer("track-pts")) return;
    const v = byId<HTMLSelectElement>("track-color").value;
    this.ctx.map.setPaintProperty("track-pts", "circle-color", interp(v, TRACK_STOPS[v]));
    byId("track-legend").innerHTML = legendHtml(TRACK_STOPS[v]);
  }
}
