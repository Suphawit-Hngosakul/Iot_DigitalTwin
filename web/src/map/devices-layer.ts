import * as echarts from "echarts";
import maplibregl from "maplibre-gl";
import { api, errMsg, toMinuteObjects } from "../api";
import type { DeviceInfo, ReadingsResponse } from "../types";
import { dayOf, dayRange } from "../utils/format";
import { byId, errHtml, esc } from "../utils/html";
import type { MapContext } from "./context";

const isStationary = (d: DeviceInfo) => String(d.device_type || "").toLowerCase() === "stationary";

/** รายชื่อเครื่องจาก /devices → ตัวเลือกเครื่องของเส้นทางเดิน + หมุด station พร้อม popup กราฟรายวัน */
export class DevicesLayer {
  private stationMarkers: maplibregl.Marker[] = [];

  constructor(private ctx: MapContext) {}

  async load() {
    try {
      const res = await api<{ devices: DeviceInfo[] }>("/devices");
      this.ctx.devices = res.devices;
      this.fillTrackSelect(res.devices);
      this.placeStations(res.devices);
    } catch {
      // /devices ยังไม่มีข้อมูล → เงียบไว้ (แผง IoT จะขึ้นว่าไม่พบอุปกรณ์)
    }
  }

  private fillTrackSelect(devices: DeviceInfo[]) {
    const mobiles = devices.filter((x) => !isStationary(x));
    const list = mobiles.length ? mobiles : devices;
    const sel = byId<HTMLSelectElement>("track-dev");
    sel.innerHTML = list.map((d) => `<option value="${esc(d.device_id)}">${esc(d.device_id)}</option>`).join("");
    const first = list.find((d) => d.latest_hour_local) || list[0];
    if (first) {
      sel.value = first.device_id;
      if (first.latest_hour_local) byId<HTMLInputElement>("track-date").value = dayOf(first.latest_hour_local);
    }
  }

  private placeStations(devices: DeviceInfo[]) {
    this.stationMarkers.forEach((m) => m.remove());
    this.stationMarkers = [];
    for (const d of devices.filter((x) => isStationary(x) && x.fixed_lat != null && x.fixed_lon != null)) {
      const el = document.createElement("div");
      el.className = "pin";
      el.title = d.site_name || d.device_id;
      el.onclick = (ev) => {
        ev.stopPropagation();
        this.stationPopup(d);
      };
      this.stationMarkers.push(new maplibregl.Marker({ element: el }).setLngLat([d.fixed_lon!, d.fixed_lat!]).addTo(this.ctx.map));
    }
  }

  private async stationPopup(d: DeviceInfo) {
    const div = document.createElement("div");
    div.className = "pop station-pop";
    div.innerHTML = `<b>${esc(d.device_id)}</b> · ${esc(d.site_name || "")}<div class="kv" data-role="kv">…</div><div class="station-pop-chart" data-role="chart"></div>`;
    new maplibregl.Popup({ maxWidth: "320px" }).setLngLat([d.fixed_lon!, d.fixed_lat!]).setDOMContent(div).addTo(this.ctx.map);
    const kv = div.querySelector<HTMLElement>("[data-role=kv]")!;

    try {
      const latest = await api<{ row: Record<string, unknown> }>("/latest", { device: d.device_id });
      const r = latest.row;
      kv.innerHTML = `<span>เวลาล่าสุด</span><b>${esc(r.minute_local)}</b><span>อุณหภูมิ</span><b>${esc(r.temp_c_mean)} °C</b><span>ความชื้น</span><b>${esc(r.humidity_pct_mean)} %</b><span>เสียง</span><b>${esc(r.db_spl_mean)} dB</b>`;
      const day = dayOf(String(r.minute_local));
      const res = await api<ReadingsResponse>("/readings", { device: d.device_id, ...dayRange(day), interval: "1min" });
      const rows = toMinuteObjects(res).filter((_, i) => i % 10 === 0);
      echarts.init(div.querySelector<HTMLElement>("[data-role=chart]")!).setOption({
        grid: { left: 34, right: 8, top: 20, bottom: 20 },
        title: { text: "อุณหภูมิอากาศ " + day, textStyle: { fontSize: 11 } },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: rows.map((row) => row.minute_local.slice(11, 16)) },
        yAxis: { type: "value", scale: true },
        series: [{ type: "line", data: rows.map((row) => row.temp_c_mean), showSymbol: false, lineStyle: { color: "#7c3aed" } }],
      });
    } catch (e: unknown) {
      kv.innerHTML = errHtml(errMsg(e));
    }
  }
}
