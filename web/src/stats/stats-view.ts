import * as echarts from "echarts";
import { api, errMsg } from "../api";
import { fmt } from "../utils/format";
import { byId, errHtml, esc } from "../utils/html";
import type {
  AnalyticsCorrelation,
  AnalyticsGroups,
  AnalyticsImportance,
  AnalyticsRegression,
  AnalyticsSpatialAutocorr,
  AnalyticsStationDiurnal,
  AnalyticsSummary,
  GridFeatureCollection,
} from "../types";

const LABEL: Record<string, string> = {
  lst_mean: "LST",
  ndvi_mean: "NDVI",
  ndbi_mean: "NDBI",
  builtup_pct: "builtup",
  air_temp_mean: "air_temp",
  db_spl_mean: "dB(SPL)",
};

export class StatsController {
  private charts: Record<string, echarts.ECharts> = {};
  private lastKey: string | null = null;
  private scGrid: GridFeatureCollection | null = null;

  constructor(
    private getPeriod: () => string | null,
    private getMatch: () => "all" | "window",
  ) {
    byId<HTMLSelectElement>("sc-x").onchange = () => this.drawScatter();
  }

  private chart(id: string) {
    return (this.charts[id] ||= echarts.init(byId(id)));
  }

  resize() {
    Object.values(this.charts).forEach((c) => c.resize());
  }

  invalidate() {
    this.lastKey = null;
  }

  async load(force = false) {
    const key = `${this.getPeriod()}|${this.getMatch()}`;
    if (!force && key === this.lastKey) return;
    this.lastKey = key;

    const q = { period: this.getPeriod(), match: this.getMatch() };
    const headlineEl = byId("headline");
    try {
      const [sum, cor, reg, imp, grp, sac, s9, grid] = await Promise.all([
        api<AnalyticsSummary>("/analytics/summary", q),
        api<AnalyticsCorrelation>("/analytics/correlation", q),
        api<AnalyticsRegression>("/analytics/regression", q),
        api<AnalyticsImportance>("/analytics/importance", q),
        api<AnalyticsGroups>("/analytics/groups", q),
        api<AnalyticsSpatialAutocorr>("/analytics/spatial_autocorr", q),
        api<AnalyticsStationDiurnal>("/analytics/station_diurnal", q),
        api<GridFeatureCollection>("/grid", { ...q, vars: "lst_mean,ndvi_mean,ndbi_mean,builtup_pct,air_temp_mean" }).catch(() => null),
      ]);

      const li = cor.variables.indexOf("lst_mean");
      let best: { v: string; r: number } | null = null;
      if (li >= 0) {
        for (let i = 0; i < cor.variables.length; i++) {
          const v = cor.variables[i];
          const rVal = cor.r[li]?.[i];
          if (i !== li && v !== "db_spl_mean" && rVal != null && !Number.isNaN(rVal)) {
            if (!best || Math.abs(rVal) > Math.abs(best.r)) best = { v, r: rVal };
          }
        }
      }
      headlineEl.textContent = best
        ? `${LABEL[best.v] || best.v} สัมพันธ์กับ LST สูงสุด (r = ${best.r.toFixed(2)}) · พื้นที่หนาแน่นร้อนกว่าพื้นที่เขียว ${grp.diff_dense_minus_green.toFixed(1)} °C`
        : `พื้นที่หนาแน่นร้อนกว่าพื้นที่เขียว ${grp.diff_dense_minus_green.toFixed(1)} °C`;

      const s3 = reg.models.find((m) => m.model === "s3");
      const s4 = reg.models.find((m) => m.model === "s4");
      const ndviR = cor.r[li]?.[cor.variables.indexOf("ndvi_mean")];
      const bldgR = cor.r[li]?.[cor.variables.indexOf("builtup_pct")];
      const airR = cor.r[li]?.[cor.variables.indexOf("air_temp_mean")];

      byId("research-takeaways").innerHTML = `
        <div class="tk-card green">
          <div class="tk-title">1. อิทธิพลของพื้นที่สีเขียว (NDVI)</div>
          <div class="tk-val">r = ${fmt(ndviR, 2)}</div>
          <p>พื้นที่ที่มีต้นไม้หนาแน่นช่วยลดอุณหภูมิพื้นผิวได้ชัดเจน โซนสีเขียวเย็นกว่าโซนอาคารหนาแน่นเฉลี่ย <b>${grp.diff_dense_minus_green.toFixed(2)} °C</b></p>
        </div>
        <div class="tk-card red">
          <div class="tk-title">2. อิทธิพลของอาคารหนาแน่น (building.shp)</div>
          <div class="tk-val">r = ${fmt(bldgR, 2)}</div>
          <p>บล็อกที่มีสัดส่วนอาคารและคอนกรีตสูงมีการสะสมความร้อนสูงกว่าและระบายความร้อนออกได้ช้ากว่าพื้นที่เปิดโล่ง</p>
        </div>
        <div class="tk-card blue">
          <div class="tk-title">3. ดาวเทียม (LST) vs อากาศจริง (IoT)</div>
          <div class="tk-val">r = ${fmt(airR, 2)} (R²=${s4?.r2 ?? "—"})</div>
          <p>อุณหภูมิพื้นผิวสูงกว่าอุณหภูมิอากาศระดับคนเดินเท้า แต่มีทิศทางสอดคล้องกัน โดยร่มเงาอาคารช่วยลดความร้อนระดับทางเดินได้บางส่วน</p>
        </div>
        <div class="tk-card amber">
          <div class="tk-title">4. กำลังการอธิบายของแบบจำลอง (S3)</div>
          <div class="tk-val">R² = ${s3?.r2 != null ? (s3.r2 * 100).toFixed(1) + "%" : "—"}</div>
          <p>ตัวแปรสัณฐานเมือง (NDVI, NDBI, ความหนาแน่นอาคาร) สามารถอธิบายความแปรปรวนของความร้อนในชุมชนได้ถึงระดับนี้</p>
        </div>
      `;

      const unit: Record<string, string> = { lst_mean: "°C", air_temp_mean: "°C", builtup_pct: "%", db_spl_mean: "dB" };
      byId("s1-cards").innerHTML = sum.rows
        .map(
          (r) =>
            `<div class="kpi"><div class="l">${LABEL[r.variable] || r.variable} เฉลี่ย (n=${r.n})</div><div class="v">${fmt(r.mean, 2)} ${unit[r.variable] || ""}</div><div class="l">SD ${fmt(r.sd, 2)} · ${fmt(r.min, 2)}–${fmt(r.max, 2)} · p90 ${fmt(r.p90, 2)}</div></div>`,
        )
        .join("");

      const hm: [number, number, number][] = [];
      cor.r.forEach((row, i) => row.forEach((v, j) => hm.push([j, i, v ?? 0])));
      this.chart("c-corr").setOption(
        {
          tooltip: {
            formatter: (p: { value: [number, number, number] }) =>
              `${LABEL[cor.variables[p.value[1]]]} × ${LABEL[cor.variables[p.value[0]]]}<br>r = ${p.value[2]}<br>p = ${cor.p_value[p.value[1]][p.value[0]]} · n = ${cor.n[p.value[1]][p.value[0]]}`,
          },
          grid: { left: 70, right: 70, top: 10, bottom: 50 },
          xAxis: { type: "category", data: cor.variables.map((v) => LABEL[v] || v), axisLabel: { interval: 0 } },
          yAxis: { type: "category", data: cor.variables.map((v) => LABEL[v] || v), axisLabel: { interval: 0 } },
          visualMap: {
            min: -1,
            max: 1,
            calculable: true,
            orient: "vertical",
            right: 0,
            top: "center",
            inRange: { color: ["#2166ac", "#f7f7f7", "#b2182b"] },
          },
          series: [
            {
              type: "heatmap",
              data: hm,
              label: { show: true, formatter: (p: { value: [number, number, number] }) => p.value[2].toFixed(2), fontSize: 10 },
            },
          ],
        },
        true,
      );

      this.scGrid = grid;
      this.drawScatter();

      if (s3) {
        this.chart("c-coef").setOption(
          {
            tooltip: { trigger: "axis" },
            legend: { top: 0 },
            grid: { left: 50, right: 10, top: 30, bottom: 30 },
            xAxis: { type: "category", data: s3.terms.map((t) => LABEL[t.term] || t.term) },
            yAxis: { type: "value" },
            series: [
              { name: "S3 |std coef|", type: "bar", data: s3.terms.map((t) => Math.abs(t.std_coef)), itemStyle: { color: "#0f766e" } },
              {
                name: "S5 RF importance",
                type: "bar",
                data: s3.terms.map((t) => imp.features.find((f) => f.feature === t.term)?.importance ?? 0),
                itemStyle: { color: "#f59e0b" },
              },
            ],
          },
          true,
        );
        const vifWarn = s3.terms.filter((t) => t.vif > 5);
        byId("s3-note").innerHTML =
          `S3: <code>${esc(s3.formula)}</code> R² = ${s3.r2 ?? "—"} (n=${s3.n}) · ` +
          s3.terms.map((t) => `${LABEL[t.term]} β*=${t.std_coef} VIF=${t.vif}`).join(", ") +
          (vifWarn.length ? ` <span class="warn">VIF > 5: ${vifWarn.map((t) => LABEL[t.term]).join(", ")} ซ้อนกัน</span>` : "") +
          (s4 ? `<br>S4: <code>${esc(s4.formula)}</code> R² = ${s4.r2 ?? "—"} (n=${s4.n}) ${esc(s4.note || "")}` : "");
      }

      this.chart("c-groups").setOption(
        {
          tooltip: {},
          grid: { left: 50, right: 10, top: 20, bottom: 30 },
          xAxis: { type: "category", data: grp.groups.map((g) => `${g.group} (NDVI ${g.ndvi_range.join("–")})`) },
          yAxis: { type: "value", scale: true, name: "LST °C" },
          series: [
            {
              type: "bar",
              data: grp.groups.map((g) => g.lst_mean),
              itemStyle: { color: (p: { dataIndex: number }) => ["#1a9641", "#fdae61", "#d7191c"][p.dataIndex] },
              label: { show: true, position: "top", formatter: (p: { value: number }) => p.value.toFixed(2) + " °C" },
            },
          ],
        },
        true,
      );

      byId("s7").innerHTML =
        sac.rows.map((r) => `<span>Moran's I (${esc(r.target)})</span><b>${r.morans_i} (p=${r.p_value})</b>`).join("") +
        `<span>R² ของ S3</span><b>${s3?.r2 ?? "—"}</b><span>R² ของ S4</span><b>${s4?.r2 ?? "—"}</b>`;

      this.chart("c-s9").setOption(
        {
          tooltip: { trigger: "axis" },
          legend: { top: 0 },
          grid: { left: 40, right: 10, top: 30, bottom: 30 },
          xAxis: { type: "category", data: [...Array(24).keys()].map((h) => h + ":00") },
          yAxis: { type: "value", scale: true, name: "°C" },
          series: s9.stations.map((s) => ({
            name: `${s.device_id} ${s.site_name || ""}`,
            type: "line",
            smooth: true,
            data: s.hours.map((h) => h.air_temp_mean),
          })),
        },
        true,
      );
    } catch (e: unknown) {
      headlineEl.innerHTML = errHtml(errMsg(e));
    }
  }

  private drawScatter() {
    const g = this.scGrid;
    if (!g) {
      this.chart("c-scatter").setOption({ title: { text: "/grid ไม่มีข้อมูล", left: "center", top: "middle" } }, true);
      return;
    }
    const sel = byId<HTMLSelectElement>("sc-x").value;
    const [xk, yk] = sel === "air" ? ["lst_mean", "air_temp_mean"] : [sel, "lst_mean"];
    const pts = g.features
      .map((f) => f.properties)
      .filter((p) => typeof p[xk] === "number" && typeof p[yk] === "number")
      .map((p) => [p[xk] as number, p[yk] as number]);

    const n = pts.length;
    const mx = n ? pts.reduce((s, p) => s + p[0], 0) / n : 0;
    const my = n ? pts.reduce((s, p) => s + p[1], 0) / n : 0;
    const denom = pts.reduce((s, p) => s + (p[0] - mx) ** 2, 0);

    if (n < 3 || denom === 0) {
      this.chart("c-scatter").setOption(
        {
          title: {
            text: `ข้อมูลยังไม่พอคำนวณแนวโน้ม ${LABEL[xk]} vs ${LABEL[yk]} (พบ ${n} บล็อก, ต้องการ ≥ 3 บล็อก)`,
            left: "center",
            top: "middle",
            textStyle: { fontSize: 12, color: "#6b7785" },
          },
          xAxis: { show: false },
          yAxis: { show: false },
          series: [],
        },
        true,
      );
      return;
    }

    const b = pts.reduce((s, p) => s + (p[0] - mx) * (p[1] - my), 0) / denom;
    const a = my - b * mx;
    const xs = pts.map((p) => p[0]);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);

    this.chart("c-scatter").setOption(
      {
        tooltip: {},
        grid: { left: 50, right: 10, top: 20, bottom: 40 },
        xAxis: { type: "value", scale: true, name: LABEL[xk], nameLocation: "middle", nameGap: 25 },
        yAxis: { type: "value", scale: true, name: LABEL[yk] },
        series: [
          { type: "scatter", data: pts, symbolSize: 4, itemStyle: { color: "#0f766e", opacity: 0.5 } },
          {
            type: "line",
            data: [
              [x0, a + b * x0],
              [x1, a + b * x1],
            ],
            showSymbol: false,
            lineStyle: { color: "#b91c1c", width: 2 },
          },
        ],
        title: { text: `n = ${n} เซลล์ · slope = ${b.toFixed(2)}`, right: 10, top: 0, textStyle: { fontSize: 11, color: "#6b7785" } },
      },
      true,
    );
  }
}
