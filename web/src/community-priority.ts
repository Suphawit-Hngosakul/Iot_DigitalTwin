import * as echarts from "echarts";
import { api } from "./api";

export interface CommunityPriorityResult {
    id: string;
    name: string;
    district: string;
    riskLevel: string;
    lst300: number | null;
    lstP90_300: number | null;
    lst500: number | null;
    builtup300: number | null;
    builtup500: number | null;
    bldgCount300: number | null;
    green300: number | null; // 0..1
    green500: number | null; // 0..1
    ndvi300: number | null;
    ndvi500: number | null;
    score: number; // 0 - 100
    rank: number;
    total: number;
    tier: "critical" | "high" | "moderate" | "low";
    tierLabel: string;
    theme: "red" | "amber" | "emerald";
    headlineReason: string;
    actions: { step: number; title: string; detail: string; tag: string }[];
}

export const COMMUNITY_GLOSSARY: { code: string; name: string; desc: string }[] = [
    {
        code: "vulnerability_score",
        name: "ดัชนีความเปราะบางความร้อนชุมชน (0–100 คะแนน)",
        desc: "คะแนนรวมถ่วงน้ำหนักจาก 3 ปัจจัยหลัก: ความร้อนพื้นผิว (45%) + ความหนาแน่นอาคาร (30%) + การขาดแคลนพื้นที่สีเขียว (25%) ยิ่งสูงยิ่งต้องเข้าช่วยเหลือเร่งด่วน",
    },
    {
        code: "lst_mean_300 / 500",
        name: "อุณหภูมิพื้นผิวเฉลี่ยรัศมี 300 ม. และ 500 ม. (°C)",
        desc: "อุณหภูมิพื้นผิวหลังคาและถนนรอบศูนย์กลางชุมชน รัศมี 300 ม. สะท้อนสภาพแวดล้อมระดับเดินเท้าในชุมชน ส่วน 500 ม. สะท้อนอิทธิพลความร้อนจากย่านข้างเคียง",
    },
    {
        code: "lst_p90_300",
        name: "อุณหภูมิพื้นผิวสูงสุดช่วงพีค (90th Percentile)",
        desc: "ค่าความร้อนจัดในจุดที่ร้อนที่สุด 10% แรกของชุมชน ใช้ระบุจุดสะสมความร้อนวิกฤต (Hotspot) ที่เสี่ยงต่อสุขภาพผู้อยู่อาศัย",
    },
    {
        code: "builtup_pct_300",
        name: "ความหนาแน่นพื้นที่อาคาร (Building Coverage %)",
        desc: "ร้อยละของพื้นที่ฐานอาคารต่อพื้นที่ทั้งหมด ยิ่งสูงแปลว่าอาคารปลูกสร้างชิดกันมาก ช่องว่างรับลมแคบ และสะสมความร้อนในเวลากลางวันสูง",
    },
    {
        code: "green_ratio_300",
        name: "สัดส่วนพื้นที่สีเขียวรอบชุมชน (%)",
        desc: "ร้อยละของพื้นที่ปกคลุมด้วยเรือนยอดไม้และพืชพรรณ ยิ่งต่ำแปลว่าชุมชนขาดร่มเงาธรรมชาติและการคายระเหยน้ำช่วยลดอุณหภูมิ",
    },
    {
        code: "ndvi_mean_300",
        name: "ดัชนีพืชพรรณ (NDVI: -1 ถึง +1)",
        desc: "ค่าความสมบูรณ์ของต้นไม้และพื้นที่สีเขียว ค่าต่ำกว่า 0.15 แสดงถึงสภาพแวดล้อมที่เป็นคอนกรีตและหลังคาหนาแน่น",
    },
];

const num = (v: unknown): number | null => {
    if (v == null || v === "" || Number.isNaN(Number(v))) return null;
    return Number(v);
};

const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

function analyzeSingleCommunity(
    props: Record<string, unknown>,
    allPropsList: Record<string, unknown>[]
): CommunityPriorityResult {
    const id = String(props.community_id || "CS341");
    const name = String(props.name || id);
    const district = String(props.district || "—");
    const riskLevel = String(props.risk_level || "");

    const lst300 = num(props.lst_mean_300);
    const lstP90_300 = num(props.lst_p90_300);
    const lst500 = num(props.lst_mean_500);
    const builtup300 = num(props.builtup_pct_300);
    const builtup500 = num(props.builtup_pct_500);
    const bldgCount300 = num(props.building_count_300);
    const green300 = num(props.green_ratio_300);
    const green500 = num(props.green_ratio_500);
    const ndvi300 = num(props.ndvi_mean_300);
    const ndvi500 = num(props.ndvi_mean_500);

    // คำนวณคะแนนความเสี่ยงย่อย (0..1) เทียบกับเกณฑ์วิกฤตเมืองและค่าในกลุ่มชุมชน
    const heatNorm = lst300 != null ? clamp01((lst300 - 34) / 11) : 0.5; // 34°C -> 0, 45°C -> 1
    const densityNorm = builtup300 != null ? clamp01(builtup300 / 65) : 0.5; // 0% -> 0, 65% -> 1
    const greenDeficitNorm =
        green300 != null
            ? clamp01(1 - green300 / 0.35) // เขียว >= 35% -> เสี่ยง 0, เขียว 0% -> เสี่ยง 1
            : ndvi300 != null
                ? clamp01((0.35 - ndvi300) / 0.35)
                : 0.5;

    const rawScore = (heatNorm * 0.45 + densityNorm * 0.30 + greenDeficitNorm * 0.25) * 100;
    const score = Math.round(rawScore * 10) / 10;

    let tier: CommunityPriorityResult["tier"] = "moderate";
    let tierLabel = "ปานกลาง · เฝ้าระวัง";
    let theme: CommunityPriorityResult["theme"] = "amber";

    if (score >= 72 || riskLevel === "high") {
        if (score >= 80) {
            tier = "critical";
            tierLabel = "วิกฤตเร่งด่วนสูงสุด (Priority 1)";
            theme = "red";
        } else {
            tier = "high";
            tierLabel = "เปราะบางสูง · ควรช่วยเหลือก่อน";
            theme = "red";
        }
    } else if (score < 45 && riskLevel !== "medium") {
        tier = "low";
        tierLabel = "ความเสี่ยงต่ำ · ต้นแบบพื้นที่สีเขียว";
        theme = "emerald";
    }

    // วิเคราะห์สาเหตุหลักเชิงกายภาพของชุมชน
    const reasons: string[] = [];
    if (lst300 != null && lst300 >= 40) reasons.push(`อุณหภูมิพื้นผิวสูงจัด (${lst300.toFixed(1)} °C)`);
    if (builtup300 != null && builtup300 >= 40) reasons.push(`อาคารหนาแน่นมาก (${builtup300.toFixed(1)}%)`);
    if (green300 != null && green300 < 0.12) reasons.push(`พื้นที่สีเขียวน้อยมาก (${(green300 * 100).toFixed(1)}%)`);
    const headlineReason =
        reasons.length > 0
            ? reasons.join(" + ")
            : "สภาพแวดล้อมความร้อนและสิ่งปลูกสร้างอยู่ในระดับปานกลาง";

    // สร้างลำดับคำแนะนำเชิงออกแบบและผังเมือง (Urban Design & Planning Actions)
    const actions: { step: number; title: string; detail: string; tag: string }[] = [];

    if ((green300 != null && green300 < 0.15) || (ndvi300 != null && ndvi300 < 0.18)) {
        actions.push({
            step: 1,
            tag: "Green Infrastructure",
            title: "เพิ่มร่มเงาและพื้นที่สีเขียวแทรกซึม (Pocket Park & Tree Canopy)",
            detail: `พื้นที่สีเขียวปัจจุบันมีเพียง ${green300 != null ? (green300 * 100).toFixed(1) + "%" : "ระดับต่ำ"} ควรปลูกไม้ยืนต้นให้ร่มเงาตามแนวทางเดินหลัก ลานกิจกรรมชุมชน และใช้ไม้เลื้อย/ผนังสีเขียวในจุดที่พื้นที่จำกัด`,
        });
    }

    if (builtup300 != null && builtup300 >= 38) {
        actions.push({
            step: actions.length + 1,
            tag: "Urban Ventilation & Cool Roof",
            title: "เปิดช่องทางลมและลดความร้อนแผ่รังสีจากหลังคา (Cool Roof & Wind Corridor)",
            detail: `ความหนาแน่นอาคารสูงถึง ${builtup300.toFixed(1)}% (${bldgCount300 ?? "—"} หลัง) ทำให้ลมระบายยาก ควรทาสีสะท้อนความร้อนบนหลังคาสังกะสี/คอนกรีต และรักษาแนวซอยหรือช่องว่างระหว่างอาคารไม่ให้ต่อเติมปิดทางลม`,
        });
    }

    if (lst300 != null && lst300 >= 39) {
        actions.push({
            step: actions.length + 1,
            tag: "Public Health & Cooling Node",
            title: "จัดตั้งจุดพักคอยคลายร้อนและลดพื้นผิวดาดแข็ง (Cooling Shelter)",
            detail: `อุณหภูมิพื้นผิวเฉลี่ย ${lst300.toFixed(2)} °C (พีคสุด ${lstP90_300?.toFixed(2) ?? "—"} °C) ควรจัดจุดพักร่มเงาพร้อมน้ำดื่มสำหรับผู้สูงอายุและเด็กเล็กในช่วง 10:00–15:00 น. และเปลี่ยนพื้นคอนกรีตลานชุมชนเป็นบล็อกปูพื้นซึมน้ำได้`,
        });
    }

    if (lst500 != null && lst300 != null && lst500 > lst300 + 0.5) {
        actions.push({
            step: actions.length + 1,
            tag: "Buffer Zone",
            title: "สร้างแนวกันชนความร้อนจากย่านรอบนอก (500 m Edge Buffer)",
            detail: `พื้นที่รอบนอก 500 ม. ร้อนกว่าใจกลางชุมชน (${lst500.toFixed(2)} °C vs ${lst300.toFixed(2)} °C) ควรปลูกแนวไม้พุ่มและต้นไม้สูงตามขอบชุมชนฝั่งติดถนนใหญ่เพื่อกรองความร้อนและฝุ่น`,
        });
    }

    if (actions.length === 0) {
        actions.push({
            step: 1,
            tag: "Conservation & Monitoring",
            title: "รักษาสัดส่วนพื้นที่สีเขียวและเฝ้าระวังจุดร้อนเฉพาะจุด",
            detail:
                "ชุมชนมีสภาพแวดล้อมค่อนข้างสมดุล ควรอนุรักษ์เรือนยอดไม้เดิมและควบคุมไม่ให้ความหนาแน่นอาคารเพิ่มขึ้นจนปิดกั้นทางลม",
        });
    }

    return {
        id,
        name,
        district,
        riskLevel,
        lst300,
        lstP90_300,
        lst500,
        builtup300,
        builtup500,
        bldgCount300,
        green300,
        green500,
        ndvi300,
        ndvi500,
        score,
        rank: 1,
        total: Math.max(1, allPropsList.length),
        tier,
        tierLabel,
        theme,
        headlineReason,
        actions: actions.slice(0, 3),
    };
}

export function evaluateAllCommunities(features: GeoJSON.Feature[]): CommunityPriorityResult[] {
    const propsList = features.map((f) => (f.properties || {}) as Record<string, unknown>);
    const results = propsList.map((p) => analyzeSingleCommunity(p, propsList));
    results.sort((a, b) => b.score - a.score);
    results.forEach((item, idx) => {
        item.rank = idx + 1;
        item.total = results.length;
    });
    return results;
}

export function buildCommunityPriorityHtml(item: CommunityPriorityResult): string {
    const actionCards = item.actions
        .map(
            (a) => `
      <div class="prio-step">
        <div class="prio-step-head">
          <span class="prio-num">แนวทางที่ ${a.step}</span>
          <span class="prio-tag">${a.tag}</span>
        </div>
        <strong>${a.title}</strong>
        <p>${a.detail}</p>
      </div>`
        )
        .join("");

    return `
    <div class="prio-box tier-${item.tier}">
      <div class="prio-top">
        <div>
          <span class="prio-rank-badge">ลำดับความเร่งด่วนที่ #${item.rank} จาก ${item.total} ชุมชน</span>
          <div class="prio-tier-title">${item.tierLabel}</div>
        </div>
        <div class="prio-score-circle">
          <b>${item.score.toFixed(0)}</b>
          <span>/100</span>
        </div>
      </div>
      <div class="prio-reason">
        <b>สาเหตุความเปราะบางหลัก:</b> ${item.headlineReason}
      </div>
      <div class="prio-actions-wrap">
        <div class="prio-sec-title">🧭 ลำดับการแก้ไขเชิงออกแบบและผังเมือง (แนะนำทำก่อน → หลัง)</div>
        ${actionCards}
      </div>
    </div>
  `;
}

/**
 * ควบคุมหน้าวิเคราะห์และจัดลำดับความเปราะบางของชุมชนใน Tab 2 (โหมด CS341)
 */
export class CommunityStatsController {
    private cached: CommunityPriorityResult[] = [];
    private barChart: echarts.ECharts | null = null;
    private scatterChart: echarts.ECharts | null = null;
    private selectedDistrict = "all";
    private selectedTier = "all";

    async render() {
        const kpiBox = document.getElementById("cs341-kpis");
        const tbody = document.getElementById("cs341-ranking-body");
        const spotlight = document.getElementById("cs341-top-spotlight");
        if (!kpiBox || !tbody) return;

        if (!this.cached.length) {
            try {
                const fc = await api<GeoJSON.FeatureCollection>("/community");
                this.cached = evaluateAllCommunities(fc.features || []);
            } catch {
                tbody.innerHTML = `<tr><td colspan="7" class="err">ไม่สามารถโหลดข้อมูลชุมชนจาก /community ได้</td></tr>`;
                return;
            }
        }

        // เติมตัวเลือกเขตอัตโนมัติ
        const distSel = document.getElementById("cs341-filter-district") as HTMLSelectElement | null;
        if (distSel && distSel.options.length <= 1) {
            const districts = Array.from(new Set(this.cached.map((c) => c.district).filter((d) => d && d !== "—")));
            districts.forEach((d) => {
                const opt = document.createElement("option");
                opt.value = d;
                opt.textContent = `เขต${d}`;
                distSel.appendChild(opt);
            });
            distSel.onchange = () => {
                this.selectedDistrict = distSel.value;
                this.renderFiltered();
            };
        }

        const tierSel = document.getElementById("cs341-filter-tier") as HTMLSelectElement | null;
        if (tierSel) {
            tierSel.onchange = () => {
                this.selectedTier = tierSel.value;
                this.renderFiltered();
            };
        }

        // สรุป KPI ภาพรวมทั้งหมด
        const criticalCount = this.cached.filter((c) => c.tier === "critical" || c.tier === "high").length;
        const avgLst =
            this.cached.reduce((s, c) => s + (c.lst300 ?? 0), 0) / Math.max(1, this.cached.filter((c) => c.lst300 != null).length);
        const avgBuiltup =
            this.cached.reduce((s, c) => s + (c.builtup300 ?? 0), 0) /
            Math.max(1, this.cached.filter((c) => c.builtup300 != null).length);
        const avgGreen =
            (this.cached.reduce((s, c) => s + (c.green300 ?? 0), 0) /
                Math.max(1, this.cached.filter((c) => c.green300 != null).length)) *
            100;

        kpiBox.innerHTML = `
      <div class="kpi live-kpi warm">
        <div class="l">ชุมชนเสี่ยงสูง/วิกฤต (ต้องช่วยก่อน)</div>
        <div class="v" style="color:#dc2626;">${criticalCount} / ${this.cached.length} แห่ง</div>
        <div class="l">คะแนนความเปราะบาง ≥ 72</div>
      </div>
      <div class="kpi live-kpi amber">
        <div class="l">อุณหภูมิพื้นผิวเฉลี่ยชุมชน (300 ม.)</div>
        <div class="v">${avgLst.toFixed(2)} °C</div>
        <div class="l">สูงสุด: ${this.cached[0]?.lst300?.toFixed(2) ?? "—"} °C</div>
      </div>
      <div class="kpi live-kpi indigo">
        <div class="l">ความหนาแน่นอาคารเฉลี่ย (300 ม.)</div>
        <div class="v">${avgBuiltup.toFixed(1)} %</div>
        <div class="l">สะท้อนการบังลมและมวลความร้อน</div>
      </div>
      <div class="kpi" style="border-top-color:#10b981;">
        <div class="l">สัดส่วนพื้นที่สีเขียวเฉลี่ย</div>
        <div class="v" style="color:#059669;">${avgGreen.toFixed(1)} %</div>
        <div class="l">เกณฑ์แนะนำเมืองเย็น ≥ 25–30%</div>
      </div>
    `;

        // การ์ดไฮไลต์ Top 3 ชุมชนที่ควรเข้าช่วยเหลือก่อน
        if (spotlight) {
            const top3 = this.cached.slice(0, 3);
            spotlight.innerHTML = top3
                .map(
                    (c) => `
        <div class="tk-card ${c.rank === 1 ? "red" : "amber"}">
          <div class="tk-title">อันดับ #${c.rank} · เขต${c.district} · คะแนน ${c.score.toFixed(0)}/100</div>
          <div class="tk-val">${c.name}</div>
          <p><b>ปัญหาหลัก:</b> ${c.headlineReason}</p>
          <p style="margin-top:5px;"><b>แก้ด่วนอันดับ 1:</b> ${c.actions[0]?.title || "เพิ่มร่มเงาและลดความร้อนพื้นผิว"}</p>
        </div>`
                )
                .join("");
        }

        this.renderCharts();
        this.renderFiltered();
        this.renderGlossary();
    }

    private renderFiltered() {
        const tbody = document.getElementById("cs341-ranking-body");
        if (!tbody) return;

        const list = this.cached.filter((c) => {
            if (this.selectedDistrict !== "all" && c.district !== this.selectedDistrict) return false;
            if (this.selectedTier === "high" && c.tier !== "critical" && c.tier !== "high") return false;
            if (this.selectedTier === "moderate" && c.tier !== "moderate") return false;
            if (this.selectedTier === "low" && c.tier !== "low") return false;
            return true;
        });

        tbody.innerHTML = list
            .map((c) => {
                const pillCls = c.tier === "critical" || c.tier === "high" ? "danger" : c.tier === "moderate" ? "warn" : "cool";
                const actionSummary = c.actions
                    .map((a, idx) => `<div><b>${idx + 1}.</b> ${a.title}</div>`)
                    .join("");
                return `
          <tr>
            <td><b>#${c.rank}</b></td>
            <td>
              <b>${c.name}</b><br>
              <small>เขต${c.district} · <code>${c.id}</code></small>
            </td>
            <td>
              <span class="v-pill ${pillCls}">${c.tierLabel}</span><br>
              <small>คะแนน <b>${c.score.toFixed(0)}</b>/100</small>
            </td>
            <td><b>${c.lst300 != null ? c.lst300.toFixed(2) + " °C" : "—"}</b><br><small>พีค ${c.lstP90_300?.toFixed(1) ?? "—"} °C</small></td>
            <td>${c.builtup300 != null ? c.builtup300.toFixed(1) + "%" : "—"}<br><small>${c.bldgCount300 ?? "—"} หลัง</small></td>
            <td>${c.green300 != null ? (c.green300 * 100).toFixed(1) + "%" : "—"}<br><small>NDVI ${c.ndvi300?.toFixed(2) ?? "—"}</small></td>
            <td style="font-size:12px; line-height:1.45;">${actionSummary}</td>
          </tr>
        `;
            })
            .join("");
    }

    private renderCharts() {
        const top10 = this.cached.slice(0, 10).reverse();
        const barEl = document.getElementById("cs341-c-bar");
        if (barEl) {
            if (!this.barChart) {
                this.barChart = echarts.init(barEl);
                new ResizeObserver(() => this.barChart?.resize()).observe(barEl);
            }
            this.barChart.setOption({
                tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
                grid: { left: 135, right: 24, top: 16, bottom: 26 },
                xAxis: { type: "value", max: 100, name: "คะแนน" },
                yAxis: {
                    type: "category",
                    data: top10.map((c) => `#${c.rank} ${c.name.slice(0, 18)}`),
                    axisLabel: { fontSize: 11 },
                },
                series: [
                    {
                        name: "ดัชนีความเปราะบาง",
                        type: "bar",
                        data: top10.map((c) => ({
                            value: c.score,
                            itemStyle: {
                                color: c.score >= 80 ? "#dc2626" : c.score >= 72 ? "#f97316" : "#eab308",
                                borderRadius: [0, 4, 4, 0],
                            },
                        })),
                        label: { show: true, position: "right", formatter: "{c}" },
                    },
                ],
            });
        }

        const scEl = document.getElementById("cs341-c-scatter");
        if (scEl) {
            if (!this.scatterChart) {
                this.scatterChart = echarts.init(scEl);
                new ResizeObserver(() => this.scatterChart?.resize()).observe(scEl);
            }
            this.scatterChart.setOption({
                tooltip: {
                    trigger: "item",
                    formatter: (p: { data: [number, number, number, string, number] }) => {
                        const [b, lst, g, name, rank] = p.data;
                        return `<b>#${rank} ${name}</b><br>ความหนาแน่นอาคาร: ${b.toFixed(1)}%<br>อุณหภูมิ LST: ${lst.toFixed(2)} °C<br>พื้นที่สีเขียว: ${g.toFixed(1)}%`;
                    },
                },
                grid: { left: 46, right: 20, top: 24, bottom: 36 },
                xAxis: { type: "value", name: "ความหนาแน่นอาคาร (%)", nameLocation: "middle", nameGap: 22 },
                yAxis: { type: "value", name: "LST (°C)", scale: true },
                series: [
                    {
                        type: "scatter",
                        symbolSize: (d: number[]) => Math.max(10, 26 - d[2] * 0.4),
                        data: this.cached
                            .filter((c) => c.builtup300 != null && c.lst300 != null)
                            .map((c) => [c.builtup300!, c.lst300!, (c.green300 ?? 0) * 100, c.name, c.rank]),
                        itemStyle: {
                            color: (p: { data: number[] }) => (p.data[1] >= 41 ? "#dc2626" : p.data[1] >= 38 ? "#f59e0b" : "#10b981"),
                            opacity: 0.85,
                        },
                    },
                ],
            });
        }
    }

    private renderGlossary() {
        const box = document.getElementById("cs341-glossary-box");
        if (!box) return;
        box.innerHTML = COMMUNITY_GLOSSARY.map(
            (g) => `
      <div class="gloss-item">
        <div class="g-head"><span class="g-tag">${g.code}</span><strong>${g.name}</strong></div>
        <p>${g.desc}</p>
      </div>`
        ).join("");
    }
}