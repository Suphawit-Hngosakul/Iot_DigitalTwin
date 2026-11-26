import * as echarts from "echarts";
import { api } from "./api";
import type { DeviceInfo } from "./types";

interface LiveSample {
    ts: string;
    temp: number;
    hum: number;
    spl: number;
    dba: number;
    lux: number;
    lat?: number | null;
    lon?: number | null;
    cellId?: string | null;
}

const MAX_POINTS = 60;

class RealtimeIotController {
    private devices: DeviceInfo[] = [];
    private samples: LiveSample[] = [];
    private soundChart: echarts.ECharts | null = null;
    private tempChart: echarts.ECharts | null = null;
    private humChart: echarts.ECharts | null = null;
    private timer: number | null = null;
    private ws: WebSocket | null = null;
    private wsUrl: string | null = null;
    private apiBase = "";
    private paused = false;
    private lastApiTs = "";
    private staleCount = 0;

    async init() {
        this.bindUi();
        await this.loadConfigAndDevices();
        this.initCharts();
        this.startStream();
    }

    private bindUi() {
        const btnLive = document.getElementById("iot-mode-live");
        const btnHist = document.getElementById("iot-mode-hist");
        const subLive = document.getElementById("iot-sub-live");
        const subHist = document.getElementById("iot-sub-hist");

        btnLive?.addEventListener("click", () => {
            btnLive.classList.add("active");
            btnHist?.classList.remove("active");
            subLive?.classList.remove("hidden");
            subHist?.classList.add("hidden");
            this.resizeCharts();
        });

        btnHist?.addEventListener("click", () => {
            btnHist.classList.add("active");
            btnLive?.classList.remove("active");
            subHist?.classList.remove("hidden");
            subLive?.classList.add("hidden");
            window.dispatchEvent(new Event("resize"));
        });

        document.getElementById("tab-btn-iot")?.addEventListener("click", () => {
            requestAnimationFrame(() => {
                this.resizeCharts();
                window.dispatchEvent(new Event("resize"));
            });
        });

        const devSel = document.getElementById("live-dev") as HTMLSelectElement | null;
        devSel?.addEventListener("change", () => {
            this.samples = [];
            this.lastApiTs = "";
            this.staleCount = 0;
            this.renderCharts();
            this.tick();
        });

        const modeSel = document.getElementById("live-source-mode") as HTMLSelectElement | null;
        modeSel?.addEventListener("change", () => {
            this.lastApiTs = "";
            this.staleCount = 0;
            this.restartStream();
        });

        const pauseBtn = document.getElementById("live-pause-btn") as HTMLButtonElement | null;
        pauseBtn?.addEventListener("click", () => {
            this.paused = !this.paused;
            pauseBtn.textContent = this.paused ? "▶ รับข้อมูลต่อ" : "⏸ หยุดชั่วคราว";
            if (this.paused) {
                this.setPill("paused", "⏸ หยุดอัปเดตชั่วคราว");
            } else {
                this.tick();
            }
        });

        document.getElementById("live-clear-btn")?.addEventListener("click", () => {
            this.samples = [];
            this.renderCharts();
        });
    }

    private async loadConfigAndDevices() {
        try {
            const cfgRes = await fetch("./config.json", { cache: "no-store" });
            if (cfgRes.ok) {
                const cfg = await cfgRes.json();
                if (cfg.hub_live_url || cfg.ws_url) {
                    this.wsUrl = String(cfg.hub_live_url || cfg.ws_url);
                }
                if (cfg.api_url) {
                    this.apiBase = String(cfg.api_url).replace(/\/$/, "");
                }
            }
        } catch {
            // ไม่มี config.json ในเครื่อง Local
        }

        try {
            if (this.apiBase) {
                const r = await fetch(`${this.apiBase}/devices`);
                if (r.ok) {
                    const data = await r.json();
                    this.devices = data.devices || [];
                }
            } else {
                const res = await api<{ devices: DeviceInfo[] }>("/devices");
                this.devices = res.devices || [];
            }
        } catch {
            this.devices = [];
        }

        if (!this.devices.length) {
            this.devices = [
                { device_id: "IOTW-A001", site_name: "หน่วยตรวจวัดที่ 1" } as DeviceInfo,
                { device_id: "IOTW-A002", site_name: "หน่วยตรวจวัดที่ 2" } as DeviceInfo,
            ];
        }

        const sel = document.getElementById("live-dev") as HTMLSelectElement | null;
        if (sel) {
            sel.innerHTML = this.devices
                .map((d) => `<option value="${d.device_id}">${d.device_id}${d.site_name ? ` · ${d.site_name}` : ""}</option>`)
                .join("");
        }
    }

    private initCharts() {
        const soundEl = document.getElementById("live-c-sound");
        const tempEl = document.getElementById("live-c-temp");
        const humEl = document.getElementById("live-c-hum");

        if (soundEl) {
            this.soundChart = echarts.init(soundEl);
            new ResizeObserver(() => this.soundChart?.resize()).observe(soundEl);
        }
        if (tempEl) {
            this.tempChart = echarts.init(tempEl);
            new ResizeObserver(() => this.tempChart?.resize()).observe(tempEl);
        }
        if (humEl) {
            this.humChart = echarts.init(humEl);
            new ResizeObserver(() => this.humChart?.resize()).observe(humEl);
        }
        this.renderCharts();
    }

    private resizeCharts() {
        this.soundChart?.resize();
        this.tempChart?.resize();
        this.humChart?.resize();
    }

    private setPill(kind: "live" | "demo" | "paused" | "connecting", text: string) {
        const el = document.getElementById("live-status-pill");
        if (!el) return;
        el.className = `live-pill ${kind}`;
        el.textContent = text;
    }

    private startStream() {
        this.stopStream();
        const mode = (document.getElementById("live-source-mode") as HTMLSelectElement | null)?.value || "auto";

        if (mode === "auto" && this.wsUrl) {
            this.connectWebSocket(this.wsUrl);
            return;
        }

        this.tick();
        this.timer = window.setInterval(() => {
            if (!this.paused) this.tick();
        }, 2000);
    }

    private restartStream() {
        this.startStream();
    }

    private stopStream() {
        if (this.timer !== null) {
            clearInterval(this.timer);
            this.timer = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    private connectWebSocket(url: string) {
        try {
            this.setPill("connecting", "● กำลังเชื่อม WebSocket…");
            this.ws = new WebSocket(url);
            this.ws.onopen = () => this.setPill("live", "● LIVE · WebSocket Stream");
            this.ws.onmessage = (ev) => {
                if (this.paused) return;
                try {
                    const msg = JSON.parse(String(ev.data));
                    const devId = (document.getElementById("live-dev") as HTMLSelectElement | null)?.value;
                    if (msg.device_id && devId && msg.device_id !== devId) return;
                    this.pushSample({
                        ts: new Date().toTimeString().slice(0, 8),
                        temp: Number(msg.temp_c ?? msg.temp_c_mean ?? 31.5),
                        hum: Number(msg.humidity_pct ?? msg.humidity_pct_mean ?? 64),
                        spl: Number(msg.db_spl ?? msg.db_spl_mean ?? 62),
                        dba: Number(msg.dba ?? msg.dba_mean ?? 56),
                        lux: Number(msg.lux ?? msg.lux_mean ?? 4200),
                        lat: msg.lat ?? null,
                        lon: msg.lon ?? null,
                        cellId: msg.cell_id ?? null,
                    });
                } catch {
                    // ข้ามแพ็กเก็ตที่ไม่ใช่ JSON
                }
            };
            this.ws.onerror = () => {
                this.ws?.close();
                this.timer = window.setInterval(() => {
                    if (!this.paused) this.tick();
                }, 2000);
            };
        } catch {
            this.timer = window.setInterval(() => {
                if (!this.paused) this.tick();
            }, 2000);
        }
    }

    private async fetchLatestRow(devId: string): Promise<Record<string, unknown>> {
        if (this.apiBase) {
            const r = await fetch(`${this.apiBase}/latest?device=${encodeURIComponent(devId)}`);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            return (data.row as Record<string, unknown>) || {};
        }
        const res = await api<{ row: Record<string, unknown> }>("/latest", { device: devId });
        return res.row || {};
    }

    private async tick() {
        const devId = (document.getElementById("live-dev") as HTMLSelectElement | null)?.value || "IOTW-A001";
        const mode = (document.getElementById("live-source-mode") as HTMLSelectElement | null)?.value || "auto";

        if (mode === "demo") {
            this.pushSimulatedTick(devId);
            this.setPill("demo", "● DEMO · จำลองข้อมูลสด (1 Hz)");
            return;
        }

        try {
            const r = await this.fetchLatestRow(devId);
            const apiTs = String(r.minute_local || r.ts || "");

            if (apiTs && apiTs !== this.lastApiTs) {
                this.lastApiTs = apiTs;
                this.staleCount = 0;
                this.setPill("live", "● LIVE · เชื่อมต่อ AWS API (/latest)");
                this.pushSample({
                    ts: new Date().toTimeString().slice(0, 8),
                    temp: Number(r.temp_c_mean ?? r.temp_c ?? 31.8),
                    hum: Number(r.humidity_pct_mean ?? r.humidity_pct ?? 65),
                    spl: Number(r.db_spl_mean ?? r.db_spl ?? 63),
                    dba: Number(r.dba_mean ?? r.dba ?? 57),
                    lux: Number(r.lux_mean ?? r.lux ?? 3800),
                    lat: r.lat != null ? Number(r.lat) : null,
                    lon: r.lon != null ? Number(r.lon) : null,
                    cellId: r.cell_id ? String(r.cell_id) : null,
                });
            } else {
                this.staleCount++;
                if (this.staleCount >= 2) {
                    this.pushSimulatedTick(devId, r);
                    this.setPill("demo", "● LIVE DEMO · จำลองค่าสดต่อเนื่อง");
                }
            }
        } catch {
            this.pushSimulatedTick(devId);
            this.setPill("demo", "● LIVE DEMO · โหมดออฟไลน์");
        }
    }

    private pushSimulatedTick(devId: string, base?: Record<string, unknown>) {
        const prev = this.samples[this.samples.length - 1];
        const isSecondUnit = devId.toLowerCase().includes("park") || devId.endsWith("02");
        const baseTemp = prev ? prev.temp : Number(base?.temp_c_mean ?? (isSecondUnit ? 29.6 : 32.4));
        const baseHum = prev ? prev.hum : Number(base?.humidity_pct_mean ?? (isSecondUnit ? 71 : 61));
        const baseSpl = prev ? prev.spl : Number(base?.db_spl_mean ?? (isSecondUnit ? 54 : 67));
        const baseLux = prev ? prev.lux : Number(base?.lux_mean ?? (isSecondUnit ? 2400 : 8500));

        const jitter = (scale: number) => (Math.random() - 0.5) * scale;
        const temp = Math.max(24, Math.min(42, +(baseTemp + jitter(0.25)).toFixed(2)));
        const hum = Math.max(35, Math.min(95, +(baseHum + jitter(0.8)).toFixed(1)));
        const spl = Math.max(42, Math.min(92, +(baseSpl + jitter(3.2)).toFixed(1)));
        const dba = +(spl - 5.5 + jitter(1.0)).toFixed(1);
        const lux = Math.max(100, Math.round(baseLux + jitter(450)));

        this.pushSample({
            ts: new Date().toTimeString().slice(0, 8),
            temp,
            hum,
            spl,
            dba,
            lux,
            lat: base?.lat != null ? Number(base.lat) : 14.0725,
            lon: base?.lon != null ? Number(base.lon) : 100.6055,
            cellId: base?.cell_id ? String(base.cell_id) : "c0024_r0020",
        });
    }

    private pushSample(s: LiveSample) {
        this.samples.push(s);
        if (this.samples.length > MAX_POINTS) this.samples.shift();

        const tsEl = document.getElementById("live-last-ts");
        if (tsEl) tsEl.textContent = `อัปเดตล่าสุด: ${s.ts}`;

        const tempEl = document.getElementById("lv-temp");
        const tempBadge = document.getElementById("lv-temp-badge");
        if (tempEl) tempEl.textContent = `${s.temp.toFixed(2)} °C`;
        if (tempBadge) {
            if (s.temp >= 33) {
                tempBadge.innerHTML = `<span style="color:#dc2626;font-weight:700;">ร้อนอันตราย (เสี่ยงเพลียแดด)</span>`;
            } else if (s.temp >= 30) {
                tempBadge.innerHTML = `<span style="color:#d97706;font-weight:700;">เริ่มร้อนอบอ้าว (เฝ้าระวัง)</span>`;
            } else {
                tempBadge.innerHTML = `<span style="color:#16a34a;font-weight:700;">อากาศสบาย / ปกติ</span>`;
            }
        }

        const splEl = document.getElementById("lv-spl");
        const dbaEl = document.getElementById("lv-dba");
        if (splEl) splEl.textContent = `${s.spl.toFixed(1)} dB`;
        if (dbaEl) {
            const noiseState = s.spl >= 75 ? "ดังมาก (จราจรหนาแน่น)" : s.spl >= 65 ? "ปานกลาง" : "เงียบสงบ";
            dbaEl.textContent = `dBA: ${s.dba.toFixed(1)} · ${noiseState}`;
        }

        const humEl = document.getElementById("lv-hum");
        if (humEl) humEl.textContent = `${s.hum.toFixed(1)} %`;

        const luxEl = document.getElementById("lv-lux");
        const gpsEl = document.getElementById("lv-gps");
        if (luxEl) luxEl.textContent = `${s.lux.toLocaleString()} lux`;
        if (gpsEl) {
            const coord = s.lat != null && s.lon != null ? `${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}` : "สถานีคงที่";
            gpsEl.textContent = `พิกัด: ${coord} · บล็อก: ${s.cellId || "—"}`;
        }

        this.renderCharts();
    }

    private renderCharts() {
        const times = this.samples.map((x) => x.ts);
        const len = this.samples.length;

        const pointLabel = (color: string) => ({
            show: true,
            position: "top" as const,
            distance: 4,
            fontSize: 10,
            fontWeight: 600,
            color,
            formatter: (p: { dataIndex: number; value: unknown }) =>
                p.dataIndex === len - 1 || p.dataIndex % 4 === 0 ? String(p.value) : "",
        });

        const peakMarks = {
            symbol: "pin",
            symbolSize: 34,
            label: { fontSize: 10, color: "#fff" },
            data: [
                { type: "max" as const, name: "สูงสุด" },
                { type: "min" as const, name: "ต่ำสุด" },
            ],
        };

        // กราฟ 1: ระดับเสียง dB(SPL) & dBA
        this.soundChart?.setOption({
            animation: false,
            tooltip: { trigger: "axis" },
            legend: { top: 0, data: ["dB(SPL)", "dBA"] },
            grid: { left: 42, right: 18, top: 38, bottom: 26 },
            xAxis: { type: "category", data: times, boundaryGap: false },
            yAxis: {
                type: "value",
                name: "dB",
                scale: true,
                min: (v: { min: number }) => Math.floor(v.min - 2),
                max: (v: { max: number }) => Math.ceil(v.max + 3),
            },
            series: [
                {
                    name: "dB(SPL)",
                    type: "line",
                    smooth: false,
                    showSymbol: true,
                    symbol: "circle",
                    symbolSize: 6,
                    data: this.samples.map((x) => x.spl),
                    itemStyle: { color: "#4f46e5" },
                    lineStyle: { color: "#4f46e5", width: 2 },
                    areaStyle: { color: "rgba(79, 70, 229, 0.10)" },
                    label: pointLabel("#3730a3"),
                    markPoint: peakMarks,
                },
                {
                    name: "dBA",
                    type: "line",
                    smooth: false,
                    showSymbol: true,
                    symbol: "diamond",
                    symbolSize: 5,
                    data: this.samples.map((x) => x.dba),
                    itemStyle: { color: "#0ea5e9" },
                    lineStyle: { color: "#0ea5e9", width: 1.5, type: "dashed" },
                },
            ],
        });

        // กราฟ 2: อุณหภูมิอากาศ (°C) แยกเดี่ยว
        this.tempChart?.setOption({
            animation: false,
            tooltip: { trigger: "axis" },
            grid: { left: 46, right: 18, top: 38, bottom: 26 },
            xAxis: { type: "category", data: times, boundaryGap: false },
            yAxis: {
                type: "value",
                name: "°C",
                scale: true,
                min: (v: { min: number }) => +(v.min - 0.3).toFixed(1),
                max: (v: { max: number }) => +(v.max + 0.4).toFixed(1),
            },
            series: [
                {
                    name: "อุณหภูมิ (°C)",
                    type: "line",
                    smooth: false,
                    showSymbol: true,
                    symbol: "circle",
                    symbolSize: 6,
                    data: this.samples.map((x) => x.temp),
                    itemStyle: { color: "#ef4444" },
                    lineStyle: { color: "#ef4444", width: 2 },
                    areaStyle: { color: "rgba(239, 68, 68, 0.10)" },
                    label: pointLabel("#b91c1c"),
                    markPoint: peakMarks,
                },
            ],
        });

        // กราฟ 3: ความชื้นสัมพัทธ์ (%) แยกเดี่ยว
        this.humChart?.setOption({
            animation: false,
            tooltip: { trigger: "axis" },
            grid: { left: 44, right: 18, top: 38, bottom: 26 },
            xAxis: { type: "category", data: times, boundaryGap: false },
            yAxis: {
                type: "value",
                name: "%",
                scale: true,
                min: (v: { min: number }) => Math.max(0, Math.floor(v.min - 2)),
                max: (v: { max: number }) => Math.min(100, Math.ceil(v.max + 3)),
            },
            series: [
                {
                    name: "ความชื้น (%)",
                    type: "line",
                    smooth: false,
                    showSymbol: true,
                    symbol: "circle",
                    symbolSize: 6,
                    data: this.samples.map((x) => x.hum),
                    itemStyle: { color: "#0284c7" },
                    lineStyle: { color: "#0284c7", width: 2 },
                    areaStyle: { color: "rgba(2, 132, 199, 0.10)" },
                    label: pointLabel("#0369a1"),
                    markPoint: peakMarks,
                },
            ],
        });
    }
}

window.addEventListener("DOMContentLoaded", () => {
    new RealtimeIotController().init();
});