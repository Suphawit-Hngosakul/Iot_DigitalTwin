import * as echarts from "echarts";
import { api, errMsg } from "../api";
import { CFG } from "../config";
import type { DeviceInfo } from "../types";
import { fmtNum } from "../utils/format";
import { byId, esc, maybeById } from "../utils/html";
import { renderLiveCharts, type LiveCharts, type LiveSample } from "./live-charts";

const MAX_POINTS = 60;
const POLL_MS = 2000;

type PillKind = "live" | "idle" | "paused" | "connecting" | "offline";

const nowHms = () => new Date().toTimeString().slice(0, 8);
const num = (...vals: unknown[]) => Number(vals.find((v) => v != null));

/**
 * แท็บ IoT › Realtime สด
 * - มี hub_live_url → WebSocket hub (spec 22) ถ้าต่อไม่ได้จะสลับไปถาม /latest ทุก 2 วินาที
 * - ไม่มี hub → ถาม /latest ทุก 2 วินาที (ค่าเป็นรายนาที กราฟจึงเพิ่มจุดเมื่อมีนาทีใหม่เท่านั้น)
 */
export class IotLiveController {
  private devices: DeviceInfo[] = [];
  private samples: LiveSample[] = [];
  private charts: LiveCharts = { sound: null, temp: null, hum: null };
  private timer: number | null = null;
  private ws: WebSocket | null = null;
  private paused = false;
  private lastApiTs = "";

  async init() {
    this.bindUi();
    await this.loadDevices();
    this.initCharts();
    this.startStream();
  }

  resize() {
    this.charts.sound?.resize();
    this.charts.temp?.resize();
    this.charts.hum?.resize();
  }

  private get deviceId() {
    return maybeById<HTMLSelectElement>("live-dev")?.value || "";
  }

  private bindUi() {
    byId<HTMLSelectElement>("live-dev").addEventListener("change", () => {
      this.samples = [];
      this.lastApiTs = "";
      this.render();
      if (!this.ws) this.tick();
    });

    const pauseBtn = byId<HTMLButtonElement>("live-pause-btn");
    pauseBtn.addEventListener("click", () => {
      this.paused = !this.paused;
      pauseBtn.textContent = this.paused ? "▶ รับข้อมูลต่อ" : "⏸ หยุดชั่วคราว";
      if (this.paused) this.setPill("paused", "⏸ หยุดอัปเดตชั่วคราว");
      else this.tick();
    });

    byId("live-clear-btn").addEventListener("click", () => {
      this.samples = [];
      this.render();
    });
  }

  private async loadDevices() {
    try {
      this.devices = (await api<{ devices: DeviceInfo[] }>("/devices")).devices || [];
    } catch {
      this.devices = [];
    }

    const sel = byId<HTMLSelectElement>("live-dev");
    if (!this.devices.length) {
      sel.innerHTML = `<option value="">ไม่พบอุปกรณ์</option>`;
      return;
    }
    sel.innerHTML = this.devices
      .map((d) => `<option value="${esc(d.device_id)}">${esc(d.device_id)}${d.site_name ? ` · ${esc(d.site_name)}` : ""}</option>`)
      .join("");
  }

  private initCharts() {
    const make = (id: string) => {
      const el = maybeById(id);
      if (!el) return null;
      const chart = echarts.init(el);
      new ResizeObserver(() => chart.resize()).observe(el);
      return chart;
    };
    this.charts = { sound: make("live-c-sound"), temp: make("live-c-temp"), hum: make("live-c-hum") };
    this.render();
  }

  private setPill(kind: PillKind, text: string) {
    const el = maybeById("live-status-pill");
    if (!el) return;
    el.className = `live-pill ${kind}`;
    el.textContent = text;
  }

  // ── แหล่งข้อมูล ────────────────────────────────────────────

  private startStream() {
    this.stopStream();
    if (CFG.hub_live_url) {
      this.connectWebSocket(CFG.hub_live_url);
      return;
    }
    this.tick();
    this.startPolling();
  }

  private startPolling() {
    this.timer = window.setInterval(() => {
      if (!this.paused) this.tick();
    }, POLL_MS);
  }

  private stopStream() {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  private connectWebSocket(url: string) {
    try {
      this.setPill("connecting", "● กำลังเชื่อม WebSocket…");
      this.ws = new WebSocket(url);
      this.ws.onopen = () => this.setPill("live", "● LIVE · WebSocket Stream");
      this.ws.onmessage = (ev) => this.onHubMessage(ev.data);
      this.ws.onerror = () => {
        this.ws?.close();
        this.startPolling();
      };
    } catch {
      this.startPolling();
    }
  }

  /** ข้อความจาก hub (spec 01 §3): ใช้เฉพาะ t="reading" ของเครื่องที่เลือก; status / audio / binary ข้ามไป */
  private onHubMessage(data: unknown) {
    if (this.paused || typeof data !== "string") return;
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    if (msg.t != null && msg.t !== "reading") return;
    if (msg.device_id && msg.device_id !== this.deviceId) return;
    this.pushSample({
      ts: nowHms(),
      temp: num(msg.temp_c, msg.temp_c_mean),
      hum: num(msg.humidity_pct, msg.humidity_pct_mean),
      spl: num(msg.db_spl, msg.db_spl_mean),
      dba: num(msg.dba, msg.dba_mean),
      lux: num(msg.lux, msg.lux_mean),
      lat: (msg.lat as number) ?? null,
      lon: (msg.lon as number) ?? null,
      cellId: (msg.cell_id as string) ?? null,
    });
  }

  private async tick() {
    const devId = this.deviceId;
    if (!devId) {
      this.setPill("offline", "● ไม่พบอุปกรณ์ใน /devices");
      return;
    }

    try {
      const r = (await api<{ row: Record<string, unknown> }>("/latest", { device: devId })).row || {};
      const apiTs = String(r.minute_local || r.ts || "");
      if (!apiTs || apiTs === this.lastApiTs) {
        this.setPill("idle", "● LIVE · รอข้อมูลนาทีถัดไปจาก /latest");
        return;
      }
      this.lastApiTs = apiTs;
      this.setPill("live", "● LIVE · เชื่อมต่อ AWS API (/latest)");
      this.pushSample({
        ts: apiTs.slice(11, 19) || nowHms(),
        temp: Number(r.temp_c_mean ?? r.temp_c),
        hum: Number(r.humidity_pct_mean ?? r.humidity_pct),
        spl: Number(r.db_spl_mean ?? r.db_spl),
        dba: Number(r.dba_mean ?? r.dba),
        lux: Number(r.lux_mean ?? r.lux),
        lat: r.lat != null ? Number(r.lat) : null,
        lon: r.lon != null ? Number(r.lon) : null,
        cellId: r.cell_id ? String(r.cell_id) : null,
      });
    } catch (e: unknown) {
      this.setPill("offline", `● ออฟไลน์ · ${errMsg(e)}`);
    }
  }

  // ── แสดงผล ────────────────────────────────────────────────

  private pushSample(s: LiveSample) {
    this.samples.push(s);
    if (this.samples.length > MAX_POINTS) this.samples.shift();
    this.renderKpis(s);
    this.render();
  }

  private renderKpis(s: LiveSample) {
    const set = (id: string, text: string) => {
      const el = maybeById(id);
      if (el) el.textContent = text;
    };
    set("live-last-ts", `อัปเดตล่าสุด: ${s.ts}`);
    set("lv-temp", `${fmtNum(s.temp, 2)} °C`);
    set("lv-spl", `${fmtNum(s.spl, 1)} dB`);
    set("lv-hum", `${fmtNum(s.hum, 1)} %`);
    set("lv-lux", `${Number.isNaN(s.lux) ? "—" : s.lux.toLocaleString()} lux`);

    const badge = maybeById("lv-temp-badge");
    if (badge && !Number.isNaN(s.temp)) {
      const [cls, text] =
        s.temp >= 33 ? ["danger", "ร้อนอันตราย (เสี่ยงเพลียแดด)"] : s.temp >= 30 ? ["warn", "เริ่มร้อนอบอ้าว (เฝ้าระวัง)"] : ["ok", "อากาศสบาย / ปกติ"];
      badge.innerHTML = `<span class="temp-flag ${cls}">${text}</span>`;
    }

    const noise = s.spl >= 75 ? "ดังมาก (จราจรหนาแน่น)" : s.spl >= 65 ? "ปานกลาง" : "เงียบสงบ";
    set("lv-dba", `dBA: ${fmtNum(s.dba, 1)} · ${noise}`);

    const coord = s.lat != null && s.lon != null ? `${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}` : "สถานีคงที่";
    set("lv-gps", `พิกัด: ${coord} · บล็อก: ${s.cellId || "—"}`);
  }

  private render() {
    renderLiveCharts(this.charts, this.samples);
  }
}
