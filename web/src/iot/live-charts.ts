import type * as echarts from "echarts";

/** ค่าสด 1 จุด (จาก WebSocket hub หรือ /latest) */
export interface LiveSample {
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

export interface LiveCharts {
  sound: echarts.ECharts | null;
  temp: echarts.ECharts | null;
  hum: echarts.ECharts | null;
}

/** วาดกราฟสด 3 กราฟ (เสียง, อุณหภูมิ, ความชื้น) จากจุดล่าสุดทั้งหมด */
export function renderLiveCharts(charts: LiveCharts, samples: LiveSample[]) {
  const times = samples.map((x) => x.ts);
  const len = samples.length;

  const pointLabel = (color: string) => ({
    show: true,
    position: "top" as const,
    distance: 4,
    fontSize: 10,
    fontWeight: 600,
    color,
    formatter: (p: { dataIndex: number; value: unknown }) => (p.dataIndex === len - 1 || p.dataIndex % 4 === 0 ? String(p.value) : ""),
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
  charts.sound?.setOption({
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
        data: samples.map((x) => x.spl),
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
        data: samples.map((x) => x.dba),
        itemStyle: { color: "#0ea5e9" },
        lineStyle: { color: "#0ea5e9", width: 1.5, type: "dashed" },
      },
    ],
  });

  // กราฟ 2: อุณหภูมิอากาศ (°C) แยกเดี่ยว
  charts.temp?.setOption({
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
        data: samples.map((x) => x.temp),
        itemStyle: { color: "#ef4444" },
        lineStyle: { color: "#ef4444", width: 2 },
        areaStyle: { color: "rgba(239, 68, 68, 0.10)" },
        label: pointLabel("#b91c1c"),
        markPoint: peakMarks,
      },
    ],
  });

  // กราฟ 3: ความชื้นสัมพัทธ์ (%) แยกเดี่ยว
  charts.hum?.setOption({
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
        data: samples.map((x) => x.hum),
        itemStyle: { color: "#0284c7" },
        lineStyle: { color: "#0284c7", width: 2 },
        areaStyle: { color: "rgba(2, 132, 199, 0.10)" },
        label: pointLabel("#0369a1"),
        markPoint: peakMarks,
      },
    ],
  });
}
