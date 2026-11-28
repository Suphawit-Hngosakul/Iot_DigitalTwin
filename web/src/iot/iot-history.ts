import * as echarts from "echarts";
import { api, errMsg, toMinuteObjects } from "../api";
import { dayOf, fmtNum as fmt, nextDay, statsOf } from "../utils/format";
import { byId, esc } from "../utils/html";
import type { DeviceInfo, QualityResponse, ReadingMinuteRow, ReadingsResponse } from "../types";

/** แท็บ IoT › ข้อมูลย้อนหลัง: กราฟเวลาจาก /readings, ความสมบูรณ์ข้อมูลจาก /quality และดาวน์โหลด CSV */
export class IotHistoryController {
  private charts: Record<string, echarts.ECharts> = {};
  private devices: DeviceInfo[] | null = null;
  private lastKey: string | null = null;
  private lastRows: Record<string, unknown>[] = [];

  constructor() {
    this.bindUi();
  }

  private chart(id: string) {
    return (this.charts[id] ||= echarts.init(byId(id)));
  }

  resize() {
    Object.values(this.charts).forEach((c) => c.resize());
  }

  private bindUi() {
    const devSel = byId<HTMLSelectElement>("iot-dev");
    const dateIn = byId<HTMLInputElement>("iot-date");
    const winSel = byId<HTMLSelectElement>("iot-window");
    const intSel = byId<HTMLSelectElement>("iot-interval");
    const dbaIn = byId<HTMLInputElement>("iot-dba");
    const exportBtn = byId<HTMLButtonElement>("iot-export");

    devSel.onchange = () => {
      const d = this.devices?.find((x) => x.device_id === devSel.value);
      if (d?.latest_hour_local) dateIn.value = dayOf(d.latest_hour_local);
      this.load();
    };
    dateIn.onchange = () => this.load();
    winSel.onchange = () => this.load();
    intSel.onchange = () => {
      // โหมด 1s รองรับสูงสุด 6 ชั่วโมง หากเลือกทั้งวันอยู่ให้สลับเป็นช่วงเช้าให้อัตโนมัติ
      if (intSel.value === "1s" && winSel.value === "all") {
        winSel.value = "morning";
      }
      this.load();
    };
    dbaIn.onchange = () => this.load();

    exportBtn.onclick = () => {
      if (!this.lastRows.length) return;
      const cols = Object.keys(this.lastRows[0]);
      const csv = [cols.join(","), ...this.lastRows.map((r) => cols.map((c) => JSON.stringify(r[c] ?? "")).join(","))].join("\n");
      const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${devSel.value}_${dateIn.value}_${intSel.value}.csv`;
      a.click();
    };
  }

  private getRange(day: string, win: string) {
    if (win === "morning") return { from: `${day}T06:00:00`, to: `${day}T12:00:00` };
    if (win === "afternoon") return { from: `${day}T12:00:00`, to: `${day}T18:00:00` };
    if (win === "evening") return { from: `${day}T18:00:00`, to: `${nextDay(day)}T00:00:00` };
    return { from: `${day}T00:00:00`, to: `${nextDay(day)}T00:00:00` };
  }

  async load(force = false) {
    if (!this.devices) {
      try {
        this.devices = (await api<{ devices: DeviceInfo[] }>("/devices")).devices;
      } catch (e: unknown) {
        byId("c-spl").innerHTML = `<p class="err">${esc(errMsg(e))}</p>`;
        return;
      }
    }

    const sel = byId<HTMLSelectElement>("iot-dev");
    const dateIn = byId<HTMLInputElement>("iot-date");
    const winSel = byId<HTMLSelectElement>("iot-window");
    const intSel = byId<HTMLSelectElement>("iot-interval");

    if (!sel.options.length) {
      sel.innerHTML = this.devices
        .map(
          (d) =>
            `<option value="${esc(d.device_id)}">${esc(d.device_id)} (${esc(d.device_type)}${d.site_name ? " · " + esc(d.site_name) : ""})</option>`,
        )
        .join("");
      const first = this.devices.find((d) => d.latest_hour_local) || this.devices[0];
      if (first) {
        sel.value = first.device_id;
        if (first.latest_hour_local) dateIn.value = dayOf(first.latest_hour_local);
      }
    }

    const dev = sel.value;
    const day = dateIn.value;
    const win = winSel.value;
    const interval = intSel.value as "1min" | "1s";
    const showDba = byId<HTMLInputElement>("iot-dba").checked;
    const key = `${dev}|${day}|${win}|${interval}|${showDba}`;
    if (!force && key === this.lastKey) return;
    this.lastKey = key;

    const range = this.getRange(day, win);
    const [rd, ql] = await Promise.all([
      api<ReadingsResponse>("/readings", { interval, device: dev, ...range }).catch((e) => ({
        device: dev,
        interval,
        from: "",
        to: "",
        columns: [],
        rows: [],
        _err: e,
      })),
      api<QualityResponse>("/quality", { device: dev, ...range }).catch(() => ({
        device: dev,
        from: "",
        to: "",
        hours: [],
      })),
    ]);

    const rows = toMinuteObjects(rd) as unknown as Record<string, unknown>[];
    this.lastRows = rows;

    const is1s = interval === "1s";
    const x = rows.map((r) => String(r[is1s ? "ts_local" : "minute_local"] || "").slice(11, is1s ? 19 : 16));
    const temps = rows.map((r) => (r[is1s ? "temp_c" : "temp_c_mean"] as number) ?? null);
    const hums = rows.map((r) => (r[is1s ? "humidity_pct" : "humidity_pct_mean"] as number) ?? null);
    const luxs = rows.map((r) => (r[is1s ? "lux" : "lux_mean"] as number) ?? null);
    const splMean = rows.map((r) => (r[is1s ? "db_spl" : "db_spl_mean"] as number) ?? null);
    const dbaMean = rows.map((r) => (r[is1s ? "dba" : "dba_mean"] as number) ?? null);
    const splMin = rows.map((r) => (is1s ? null : ((r as unknown as ReadingMinuteRow).db_spl_min ?? null)));
    const splMax = rows.map((r) => (is1s ? null : ((r as unknown as ReadingMinuteRow).db_spl_max ?? null)));

    const tStat = statsOf(temps);
    const hStat = statsOf(hums);
    const sStat = statsOf(splMean);
    const lStat = statsOf(luxs);

    byId("iot-kpis").innerHTML = `
      <div class="kpi"><div class="l">อุณหภูมิอากาศเฉลี่ย</div><div class="v">${fmt(tStat.avg, 1)} °C</div><div class="l">ต่ำสุด ${fmt(tStat.min, 1)} · สูงสุด ${fmt(tStat.max, 1)} °C</div></div>
      <div class="kpi"><div class="l">ความชื้นสัมพัทธ์เฉลี่ย</div><div class="v">${fmt(hStat.avg, 1)} %</div><div class="l">ต่ำสุด ${fmt(hStat.min, 1)} · สูงสุด ${fmt(hStat.max, 1)} %</div></div>
      <div class="kpi"><div class="l">ระดับเสียงเฉลี่ย</div><div class="v">${fmt(sStat.avg, 1)} dB</div><div class="l">ต่ำสุด ${fmt(sStat.min, 1)} · สูงสุด ${fmt(sStat.max, 1)} dB</div></div>
      <div class="kpi"><div class="l">ความเข้มแสงเฉลี่ย</div><div class="v">${fmt(lStat.avg, 0)} lux</div><div class="l">สูงสุด ${fmt(lStat.max, 0)} lux</div></div>
      <div class="kpi"><div class="l">จำนวนข้อมูลที่บันทึกได้</div><div class="v">${rows.length.toLocaleString()}</div><div class="l">${is1s ? "วินาที" : "นาที"} · วันที่ ${day}</div></div>
    `;

    const errObj = (rd as { _err?: Error })._err;
    const empty = errObj ? "เกิดข้อผิดพลาด: " + errObj.message : rows.length ? "" : `ไม่พบข้อมูลย้อนหลังของ ${dev} ในวันที่ ${day}`;

    const base = (name: string) => ({
      title: { text: empty, left: "center", top: "middle", textStyle: { fontSize: 13, color: "#94a3b8" } },
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 14, top: 28, bottom: 26 },
      xAxis: { type: "category", data: x },
      yAxis: { type: "value", scale: true, name },
      dataZoom: [{ type: "inside" }],
    });

    const splSeries: unknown[] = [];
    if (!is1s) {
      splSeries.push(
        { name: "min", type: "line", data: splMin, stack: "band", lineStyle: { opacity: 0 }, showSymbol: false, tooltip: { show: false } },
        {
          name: "range",
          type: "line",
          data: splMax.map((mx, i) => (mx != null && splMin[i] != null ? +(mx - splMin[i]!).toFixed(2) : null)),
          stack: "band",
          lineStyle: { opacity: 0 },
          areaStyle: { color: "rgba(15,118,110,.15)" },
          showSymbol: false,
          tooltip: { show: false },
        },
      );
    }
    splSeries.push({ name: "เสียงเฉลี่ย dB(SPL)", type: "line", data: splMean, showSymbol: false, lineStyle: { color: "#0f766e", width: 2 } });
    if (showDba) {
      splSeries.push({ name: "เสียง dBA", type: "line", data: dbaMean, showSymbol: false, lineStyle: { color: "#f59e0b", width: 1.8 } });
    }

    this.chart("c-spl").setOption({ ...base("dB"), legend: { top: 0, data: ["เสียงเฉลี่ย dB(SPL)", "เสียง dBA"] }, series: splSeries }, true);
    this.chart("c-temp").setOption(
      {
        ...base("°C"),
        series: [{ name: "อุณหภูมิ", type: "line", data: temps, showSymbol: false, lineStyle: { color: "#dc2626", width: 2 } }],
      },
      true,
    );
    this.chart("c-hum").setOption(
      {
        ...base("%"),
        series: [{ name: "ความชื้น", type: "line", data: hums, showSymbol: false, lineStyle: { color: "#2563eb", width: 2 } }],
      },
      true,
    );
    this.chart("c-lux").setOption(
      { ...base("lux"), series: [{ name: "แสง", type: "line", data: luxs, showSymbol: false, lineStyle: { color: "#eab308", width: 2 } }] },
      true,
    );

    this.chart("c-quality").setOption(
      {
        tooltip: {
          trigger: "axis",
          formatter: (ps: { dataIndex: number }[]) => {
            const h = ql.hours[ps[0].dataIndex];
            const okPct = Math.max(0, 100 - h.loss_rate * 100).toFixed(1);
            return `เวลา ${h.hour_local.slice(11, 13)}:00 น.<br>ความสมบูรณ์ข้อมูล: <b>${okPct}%</b><br>ได้รับ ${h.received_rows} / ${h.expected_rows} แถว`;
          },
        },
        grid: { left: 48, right: 14, top: 24, bottom: 26 },
        xAxis: { type: "category", data: ql.hours.map((h) => h.hour_local.slice(11, 13) + ":00") },
        yAxis: { type: "value", min: 0, max: 100, name: "%" },
        series: [
          {
            type: "bar",
            data: ql.hours.map((h) => +Math.max(0, 100 - h.loss_rate * 100).toFixed(1)),
            itemStyle: { color: (p: { value: number }) => (p.value >= 95 ? "#16a34a" : "#f59e0b"), borderRadius: [4, 4, 0, 0] },
          },
        ],
      },
      true,
    );
  }
}
