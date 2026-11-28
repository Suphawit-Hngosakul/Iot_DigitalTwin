import maplibregl from "maplibre-gl";
import { api } from "../api";
import { fmt } from "../utils/format";
import { esc, maybeById } from "../utils/html";

const REFRESH_MS = 10_000;

interface LiveInfo {
  devId: string;
  ts: string;
  row: Record<string, unknown>;
  coords: [number, number];
}

/** หมุด "ตำแหน่งปัจจุบัน" ของเครื่องที่เลือกในแผง IoT (จาก /latest ทุก 10 วินาที) */
export class LivePosition {
  private marker: maplibregl.Marker | null = null;
  private info: LiveInfo | null = null;
  private timer: number | null = null;

  constructor(private map: maplibregl.Map) {
    maybeById("iot-focus-now")?.addEventListener("click", () => {
      if (this.info) {
        map.easeTo({ center: this.info.coords, zoom: Math.max(map.getZoom(), 16.5), duration: 500 });
      }
    });
  }

  async refresh(devId: string) {
    if (!devId) return;
    const nowText = maybeById("iot-now-text");
    const nowMeta = maybeById("iot-now-meta");

    try {
      const latest = await api<{ row?: Record<string, unknown> }>("/latest", { device: devId });
      const r = latest.row || {};
      const lat = r.lat != null ? Number(r.lat) : null;
      const lon = r.lon != null ? Number(r.lon) : null;
      const ts = String(r.minute_local || r.ts || "");

      if (lat != null && lon != null && !Number.isNaN(lat) && !Number.isNaN(lon)) {
        this.info = { devId, ts, row: r, coords: [lon, lat] };
        this.placeMarker(devId, ts, [lon, lat]);
        if (nowText) nowText.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
        if (nowMeta) nowMeta.textContent = `อัปเดต ${ts || "ล่าสุด"} · อุณหภูมิ ${fmt(r.temp_c_mean ?? r.temp_c, 1)} °C`;
      } else {
        this.info = null;
        this.marker?.remove();
        this.marker = null;
        if (nowText) nowText.textContent = "ไม่มีพิกัด GPS ในข้อมูลล่าสุด";
        if (nowMeta) nowMeta.textContent = "แสดงเฉพาะเส้นทางเดินย้อนหลังตามวันที่เลือก";
      }
    } catch {
      if (nowText) nowText.textContent = "ดึงพิกัดล่าสุดไม่สำเร็จ";
      if (nowMeta) nowMeta.textContent = "ตรวจสอบการเชื่อมต่อ /latest API";
    }

    if (this.timer === null) {
      this.timer = window.setInterval(() => {
        const curDev = maybeById<HTMLSelectElement>("track-dev")?.value;
        if (curDev) this.refresh(curDev);
      }, REFRESH_MS);
    }
  }

  private placeMarker(devId: string, ts: string, lngLat: [number, number]) {
    if (this.marker) {
      this.marker.setLngLat(lngLat);
      const lbl = this.marker.getElement().querySelector(".iot-live-badge");
      if (lbl) lbl.textContent = `${devId} · ${ts ? ts.slice(11, 19) : "ปัจจุบัน"}`;
      return;
    }
    const el = document.createElement("div");
    el.className = "iot-live-marker";
    el.innerHTML = `<div class="iot-live-badge">${esc(devId)} · ปัจจุบัน</div><div class="iot-live-dot"></div>`;
    el.onclick = (ev) => {
      ev.stopPropagation();
      this.showPopup();
    };
    this.marker = new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat(lngLat).addTo(this.map);
  }

  private showPopup() {
    const cur = this.info;
    if (!cur) return;
    const cr = cur.row;
    const [lon, lat] = cur.coords;
    new maplibregl.Popup()
      .setLngLat(cur.coords)
      .setHTML(
        `<div class="pop"><b>🔴 พิกัดปัจจุบัน ${esc(cur.devId)}</b><br>
                <table>
                  <tr><td>เวลาล่าสุด</td><td><b>${esc(cur.ts || "ล่าสุด")}</b></td></tr>
                  <tr><td>อุณหภูมิอากาศ</td><td><b>${esc(cr.temp_c_mean ?? cr.temp_c ?? "—")} °C</b></td></tr>
                  <tr><td>ระดับเสียง</td><td>${esc(cr.db_spl_mean ?? cr.db_spl ?? "—")} dB</td></tr>
                  <tr><td>พิกัด</td><td>${lat.toFixed(5)}, ${lon.toFixed(5)}</td></tr>
                </table></div>`,
      )
      .addTo(this.map);
  }
}
