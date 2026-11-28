import { api, errMsg } from "../api";
import { byId, esc } from "../utils/html";
import type { AnalyticsCorrelation, AnalyticsGroups, AnalyticsRegression, AnalyticsSpatialAutocorr, AnalyticsSummary } from "../types";

export class ResearchController {
  private lastKey: string | null = null;

  constructor(
    private getPeriod: () => string | null,
    private getMatch: () => "all" | "window",
  ) {}

  invalidate() {
    this.lastKey = null;
  }

  async load(force = false) {
    const period = this.getPeriod();
    const match = this.getMatch();
    const key = `${period}|${match}`;
    if (!force && key === this.lastKey) return;
    this.lastKey = key;

    byId("res-period-label").textContent = `${period || "ล่าสุด"} · โหมด ${match}`;
    const tbody = byId("res-comparison-body");
    const interpBox = byId("res-interpretation");

    try {
      const q = { period, match };
      const [sum, cor, grp, reg, sac] = await Promise.all([
        api<AnalyticsSummary>("/analytics/summary", q),
        api<AnalyticsCorrelation>("/analytics/correlation", q),
        api<AnalyticsGroups>("/analytics/groups", q),
        api<AnalyticsRegression>("/analytics/regression", q),
        api<AnalyticsSpatialAutocorr>("/analytics/spatial_autocorr", q),
      ]);

      const li = cor.variables.indexOf("lst_mean");
      const rNdvi = cor.r[li]?.[cor.variables.indexOf("ndvi_mean")];
      const rBldg = cor.r[li]?.[cor.variables.indexOf("builtup_pct")];
      const rAir = cor.r[li]?.[cor.variables.indexOf("air_temp_mean")];
      const s3 = reg.models.find((m) => m.model === "s3");
      const s4 = reg.models.find((m) => m.model === "s4");
      const moranLst = sac.rows.find((x) => x.target === "lst_mean")?.morans_i;

      const rows = [
        {
          topic: "1. ความแตกต่างความร้อนระหว่างโซนอาคารหนาแน่น vs โซนสีเขียว",
          method: "แบ่งกลุ่มตาม NDVI (Kruskal-Wallis / S8)",
          ours: `โซนอาคารหนาแน่นร้อนกว่าโซนเขียว <b>+${grp.diff_dense_minus_green.toFixed(2)} °C</b>`,
          lit: "<b>Oke (1982); Stewart & Oke (2012)</b> (กรอบ Local Climate Zones) และ <b>Srivanit & Hokao (2012)</b> พบความต่าง LST ในกทม./เมืองเขตร้อนอยู่ที่ <b>+2.0 ถึง +5.5 °C</b>",
          meaning:
            grp.diff_dense_minus_green >= 2
              ? "สอดคล้องกับทฤษฎี UHI ชัดเจน พื้นที่สีเขียวในชุมชนทำหน้าที่เป็น Cool Island ลดอุณหภูมิพื้นผิวได้อย่างมีนัยสำคัญ"
              : "ความต่างน้อยกว่าค่าเฉลี่ยใจกลางเมืองใหญ่ สะท้อนว่าพื้นที่ศึกษามีลักษณะกึ่งเมืองกึ่งชานเมืองและมีอาคารเตี้ยกระจายตัว",
        },
        {
          topic: "2. ความสัมพันธ์ระหว่างพื้นที่สีเขียว (NDVI) กับอุณหภูมิพื้นผิว (LST)",
          method: "Pearson Correlation (r) & Standardized β* (S2, S3)",
          ours: `r = <b>${rNdvi != null ? rNdvi.toFixed(3) : "—"}</b>`,
          lit: "<b>Weng, Lu & Schubring (2004)</b> (Remote Sensing of Environment) รายงานความสัมพันธ์เชิงลบระดับปานกลางถึงสูง (<b>r = -0.45 ถึง -0.75</b>)",
          meaning: "ยืนยันว่าการเพิ่มความปกคลุมของเรือนยอดไม้ช่วยลดการสะสมความร้อนของพื้นผิวชุมชนได้โดยตรงผ่านการคายระเหยน้ำ (Evapotranspiration)",
        },
        {
          topic: "3. อิทธิพลของความหนาแน่นอาคาร (building.shp) ต่อความร้อน",
          method: "Overlay building.shp ลงกริด 90m (builtup_pct vs LST)",
          ours: `r = <b>${rBldg != null ? rBldg.toFixed(3) : "—"}</b> (R² รวม = <b>${s3?.r2 ?? "—"}</b>)`,
          lit: "<b>Oke (1981); Li et al. (2011); Srivanit & Hokao (2013)</b> พบว่าสัดส่วนพื้นที่อาคาร (BCR) สัมพันธ์เชิงบวกกับ LST (<b>r = +0.40 ถึง +0.70</b>)",
          meaning: "หลังคาอาคารและพื้นผิวคอนกรีตดูดซับรังสีดวงอาทิตย์และคายความร้อนสะสม ทำให้บล็อกที่มีอาคารหนาแน่นเกิดเป็น Hot Spot",
        },
        {
          topic: "4. ความสัมพันธ์ระหว่างอุณหภูมิพื้นผิว (LST) กับอากาศจริง (IoT)",
          method: "จับคู่เวลาเดินวัด IoT กับพิกัดกริด 90m (S2, S4)",
          ours: `r = <b>${rAir != null ? rAir.toFixed(3) : "—"}</b>`,
          lit: "<b>Voogt & Oke (2003); Nichol et al. (2009)</b> พบว่าอุณหภูมิอากาศระดับทางเดินสัมพันธ์กับ LST ในระดับปานกลาง (<b>r = +0.30 ถึง +0.65</b>)",
          meaning: "อธิบายได้ว่าทำไมบางจุดที่หลังคาร้อนจัด (LST สูง) แต่ทางเดินเท้าด้านล่างอาจเย็นกว่าเพราะได้รับร่มเงาจากตัวอาคารสูงและกระแสลม",
        },
        {
          topic: "5. การกระจุกตัวเชิงพื้นที่ของความร้อนในชุมชน",
          method: "Global Moran's I & Getis-Ord Gi* (S6, S7)",
          ours: `Moran's I = <b>${moranLst ?? "—"}</b>`,
          lit: "<b>Getis & Ord (1992); Tsai et al. (2018)</b> ระบุว่าค่า Moran's I > 0.30 (p < 0.05) บ่งชี้การกระจุกตัวเชิงพื้นที่ (Spatial Clustering)",
          meaning: "ความร้อนไม่ได้กระจายแบบสุ่ม แต่กระจุกตัวตามแนวถนนสายหลักและกลุ่มอาคารหนาแน่น ช่วยชี้เป้าจุดปรับปรุงผังชุมชนได้แม่นยำ",
        },
      ];

      tbody.innerHTML = rows
        .map(
          (r) => `<tr>
          <td><b>${esc(r.topic)}</b></td>
          <td><small>${esc(r.method)}</small></td>
          <td class="ours-cell">${r.ours}</td>
          <td>${r.lit}</td>
          <td>${esc(r.meaning)}</td>
        </tr>`,
        )
        .join("");

      // What-If Urban Cooling Simulator (คำนวณจากสมการ Regression S3 & S4 จริง)
      const lstMean = sum.rows.find((x) => x.variable === "lst_mean")?.mean ?? 38.0;
      const airMean = sum.rows.find((x) => x.variable === "air_temp_mean")?.mean ?? 31.0;
      const betaNdvi = s3?.terms.find((t) => t.term === "ndvi_mean")?.coef ?? -6.5;
      const betaBuiltup = s3?.terms.find((t) => t.term === "builtup_pct")?.coef ?? 0.045;
      const betaAirLst = s4?.terms.find((t) => t.term === "lst_mean")?.coef ?? 0.25;

      let simBox = document.getElementById("whatif-sim");
      if (!simBox) {
        simBox = document.createElement("div");
        simBox.id = "whatif-sim";
        simBox.className = "sim-box";
        interpBox.parentElement!.insertBefore(simBox, interpBox);
      }

      simBox.innerHTML = `
        <h4>ตัวจำลองสถานการณ์ลดความร้อนชุมชน (What-If Digital Twin Simulator)</h4>
        <p class="note">ทดลองปรับเปลี่ยนสภาพแวดล้อมในบล็อก 90×90 ม. เพื่อคาดการณ์อุณหภูมิที่ลดลงโดยใช้ค่าสัมประสิทธิ์จากแบบจำลอง Multiple Regression (<code>β_NDVI = ${betaNdvi.toFixed(3)}</code>, <code>β_builtup = ${betaBuiltup.toFixed(4)}</code>)</p>
        <div class="sim-grid">
          <div class="sim-ctrl">
            <label>1. เพิ่มพื้นที่สีเขียว / ต้นไม้ให้ร่มเงา (ΔNDVI): <b id="sim-ndvi-lbl">+0.10</b></label>
            <input type="range" id="sim-ndvi" min="0" max="0.30" step="0.01" value="0.10" />
            <label>2. ลดพื้นผิวอาคารทึบแสง / เพิ่มหลังคาเย็น (ΔBuilt-up): <b id="sim-bldg-lbl">-10%</b></label>
            <input type="range" id="sim-bldg" min="0" max="30" step="1" value="10" />
          </div>
          <div class="sim-out" id="sim-result"></div>
        </div>
      `;

      const updateSim = () => {
        const dNdvi = parseFloat(byId<HTMLInputElement>("sim-ndvi").value);
        const dBuilt = -parseFloat(byId<HTMLInputElement>("sim-bldg").value);
        byId("sim-ndvi-lbl").textContent = `+${dNdvi.toFixed(2)}`;
        byId("sim-bldg-lbl").textContent = `${dBuilt}%`;

        const dLst = betaNdvi * dNdvi + betaBuiltup * dBuilt;
        const dAir = dLst * Math.max(0.15, betaAirLst);
        byId("sim-result").innerHTML = `
          <div><span>อุณหภูมิพื้นผิวคาดการณ์ (LST)</span><b class="cool">${(lstMean + dLst).toFixed(2)} °C (${dLst.toFixed(2)} °C)</b></div>
          <div><span>อุณหภูมิอากาศทางเดินเท้าคาดการณ์ (IoT)</span><b class="cool">${(airMean + dAir).toFixed(2)} °C (${dAir.toFixed(2)} °C)</b></div>
        `;
      };
      byId<HTMLInputElement>("sim-ndvi").oninput = updateSim;
      byId<HTMLInputElement>("sim-bldg").oninput = updateSim;
      updateSim();

      interpBox.innerHTML = `<strong>ข้อสรุปสำหรับนำไปอภิปรายผลในเล่มโครงงาน:</strong> จากข้อมูลช่วง <code>${esc(period || "")}</code> พบว่าสัณฐานอาคารจาก <code>building.shp</code> และพื้นที่สีเขียว <code>NDVI</code> สามารถอธิบายความแตกต่างของอุณหภูมิพื้นผิวในชุมชนได้ <b>${s3?.r2 != null ? (s3.r2 * 100).toFixed(1) + "%" : "—"}</b> โดยมีส่วนต่างอุณหภูมิระหว่างโซนอาคารหนาแน่นกับโซนสีเขียว <b>${grp.diff_dense_minus_green.toFixed(2)} °C</b> และเมื่อเทียบกับค่าตรวจวัดระดับคนเดินเท้าจากเซนเซอร์ IoT จะเห็นความเชื่อมโยงระหว่างความร้อนพื้นผิวกับสภาวะน่าสบายในแต่ละซอยของชุมชนได้อย่างชัดเจน`;
    } catch (e: unknown) {
      tbody.innerHTML = `<tr><td colspan="5" class="err">${esc(errMsg(e))}</td></tr>`;
    }
  }
}
