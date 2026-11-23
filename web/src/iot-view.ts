import * as echarts from "echarts";
import { api, toMinuteObjects } from "./api";
import { LiveAudioPlayer } from "./audio";
import type { DeviceInfo, LiveDeviceState, QualityResponse, ReadingsResponse } from "./types";

const fmt = (v: unknown, d = 1) => (v == null ? "—" : typeof v === "number" ? v.toFixed(d) : String(v));
const dayOf = (s?: string | null) => (s || "").slice(0, 10);
const nextDay = (day: string) => new Date(Date.parse(day + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);
const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

export class IotController {
  private charts: Record<string, echarts.ECharts> = {};
  private devices: DeviceInfo[] | null = null;
  private lastKey: string | null = null;
  private player: LiveAudioPlayer | null = null;
  private listeningDevice: string | null = null;

  constructor(
    private getLiveState: () => Record<string, LiveDeviceState>,
    private sendWs: (payload: unknown) => boolean
  ) {
    this.bindUi();
  }

  private chart(id: string) {
    return (this.charts[id] ||= echarts.init(document.getElementById(id)!));
  }

  resize() {
    Object.values(this.charts).forEach((c) => c.resize());
  }

  private bindUi() {
    const devSel = document.getElementById("iot-dev") as HTMLSelectElement;
    const dateIn = document.getElementById("iot-date") as HTMLInputElement;
    const dbaIn = document.getElementById("iot-dba") as HTMLInputElement;

    devSel.onchange = () => {
      const d = this.devices?.find((x) => x.device_id === devSel.value);
      if (d?.latest_hour_local) dateIn.value = dayOf(d.latest_hour_local);
      this.load();
      this.renderLive();
    };
    dateIn.onchange = () => this.load();
    dbaIn.onchange = () => this.load();

    const listenBtn = document.getElementById("listen") as HTMLButtonElement;
    listenBtn.onclick = () => {
      const id = devSel.value;
      const statusEl = document.getElementById("audio-status")!;
      if (!this.player) {
        if (!this.sendWs({ t: "listen", device_id: id })) {
          statusEl.textContent = "ยังไม่ได้เชื่อมต่อ ws /live";
          return;
        }
        this.player = new LiveAudioPlayer();
        this.listeningDevice = id;
        listenBtn.textContent = "■ หยุดฟัง";
        listenBtn.classList.add("stop");
        statusEl.innerHTML = `ส่ง <code>{"t":"listen","device_id":"${esc(id)}"}</code> แล้ว`;
      } else {
        if (this.listeningDevice) this.sendWs({ t: "unlisten", device_id: this.listeningDevice });
        this.player.stop();
        this.player = null;
        this.listeningDevice = null;
        listenBtn.textContent = "▶ ฟังเสียงสด";
        listenBtn.classList.remove("stop");
        statusEl.innerHTML = `ส่ง <code>{"t":"unlisten"}</code> แล้ว`;
      }
    };
  }

  onAudioFrame(buf: ArrayBuffer) {
    if (!this.player) return;
    this.player.onFrame(buf);
    if (this.player.frames % 5 === 0) this.renderAudioStats();
  }

  onAudioStats(lost: number) {
    if (!this.player) return;
    this.player.lost = lost;
    this.renderAudioStats();
  }

  private renderAudioStats() {
    const p = this.player;
    if (!p?.lastHeader) return;
    const h = p.lastHeader;
    document.getElementById("audio-stats")!.innerHTML =
      `<span>frame ล่าสุด</span><b>type=0x0${h.type} v${h.version} samples=${h.samples} seq=${h.seq} (${h.bytes} B)</b>` +
      `<span>รับแล้ว</span><b>${p.frames} frame (~10/วินาที)</b>` +
      `<span>audio_stats.lost</span><b class="${p.lost > 5 ? "warn" : ""}">${p.lost}${p.lost > 5 ? " สัญญาณขาด" : ""}</b>`;
  }

  async load(force = false) {
    if (!this.devices) {
      try {
        this.devices = (await api<{ devices: DeviceInfo[] }>("/devices")).devices;
      } catch (e: unknown) {
        document.getElementById("c-spl")!.innerHTML = `<p class="err">${esc(e instanceof Error ? e.message : String(e))}</p>`;
        return;
      }
    }

    const sel = document.getElementById("iot-dev") as HTMLSelectElement;
    const dateIn = document.getElementById("iot-date") as HTMLInputElement;
    if (!sel.options.length) {
      sel.innerHTML = this.devices
        .map((d) => `<option value="${esc(d.device_id)}">${esc(d.device_id)} (${esc(d.device_type)}${d.site_name ? " · " + esc(d.site_name) : ""})</option>`)
        .join("");
      const first = this.devices.find((d) => d.latest_hour_local) || this.devices[0];
      if (first) {
        sel.value = first.device_id;
        if (first.latest_hour_local) dateIn.value = dayOf(first.latest_hour_local);
      }
    }

    const dev = sel.value;
    const day = dateIn.value;
    const showDba = (document.getElementById("iot-dba") as HTMLInputElement).checked;
    const key = `${dev}|${day}|${showDba}`;
    if (!force && key === this.lastKey) return;
    this.lastKey = key;

    const range = { device: dev, from: day + "T00:00:00", to: nextDay(day) + "T00:00:00" };
    const [rd, ql] = await Promise.all([
      api<ReadingsResponse>("/readings", { interval: "1min", ...range }).catch((e) => ({ device: dev, interval: "1min" as const, from: "", to: "", columns: [], rows: [], _err: e })),
      api<QualityResponse>("/quality", range).catch(() => ({ device: dev, from: "", to: "", hours: [] })),
    ]);

    const rows = toMinuteObjects(rd);
    const x = rows.map((r) => r.minute_local.slice(11, 16));
    const errObj = (rd as { _err?: Error })._err;
    const empty = errObj ? "error: " + errObj.message : rows.length ? "" : `ไม่มีข้อมูล ${dev} วันที่ ${day}`;

    const base = (name: string) => ({
      title: { text: empty, left: "center", top: "middle", textStyle: { fontSize: 12, color: "#999" } },
      tooltip: { trigger: "axis" },
      grid: { left: 45, right: 10, top: 25, bottom: 25 },
      xAxis: { type: "category", data: x },
      yAxis: { type: "value", scale: true, name },
      dataZoom: [{ type: "inside" }],
    });

    const splSeries: unknown[] = [
      { name: "min", type: "line", data: rows.map((r) => r.db_spl_min), stack: "band", lineStyle: { opacity: 0 }, showSymbol: false },
      {
        name: "max-min",
        type: "line",
        data: rows.map((r) => (r.db_spl_max != null && r.db_spl_min != null ? +(r.db_spl_max - r.db_spl_min).toFixed(2) : null)),
        stack: "band",
        lineStyle: { opacity: 0 },
        areaStyle: { color: "rgba(15,118,110,.15)" },
        showSymbol: false,
        tooltip: { show: false },
      },
      { name: "db_spl_mean", type: "line", data: rows.map((r) => r.db_spl_mean), showSymbol: false, lineStyle: { color: "#0f766e" } },
    ];
    if (showDba) {
      splSeries.push({ name: "dba_mean", type: "line", data: rows.map((r) => r.dba_mean), showSymbol: false, lineStyle: { color: "#f59e0b" } });
    }

    this.chart("c-spl").setOption({ ...base("dB"), legend: { top: 0, data: ["db_spl_mean", "dba_mean"] }, series: splSeries }, true);
    this.chart("c-temp").setOption({ ...base("°C"), series: [{ type: "line", data: rows.map((r) => r.temp_c_mean), showSymbol: false, lineStyle: { color: "#d7191c" } }] }, true);
    this.chart("c-hum").setOption({ ...base("%"), series: [{ type: "line", data: rows.map((r) => r.humidity_pct_mean), showSymbol: false, lineStyle: { color: "#2c7bb6" } }] }, true);
    this.chart("c-lux").setOption({ ...base("lux"), series: [{ type: "line", data: rows.map((r) => r.lux_mean), showSymbol: false, lineStyle: { color: "#eab308" } }] }, true);

    this.chart("c-quality").setOption(
      {
        tooltip: {
          trigger: "axis",
          formatter: (ps: { dataIndex: number }[]) => {
            const h = ql.hours[ps[0].dataIndex];
            return `${esc(h.batch_id)}<br>loss_rate ${(h.loss_rate * 100).toFixed(2)}%<br>expected ${h.expected_rows} · missing ${h.missing_rows}<br>duplicate ${h.duplicate_rows} · invalid ${h.invalid_rows} · sessions ${h.sessions}`;
          },
        },
        grid: { left: 45, right: 10, top: 20, bottom: 25 },
        xAxis: { type: "category", data: ql.hours.map((h) => h.hour_local.slice(11, 13) + ":00") },
        yAxis: { type: "value", name: "loss %", axisLabel: { formatter: (v: number) => v + "%" } },
        series: [
          {
            type: "bar",
            data: ql.hours.map((h) => +(h.loss_rate * 100).toFixed(2)),
            itemStyle: { color: (p: { value: number }) => (p.value > 2 ? "#dc2626" : "#16a34a") },
          },
        ],
      },
      true
    );

    this.renderLive();
  }

  renderLive() {
    const liveMap = this.getLiveState();
    document.getElementById("live-mini")!.innerHTML = Object.entries(liveMap)
      .map(
        ([id, L]) =>
          `<div class="lv"><span class="dot ${L.online ? "on" : "off"}"></span><b>${esc(id)}</b> ${
            L.online ? `${fmt(L.temp_c)} °C · ${fmt(L.db_spl)} dB(SPL) · ${L.ts ? esc(L.ts.slice(11)) : ""}` : "offline"
          }</div>`
      )
      .join("");

    const id = (document.getElementById("iot-dev") as HTMLSelectElement).value;
    const L = liveMap[id];
    const bigEl = document.getElementById("live-big")!;
    if (!L) {
      bigEl.innerHTML = `<p class="note">ยังไม่มีข้อความของ ${esc(id)}</p>`;
      return;
    }
    if (!L.online || !L.ts) {
      bigEl.innerHTML = `<p><span class="dot off"></span>${esc(id)} offline (hub ส่ง <code>{"t":"status","online":false}</code>)</p>`;
      return;
    }
    bigEl.innerHTML = `<div><span class="dot on"></span>${esc(id)} · ${esc(L.ts)}</div><div class="big">${fmt(L.db_spl)} dB(SPL)</div>
      <div class="kv small"><span>dBA</span><b>${fmt(L.dba)}</b><span>อุณหภูมิ</span><b>${fmt(L.temp_c)} °C</b><span>ความชื้น</span><b>${fmt(L.humidity_pct)} %</b><span>แสง</span><b>${fmt(L.lux, 0)} lux</b>
      <span>GPS</span><b>${fmt(L.lat, 5)}, ${fmt(L.lon, 5)}</b><span>heap / rssi</span><b>${L.health?.heap_free ?? "—"} B · ${L.health?.rssi ?? "—"} dBm</b></div>`;

    this.player?.updateMockLevel(L.db_spl);
  }
}