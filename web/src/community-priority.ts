import * as echarts from "echarts";

export type RiskTier = "high" | "moderate" | "low";

export interface CommunityPriorityResult {
  id: string;
  name: string;
  district: string;
  rank: number;
  total: number;
  /** คะแนนที่แสดง (รวม override ตาม risk_level แล้ว) */
  score: number;
  /** คะแนนดิบจากสูตร 3 มิติ ไม่รวม override (ใช้คำนวณส่วนต่างหลังจำลอง) */
  rawScore: number;
  tier: RiskTier;
  tierLabel: string;
  rawRiskLevel: string;
  theme: "red" | "amber" | "teal";
  lst300: number | null;
  lst500: number | null;
  lstP90_300: number | null;
  builtup300: number | null;
  builtup500: number | null;
  greenRatio300: number | null;
  greenRatio500: number | null;
  ndvi300: number | null;
  ndvi500: number | null;
  bldgCount300: number | null;
  /** พื้นที่ buffer 300 ม. จริงจาก GeoJSON (buffer_area_sqm_300) */
  bufferArea300Sqm: number | null;
  lstScenes300: number | null;
  ndviScenes300: number | null;
  dataComplete: boolean;
  missingFields: string[];
  primaryDriver: string;
  simulationNote: string;
}

export interface TreeSimulationResult {
  trees: number;
  canopyPerTreeSqm: number;
  addedCanopySqm: number;
  totalBufferSqm: number;
  builtupSqm: number;
  existingGreenSqm: number;
  availableOpenSqm: number;
  availableOpenPct: number;
  openAreaFillPct: number;
  currentGreenPct: number;
  simulatedGreenPct: number;
  deltaGreenPct: number;
  currentNdvi: number;
  simulatedNdvi: number;
  deltaNdvi: number;
  currentScore: number;
  simulatedScore: number;
  deltaScore: number;
  /** ส่วนต่างสัดส่วนสีเขียว (0–1) ที่ใช้คูณกับ slope */
  deltaGreenFrac: number;
  lstDropLow: number;
  lstDropHigh: number;
  lstDropMid: number;
  simulatedLstMid: number | null;
}

export interface CommunityDiagnosis {
  headline: string;
  summary: string;
  recommendations: string[];
}

type SimInput = Pick<
  CommunityPriorityResult,
  "lst300" | "lstP90_300" | "builtup300" | "greenRatio300" | "ndvi300" | "score"
> &
  Partial<Pick<CommunityPriorityResult, "rawScore" | "bufferArea300Sqm">>;

type DiagInput = Pick<
  CommunityPriorityResult,
  "name" | "lst300" | "lstP90_300" | "builtup300" | "greenRatio300" | "ndvi300" | "bldgCount300"
>;

export const TREE_CHOICES = [10, 30, 50, 100, 200] as const;
const CANOPY_SQM_PER_TREE = 25;
/** ใช้เมื่อ GeoJSON ไม่มี buffer_area_sqm_300 */
const FALLBACK_BUFFER_300_SQM = Math.round(Math.PI * 300 * 300);

/**
 * สัมประสิทธิ์จำลอง — ได้จากการถดถอยข้อมูลจริง 36 ชุมชน (ปี 2026, buffer 300 ม.)
 *   lst_mean_300 = 43.87 − 2.004·green_ratio_300 + 7.070·(builtup_pct_300/100)   (R² = 0.50)
 *   lst_mean_300 = 47.40 − 5.441·green_ratio_300                                  (ไม่ควบคุม builtup)
 *   ndvi_mean_300 = 0.0673 + 0.5408·green_ratio_300                               (R² = 0.98)
 * เป็นความสัมพันธ์ข้ามชุมชน (cross-section) ไม่ใช่ผลเชิงสาเหตุ:
 *   - ช่วงล่างของการลด LST = slope ที่ควบคุม builtup แล้ว (SE 1.60, ยังไม่มีนัยสำคัญทางสถิติ)
 *   - ช่วงบนของการลด LST = slope เดี่ยว (รวมอิทธิพลของ builtup ที่สัมพันธ์กับสีเขียว r = −0.72)
 * หากข้อมูลเปลี่ยนปีหรือเพิ่มชุมชน ให้ถดถอยใหม่แล้วแก้ค่าตรงนี้
 */
export const SIM_MODEL = {
  n: 36,
  r2Adjusted: 0.5,
  slopeGreenAdjusted: -2.004,
  seGreenAdjusted: 1.602,
  slopeGreenUnadjusted: -5.441,
  slopeBuiltupAdjusted: 7.07,
  ndviPerGreen: 0.5408,
} as const;

/** ช่วง normalize ของคะแนน (ข้อมูลปี 2026: LST 43–48 °C, builtup 15–52%, green 13–66%) */
const SCORE_NORM = {
  heatMin: 34,
  heatRange: 14,
  builtupMax: 60,
  greenTarget: 0.5,
  wHeat: 0.4,
  wBuiltup: 0.3,
  wGreen: 0.3,
};

/**
 * เกณฑ์วินิจฉัย: เริ่มต้นใช้ค่าคงที่ แล้วถูกแทนที่ด้วย percentile (33.3 / 66.7)
 * ของชุมชนทั้งชุดทุกครั้งที่เรียก evaluateAllCommunities
 * เพื่อให้คำว่า "สูง/ต่ำ" หมายถึงสูง/ต่ำเมื่อเทียบกับชุมชนอื่นในชุดข้อมูล
 */
const cuts = {
  lstMod: 38,
  lstHigh: 42,
  builtupMod: 25,
  builtupHigh: 40,
  greenLowPct: 20,
  ndviLow: 0.2,
};

const numOrNull = (v: unknown): number | null => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
};

const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

const fmtNum = (v: number | null | undefined, d = 1) =>
  v == null || Number.isNaN(v) ? "—" : v.toFixed(d);

const escHtml = (s: unknown) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

/** ค่า id/ชื่อ: รับ 0 ได้ ปฏิเสธเฉพาะ null/undefined/สตริงว่าง */
const strOr = (v: unknown, fallback: string): string => {
  if (v == null) return fallback;
  const s = String(v).trim();
  return s === "" ? fallback : s;
};

const percentile = (sortedAsc: number[], q: number): number => {
  if (!sortedAsc.length) return NaN;
  const pos = (sortedAsc.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sortedAsc[lo] + (sortedAsc[hi] - sortedAsc[lo]) * (pos - lo);
};

/** สูตรคะแนนดิบ 3 มิติ (ใช้ร่วมกันทั้งการจัดอันดับและการจำลอง เพื่อให้ก่อน/หลังเทียบกันได้) */
function computeRawScore(
  lst: number | null,
  builtupPct: number | null,
  greenRatio: number | null
): { raw: number; heatNorm: number; bldgNorm: number; greenDeficitNorm: number } {
  const heatNorm = lst != null ? clamp(((lst - SCORE_NORM.heatMin) / SCORE_NORM.heatRange) * 100, 0, 100) : 50;
  const bldgNorm = builtupPct != null ? clamp((builtupPct / SCORE_NORM.builtupMax) * 100, 0, 100) : 45;
  const greenDeficitNorm =
    greenRatio != null ? clamp((1 - clamp(greenRatio / SCORE_NORM.greenTarget, 0, 1)) * 100, 0, 100) : 50;
  const raw =
    heatNorm * SCORE_NORM.wHeat + bldgNorm * SCORE_NORM.wBuiltup + greenDeficitNorm * SCORE_NORM.wGreen;
  return { raw, heatNorm, bldgNorm, greenDeficitNorm };
}

export function diagnoseCommunity(c: DiagInput, sim?: TreeSimulationResult): CommunityDiagnosis {
  const lst = c.lst300;
  const p90 = c.lstP90_300;
  const builtup = c.builtup300;
  const greenPct = c.greenRatio300 != null ? c.greenRatio300 * 100 : null;
  const ndvi = c.ndvi300;

  const findings: string[] = [];
  const recommendations: string[] = [];

  const isHighHeat = lst != null && lst >= cuts.lstHigh;
  const isModHeat = lst != null && lst >= cuts.lstMod && lst < cuts.lstHigh;
  const isHighBuiltup = builtup != null && builtup >= cuts.builtupHigh;
  const isModBuiltup = builtup != null && builtup >= cuts.builtupMod && builtup < cuts.builtupHigh;
  const isLowGreen =
    (greenPct != null && greenPct < cuts.greenLowPct) || (ndvi != null && ndvi < cuts.ndviLow);

  if (isHighHeat) {
    findings.push(
      `อุณหภูมิพื้นผิวเฉลี่ยสูงเมื่อเทียบกับชุมชนอื่น (${fmtNum(lst, 2)} °C${p90 != null ? ` · ช่วงพีค p90 สูงถึง ${fmtNum(p90, 2)} °C` : ""})`
    );
  } else if (isModHeat) {
    findings.push(`อุณหภูมิพื้นผิวอยู่ระดับกลาง (${fmtNum(lst, 2)} °C)`);
  } else if (lst != null) {
    findings.push(`อุณหภูมิพื้นผิวค่อนข้างต่ำเมื่อเทียบกับชุมชนอื่น (${fmtNum(lst, 2)} °C)`);
  }

  if (isLowGreen) {
    findings.push(
      `สัดส่วนพื้นที่สีเขียวน้อย (${greenPct != null ? fmtNum(greenPct, 1) + "%" : "—"}${ndvi != null ? ` · NDVI ${fmtNum(ndvi, 3)}` : ""})`
    );
    recommendations.push(
      `เพิ่มพื้นที่สีเขียวและร่มเงาไม้ยืนต้นในจุดว่าง/ริมทางเดิน เพื่อเพิ่มการคายความชื้นและลดรังสีความร้อนตกกระทบพื้นผิว`
    );
  } else if (greenPct != null) {
    findings.push(`มีพื้นที่สีเขียว ${fmtNum(greenPct, 1)}%`);
  }

  if (isHighBuiltup) {
    findings.push(
      `ความหนาแน่นอาคารสูง (${fmtNum(builtup, 1)}%${c.bldgCount300 != null ? ` · ประมาณ ${Math.round(c.bldgCount300)} หลัง` : ""}) ทำให้เกิดการสะสมความร้อนและกีดขวางการระบายลม`
    );
    recommendations.push(
      `ลดแรงกดดันจากความหนาแน่นและมวลความร้อนของอาคาร โดยเน้นแนวทางลดความร้อนบนหลังคา/พื้นผิวดาดแข็ง (เช่น Cool/Green Roof, พื้นซึมผ่านได้) และรักษาช่องทางระบายลมระหว่างอาคาร`
    );
  } else if (isModBuiltup) {
    findings.push(`ความหนาแน่นอาคารระดับปานกลาง (${fmtNum(builtup, 1)}%)`);
    recommendations.push(
      `จัดวางจุดร่มเงาและไม้พุ่ม/ไม้ยืนต้นเชื่อมโยงเป็นเครือข่าย (Green Corridor) ร่วมกับพื้นที่ว่างของชุมชน`
    );
  }

  if (sim && sim.availableOpenSqm > 0) {
    if (sim.openAreaFillPct < 5 && sim.trees >= 100) {
      recommendations.push(
        `แม้จำลองเพิ่ม ${sim.trees} ต้น แต่คิดเป็นสัดส่วนเพียง ${sim.openAreaFillPct.toFixed(1)}% ของพื้นที่เปิดโล่งที่มีอยู่ (${sim.availableOpenSqm.toLocaleString()} ตร.ม.) จึงควรทำควบคู่กับการลดความร้อนสะสมของอาคารและพื้นผิวดาดแข็งเพื่อให้เห็นผลชัดขึ้น`
      );
    } else if (recommendations.length === 0) {
      recommendations.push(
        `รักษาสัดส่วนพื้นที่สีเขียวเดิมและเพิ่มร่มเงาเฉพาะจุดร้อนหรือจุดพักผ่อนกลางแจ้งของชุมชน`
      );
    }
  }

  const headline =
    isHighHeat && isLowGreen && isHighBuiltup
      ? "ปัจจัยหลัก: ร้อนสะสมสูง + พื้นที่สีเขียวน้อย + อาคารหนาแน่น"
      : isHighHeat && isLowGreen
        ? "ปัจจัยหลัก: ความร้อนสูงร่วมกับสัดส่วนพื้นที่สีเขียวน้อย"
        : isHighBuiltup
          ? "ปัจจัยหลัก: ความหนาแน่นของอาคารและการสะสมความร้อน"
          : findings[0] || "ปัจจัยเชิงพื้นที่ของชุมชน";

  const summary =
    findings.length > 0
      ? `จากข้อมูลเชิงพื้นที่ของ ${c.name} พบว่า${findings.join(" และ ")}`
      : `ยังไม่มีข้อมูลตัวแปรครบถ้วนสำหรับวินิจฉัย ${c.name}`;

  return {
    headline,
    summary,
    recommendations: Array.from(new Set(recommendations)),
  };
}

export function buildCommunityDiagnosisHtml(c: DiagInput, sim?: TreeSimulationResult): string {
  const diag = diagnoseCommunity(c, sim);
  const recItems = diag.recommendations
    .map((r) => `<li style="margin-bottom:4px;">${escHtml(r)}</li>`)
    .join("");

  return `
    <div style="margin-top:8px; padding:10px; border-radius:8px; background:#f8fafc; border:1px solid #e2e8f0;">
      <div style="font-weight:700; font-size:13px; color:#0f172a; margin-bottom:4px;">🔎 ${escHtml(diag.headline)}</div>
      <p class="note" style="margin:0 0 6px; color:#334155;">${escHtml(diag.summary)}</p>
      ${recItems
      ? `<div style="font-weight:600; font-size:12px; color:#0f172a; margin-bottom:3px;">💡 ข้อเสนอแนะเบื้องต้นจากบริบทข้อมูล:</div>
             <ul class="note" style="margin:0; padding-left:18px; color:#334155;">${recItems}</ul>`
      : ""
    }
    </div>
  `;
}

export function simulateTreeAddition(c: SimInput, trees: number): TreeSimulationResult {
  const safeTrees = Math.max(0, Math.round(trees));
  const addedCanopySqm = safeTrees * CANOPY_SQM_PER_TREE;

  const bufferSqm = c.bufferArea300Sqm && c.bufferArea300Sqm > 0 ? c.bufferArea300Sqm : FALLBACK_BUFFER_300_SQM;

  const builtupFrac = clamp((c.builtup300 ?? 30) / 100, 0, 0.85);
  const baseGreenFrac = clamp(c.greenRatio300 ?? 0.2, 0, 0.9);

  const builtupSqm = Math.round(bufferSqm * builtupFrac);
  const existingGreenSqm = Math.round(bufferSqm * baseGreenFrac);

  // พื้นที่เหลือ = 1 − อาคาร − สีเขียว (ไม่บังคับขั้นต่ำ ถ้าเต็มแล้วคือปลูกเพิ่มไม่ได้)
  const openFrac = clamp(1 - builtupFrac - baseGreenFrac, 0, 0.95);
  const availableOpenSqm = Math.round(bufferSqm * openFrac);
  const availableOpenPct = openFrac * 100;

  const openAreaFillPct =
    availableOpenSqm > 0 ? clamp((addedCanopySqm / availableOpenSqm) * 100, 0, 100) : 0;

  const addedGreenFrac = clamp(addedCanopySqm / bufferSqm, 0, openFrac);
  const simGreenFrac = clamp(baseGreenFrac + addedGreenFrac, 0, 0.98);
  const deltaGreenFrac = simGreenFrac - baseGreenFrac;

  const currentGreenPct = baseGreenFrac * 100;
  const simulatedGreenPct = simGreenFrac * 100;
  const deltaGreenPct = simulatedGreenPct - currentGreenPct;

  const baseNdvi = clamp(c.ndvi300 ?? 0.25, -0.2, 0.9);
  const deltaNdvi = deltaGreenFrac * SIM_MODEL.ndviPerGreen;
  const simulatedNdvi = clamp(baseNdvi + deltaNdvi, -0.2, 0.95);

  // LST: ช่วงล่าง = slope ที่ควบคุม builtup, ช่วงบน = slope เดี่ยว (ดูคำอธิบาย SIM_MODEL)
  const lstDropLow = Number((Math.abs(SIM_MODEL.slopeGreenAdjusted) * deltaGreenFrac).toFixed(3));
  const lstDropHigh = Number((Math.abs(SIM_MODEL.slopeGreenUnadjusted) * deltaGreenFrac).toFixed(3));
  const lstDropMid = Number(((lstDropLow + lstDropHigh) / 2).toFixed(3));
  const simulatedLstMid = c.lst300 != null ? Number((c.lst300 - lstDropMid).toFixed(3)) : null;

  // คะแนน: เทียบ "คะแนนดิบก่อน" กับ "คะแนนดิบหลัง" แล้วนำส่วนต่างไปลบจากคะแนนที่แสดง
  // (ไม่ให้การหลุด override ของ risk_level ถูกนับเป็นผลของต้นไม้)
  const before = computeRawScore(c.lst300, c.builtup300, c.greenRatio300);
  const afterLst = simulatedLstMid ?? c.lst300;
  const after = computeRawScore(afterLst, c.builtup300, c.greenRatio300 != null ? simGreenFrac : null);
  const rawDelta = Math.min(0, after.raw - before.raw);

  const currentScore = c.score;
  const simulatedScore = Number(clamp(currentScore + rawDelta, 0, 100).toFixed(1));
  const deltaScore = Number((simulatedScore - currentScore).toFixed(1));

  return {
    trees: safeTrees,
    canopyPerTreeSqm: CANOPY_SQM_PER_TREE,
    addedCanopySqm,
    totalBufferSqm: bufferSqm,
    builtupSqm,
    existingGreenSqm,
    availableOpenSqm,
    availableOpenPct,
    openAreaFillPct,
    currentGreenPct,
    simulatedGreenPct,
    deltaGreenPct,
    currentNdvi: baseNdvi,
    simulatedNdvi,
    deltaNdvi,
    currentScore,
    simulatedScore,
    deltaScore,
    deltaGreenFrac,
    lstDropLow,
    lstDropHigh,
    lstDropMid,
    simulatedLstMid,
  };
}

export function evaluateAllCommunities(
  features: { properties?: Record<string, unknown> | null }[]
): CommunityPriorityResult[] {
  const parsed = features.map((f, idx) => {
    const p = f.properties || {};
    const id = strOr(p.community_id, `COMM-${idx + 1}`);
    const name = strOr(p.name, id);
    // เก็บชื่อเขตแบบไม่มีคำว่า "เขต" นำหน้า (UI จะเติมเองจึงไม่ซ้ำเป็น "เขตเขต...")
    const district = strOr(p.district, "—").replace(/^เขต\s*/, "") || "—";
    const rawRiskLevel = String(p.risk_level ?? "").toLowerCase();

    const lst300 = numOrNull(p.lst_mean_300);
    const lst500 = numOrNull(p.lst_mean_500);
    const lstP90_300 = numOrNull(p.lst_p90_300);
    const builtup300 = numOrNull(p.builtup_pct_300);
    const builtup500 = numOrNull(p.builtup_pct_500);

    let greenRatio300 = numOrNull(p.green_ratio_300);
    if (greenRatio300 != null && greenRatio300 > 1) greenRatio300 = greenRatio300 / 100;

    let greenRatio500 = numOrNull(p.green_ratio_500);
    if (greenRatio500 != null && greenRatio500 > 1) greenRatio500 = greenRatio500 / 100;

    const ndvi300 = numOrNull(p.ndvi_mean_300);
    const ndvi500 = numOrNull(p.ndvi_mean_500);
    const bldgCount300 = numOrNull(p.building_count_300);
    const bufferArea300Sqm = numOrNull(p.buffer_area_sqm_300);
    const lstScenes300 = numOrNull(p.lst_n_scenes_300);
    const ndviScenes300 = numOrNull(p.ndvi_n_scenes_300);

    const missingFields: string[] = [];
    if (lst300 == null) missingFields.push("lst_mean_300");
    if (builtup300 == null) missingFields.push("builtup_pct_300");
    if (greenRatio300 == null) missingFields.push("green_ratio_300");

    const { raw, heatNorm, bldgNorm, greenDeficitNorm } = computeRawScore(lst300, builtup300, greenRatio300);

    let score = Math.round(raw);
    if (rawRiskLevel === "high") score = Math.max(score, 68);
    if (rawRiskLevel === "low") score = Math.min(score, 54);
    score = clamp(score, 0, 100);

    let tier: RiskTier = "moderate";
    let tierLabel = "ความเปราะบางปานกลาง (ควรเฝ้าระวัง)";
    let theme: "red" | "amber" | "teal" = "amber";
    if (score >= 65 || rawRiskLevel === "high") {
      tier = "high";
      tierLabel = "ความเปราะบางสูง";
      theme = "red";
    } else if (score < 50 || rawRiskLevel === "low") {
      tier = "low";
      tierLabel = "ความเปราะบางต่ำกว่าเกณฑ์";
      theme = "teal";
    }

    const drivers: { label: string; val: number }[] = [
      { label: `อุณหภูมิพื้นผิวเฉลี่ย (${fmtNum(lst300, 2)} °C)`, val: heatNorm },
      { label: `ความหนาแน่นอาคาร (${fmtNum(builtup300, 1)}%)`, val: bldgNorm },
      {
        label: `สัดส่วนพื้นที่สีเขียวต่ำ (${greenRatio300 != null ? fmtNum(greenRatio300 * 100, 1) + "%" : "—"})`,
        val: greenDeficitNorm,
      },
    ].sort((a, b) => b.val - a.val);

    const primaryDriver = `ปัจจัยเด่นในดัชนี: ${drivers[0].label}`;
    const simulationNote =
      "หมายเหตุ: สีวงกลมบนแผนที่แสดงระดับความเปราะบาง (ไม่ใช่ °C) LST คืออุณหภูมิพื้นผิวจากดาวเทียม ไม่ใช่อุณหภูมิอากาศ และตัวเลขการจำลองเพิ่มต้นไม้เป็น 'ค่าสมมติเชิงจำลอง' จากความสัมพันธ์ข้ามชุมชน ยังไม่ได้ลงพื้นที่จริง";

    return {
      id,
      name,
      district,
      rank: 0,
      total: features.length,
      score,
      rawScore: raw,
      tier,
      tierLabel,
      rawRiskLevel,
      theme,
      lst300,
      lst500,
      lstP90_300,
      builtup300,
      builtup500,
      greenRatio300,
      greenRatio500,
      ndvi300,
      ndvi500,
      bldgCount300,
      bufferArea300Sqm,
      lstScenes300,
      ndviScenes300,
      dataComplete: missingFields.length === 0,
      missingFields,
      primaryDriver,
      simulationNote,
    };
  });

  // ปรับเกณฑ์วินิจฉัยตามการกระจายจริงของชุดข้อมูล (ต้องมีอย่างน้อย 6 ชุมชน)
  if (parsed.length >= 6) {
    const sortedOf = (get: (x: CommunityPriorityResult) => number | null) =>
      parsed
        .map(get)
        .filter((v): v is number => v != null)
        .sort((a, b) => a - b);
    const lstS = sortedOf((x) => x.lst300);
    const bS = sortedOf((x) => x.builtup300);
    const gS = sortedOf((x) => (x.greenRatio300 != null ? x.greenRatio300 * 100 : null));
    const nS = sortedOf((x) => x.ndvi300);
    if (lstS.length >= 6) {
      cuts.lstMod = percentile(lstS, 1 / 3);
      cuts.lstHigh = percentile(lstS, 2 / 3);
    }
    if (bS.length >= 6) {
      cuts.builtupMod = percentile(bS, 1 / 3);
      cuts.builtupHigh = percentile(bS, 2 / 3);
    }
    if (gS.length >= 6) cuts.greenLowPct = percentile(gS, 1 / 3);
    if (nS.length >= 6) cuts.ndviLow = percentile(nS, 1 / 3);
  }

  parsed.sort((a, b) => b.score - a.score);
  parsed.forEach((item, i) => {
    item.rank = i + 1;
    item.total = parsed.length;
  });

  return parsed;
}

export function buildCommunitySimulationHtml(c: CommunityPriorityResult, defaultTrees = 50): string {
  const sim = simulateTreeAddition(c, defaultTrees);
  const badgeClass = c.tier === "high" ? "danger" : c.tier === "moderate" ? "warn" : "cool";

  const buttonsHtml = TREE_CHOICES.map(
    (n) =>
      `<button type="button" class="mini-btn ${n === defaultTrees ? "active" : ""}" data-sim-trees="${n}">+${n} ต้น</button>`
  ).join(" ");

  const sceneNote =
    c.lstScenes300 != null || c.ndviScenes300 != null
      ? `<p class="note" style="margin-top:6px;">คุณภาพข้อมูล: LST เฉลี่ยจาก ${c.lstScenes300 ?? "—"} scenes · NDVI/สีเขียวจาก ${c.ndviScenes300 ?? "—"} scenes</p>`
      : "";
  const missingNote = !c.dataComplete
    ? `<p class="note" style="margin-top:6px;">⚠️ <b>ข้อมูลไม่ครบ:</b> ขาด ${escHtml(c.missingFields.join(", "))} คะแนนใช้ค่ากลางแทนตัวแปรที่ขาด จึงเทียบกับชุมชนอื่นได้จำกัด</p>`
    : "";

  return `
    <div class="d-card">
      <div class="verdict-top">
        <div>
          <div class="d-kpis" style="grid-template-columns: 1fr 1fr; margin-bottom: 6px;">
            <div class="d-kpi ${c.theme === "red" ? "warm" : c.theme === "amber" ? "amber" : "cool"}">
              <div class="lbl">คะแนนความเปราะบาง</div>
              <div class="val">${c.score} / 100</div>
              <div class="sub">อันดับ #${c.rank} จาก ${c.total} ชุมชน</div>
            </div>
            <div class="d-kpi blue">
              <div class="lbl">สถานะความเปราะบาง</div>
              <div class="val" style="font-size:14px;">${escHtml(c.rawRiskLevel || "—")}</div>
              <div class="sub">สีวงกลม = ความเปราะบาง ไม่ใช่ °C</div>
            </div>
          </div>
          <span class="v-pill ${badgeClass}">${escHtml(c.tierLabel)}</span>
        </div>
      </div>
      <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
        <button type="button" class="mini-btn" id="drawer-compare-comm-btn" data-compare-id="${escHtml(c.id)}">⚖️ เปรียบเทียบกับชุมชนอื่น</button>
      </div>
      <p class="note" style="margin-top:8px;"><b>${escHtml(c.primaryDriver)}</b></p>
      <div id="comm-diag-box">${buildCommunityDiagnosisHtml(c, sim)}</div>
      <p class="note" style="margin-top:6px;">${escHtml(c.simulationNote)}</p>
      ${sceneNote}
      ${missingNote}
    </div>

    <div class="d-card" id="comm-sim-box">
      <h4>🧪 จำลองสมมติฐานการเพิ่มต้นไม้ (10–200 ต้น · ค่าสมมติ)</h4>
      <p class="note">เลือกจำนวนต้นไม้สมมติ (เรือนยอดเฉลี่ย ~${CANOPY_SQM_PER_TREE} ตร.ม./ต้น เมื่อโตเต็มที่) เพื่อคำนวณเทียบกับพื้นที่เปิดโล่งจริงของชุมชน:</p>
      <div style="display:flex; gap:6px; flex-wrap:wrap; margin:8px 0 10px;" id="comm-sim-btns">
        ${buttonsHtml}
      </div>
      <div id="comm-sim-result">
        ${renderSimulationSummary(c, sim)}
      </div>
    </div>
  `;
}

function renderSimulationSummary(
  c: Pick<
    CommunityPriorityResult,
    "name" | "lst300" | "lstP90_300" | "builtup300" | "greenRatio300" | "ndvi300" | "bldgCount300"
  >,
  s: TreeSimulationResult
): string {
  const scoreDeltaTxt = s.deltaScore <= 0 ? s.deltaScore.toFixed(1) : "+" + s.deltaScore.toFixed(1);
  return `
    <div class="d-kpis" style="grid-template-columns: 1fr 1fr; margin-bottom:8px;">
      <div class="d-kpi cool">
        <div class="lbl">พื้นที่สีเขียวใหม่ (+${s.trees} ต้น)</div>
        <div class="val">${s.simulatedGreenPct.toFixed(2)} %</div>
        <div class="sub">เดิม ${s.currentGreenPct.toFixed(2)}% (+${s.deltaGreenPct.toFixed(2)}% · เติมพื้นที่เปิดโล่ง ${s.openAreaFillPct.toFixed(2)}%)</div>
      </div>
      <div class="d-kpi warm">
        <div class="lbl">ช่วง LST ที่คาดว่าจะลด (ค่าสมมติ)</div>
        <div class="val">-${s.lstDropLow.toFixed(3)} ถึง -${s.lstDropHigh.toFixed(3)} °C</div>
        <div class="sub">ค่ากลางสมมติ: ${s.simulatedLstMid != null ? s.simulatedLstMid.toFixed(2) + " °C" : "—"}</div>
      </div>
    </div>
    <table class="d-table">
      <thead>
        <tr>
          <th>ตัวแปร</th>
          <th>ค่าปัจจุบันของชุมชน</th>
          <th>ค่าจำลอง (+${s.trees} ต้น)</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>พื้นที่เปิดโล่งที่ปลูกแทรกได้</td>
          <td>${s.availableOpenSqm.toLocaleString()} ตร.ม. (${s.availableOpenPct.toFixed(1)}%)</td>
          <td>ถูกเติมร่มเงาเพิ่ม <b>${s.addedCanopySqm.toLocaleString()} ตร.ม.</b> (${s.openAreaFillPct.toFixed(2)}%)</td>
        </tr>
        <tr>
          <td>สัดส่วนพื้นที่สีเขียว (300 ม.)</td>
          <td>${s.currentGreenPct.toFixed(2)} %</td>
          <td><b>${s.simulatedGreenPct.toFixed(2)} %</b> (+${s.deltaGreenPct.toFixed(2)}%)</td>
        </tr>
        <tr>
          <td>ดัชนีพืชพรรณ (NDVI)</td>
          <td>${s.currentNdvi.toFixed(3)}</td>
          <td><b>${s.simulatedNdvi.toFixed(3)}</b> (+${s.deltaNdvi.toFixed(3)})</td>
        </tr>
        <tr>
          <td>อุณหภูมิพื้นผิว (LST 300 ม.)</td>
          <td>${c.lst300 != null ? c.lst300.toFixed(2) + " °C" : "—"}</td>
          <td><b>${s.simulatedLstMid != null ? s.simulatedLstMid.toFixed(2) + " °C" : "—"}</b> (ช่วง -${s.lstDropLow.toFixed(3)} ถึง -${s.lstDropHigh.toFixed(3)} °C)</td>
        </tr>
        <tr>
          <td>คะแนนความเปราะบางรวม</td>
          <td>${s.currentScore} / 100</td>
          <td><b>${s.simulatedScore.toFixed(1)} / 100</b> (${scoreDeltaTxt} คะแนน)</td>
        </tr>
      </tbody>
    </table>

    <details class="glossary-card" style="margin-top:8px;">
      <summary>
        <span>📐 ดูหลักฐานและขั้นตอนการคำนวณตัวเลขสมมตินี้</span>
        <span class="gloss-hint">กดเพื่อดูสมการและตัวแปรตั้งต้น</span>
      </summary>
      <div class="glossary-list">
        <div class="gloss-item">
          <div class="g-head"><span class="g-tag">ขั้นที่ 1</span><strong>คำนวณพื้นที่เปิดโล่งที่ปลูกแทรกได้ (A_open)</strong></div>
          <p>
            พื้นที่ buffer 300 ม. (จาก GeoJSON) = <b>${s.totalBufferSqm.toLocaleString()} ตร.ม.</b><br>
            หักพื้นที่อาคาร (<code>builtup_pct_300</code> = ${fmtNum(c.builtup300, 1)}%) = <b>${s.builtupSqm.toLocaleString()} ตร.ม.</b><br>
            หักพื้นที่สีเขียวเดิม (<code>green_ratio_300</code> = ${s.currentGreenPct.toFixed(1)}%) = <b>${s.existingGreenSqm.toLocaleString()} ตร.ม.</b><br>
            → เหลือพื้นที่เปิดโล่ง/ดาดแข็งที่ปลูกแทรกได้ <b>${s.availableOpenSqm.toLocaleString()} ตร.ม.</b> (${s.availableOpenPct.toFixed(1)}%)<br>
            <span class="note">ส่วนนี้รวมถนนและพื้นที่ที่ปลูกไม่ได้จริง จึงเป็นค่าประมาณสูงสุด</span>
          </p>
        </div>
        <div class="gloss-item">
          <div class="g-head"><span class="g-tag">ขั้นที่ 2</span><strong>คำนวณเรือนยอดไม้ที่เพิ่มเทียบกับพื้นที่เปิดโล่ง</strong></div>
          <p>
            ปลูกสมมติ <b>${s.trees} ต้น</b> × เรือนยอด <b>${s.canopyPerTreeSqm} ตร.ม./ต้น</b> = <b>${s.addedCanopySqm.toLocaleString()} ตร.ม.</b><br>
            คิดเป็น <b>${s.openAreaFillPct.toFixed(2)}%</b> ของพื้นที่เปิดโล่งในชุมชน และเพิ่ม <code>green_ratio_300</code> ขึ้น <b>+${s.deltaGreenPct.toFixed(2)}%</b> (จาก ${s.currentGreenPct.toFixed(2)}% → ${s.simulatedGreenPct.toFixed(2)}%)
          </p>
        </div>
        <div class="gloss-item">
          <div class="g-head"><span class="g-tag">ขั้นที่ 3</span><strong>ประมาณการลดลงของ LST จากความสัมพันธ์ในข้อมูลจริง</strong></div>
          <p>
            ถดถอยข้อมูล ${SIM_MODEL.n} ชุมชน: <code>lst_mean_300 ~ green_ratio_300 + builtup_pct_300</code> (R² = ${SIM_MODEL.r2Adjusted.toFixed(2)})<br>
            ช่วงล่าง: slope สีเขียวที่ควบคุมอาคารแล้ว = <b>${SIM_MODEL.slopeGreenAdjusted} °C</b> ต่อสีเขียว 100% (SE ${SIM_MODEL.seGreenAdjusted})<br>
            ช่วงบน: slope สีเขียวเดี่ยว (ไม่ควบคุมอาคาร) = <b>${SIM_MODEL.slopeGreenUnadjusted} °C</b> ต่อสีเขียว 100%<br>
            Δ สัดส่วนสีเขียว = <b>${s.deltaGreenFrac.toFixed(5)}</b> → ลดลง <b>-${s.lstDropLow.toFixed(3)} ถึง -${s.lstDropHigh.toFixed(3)} °C</b><br>
            <span class="note">เป็นความสัมพันธ์ข้ามชุมชนจากตัวอย่างเล็ก ไม่ใช่ผลเชิงสาเหตุ</span>
          </p>
        </div>
        <div class="gloss-item">
          <div class="g-head"><span class="g-tag">ขั้นที่ 4</span><strong>คำนวณคะแนนความเปราะบางใหม่ (Vulnerability Re-scoring)</strong></div>
          <p>
            ใช้สมการเดียวกับการจัดอันดับ (ความร้อน 40% + ความหนาแน่นอาคาร 30% + การขาดแคลนสีเขียว 30%) แทนค่า <code>LST</code> และ <code>green_ratio</code> ใหม่ แล้วนำ<b>ส่วนต่างของคะแนนดิบ</b>ไปลบจากคะแนนปัจจุบัน ได้ <b>${s.simulatedScore.toFixed(1)}/100</b> (เปลี่ยนแปลง <b>${scoreDeltaTxt}</b> คะแนน)
          </p>
        </div>
      </div>
    </details>
    <p class="note" style="margin-top:6px;">⚠️ <b>ค่าสมมติเท่านั้น:</b> เป็นแบบจำลองจากตัวแปรเชิงพื้นที่ ยังไม่รวมทิศทางลม ความสูงตึกบังแดด อายุต้นไม้ (ต้นใหม่ใช้เวลาหลายปีกว่าเรือนยอดจะโตเต็มที่) และการสำรวจหน้างานจริง</p>
  `;
}

export function bindCommunitySimulationEvents(
  container: HTMLElement | null,
  c: CommunityPriorityResult,
  onCompareClick?: (communityId: string) => void
): void {
  if (!container) return;
  const btns = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-sim-trees]"));
  const resultEl = container.querySelector<HTMLElement>("#comm-sim-result");
  const diagEl = document.getElementById("comm-diag-box");
  if (resultEl) {
    btns.forEach((btn) => {
      btn.onclick = () => {
        const trees = Number(btn.dataset.simTrees || 50);
        const sim = simulateTreeAddition(c, trees);
        btns.forEach((b) => b.classList.toggle("active", b === btn));
        resultEl.innerHTML = renderSimulationSummary(c, sim);
        if (diagEl) diagEl.innerHTML = buildCommunityDiagnosisHtml(c, sim);
      };
    });
  }

  const compareBtn = document.getElementById("drawer-compare-comm-btn");
  if (compareBtn && onCompareClick) {
    compareBtn.onclick = () => onCompareClick(c.id);
  }
}

export class CommunityStatsController {
  private items: CommunityPriorityResult[] = [];
  private selectedId: string | null = null;
  private compareId: string | null = null;
  private selectedTrees = 50;
  private barChart: echarts.ECharts | null = null;
  private scatterChart: echarts.ECharts | null = null;
  private compareChart: echarts.ECharts | null = null;
  private onJumpToMap?: (communityId: string) => void;

  setJumpHandler(handler: (communityId: string) => void) {
    this.onJumpToMap = handler;
  }

  /**
   * คืน chart ที่ผูกกับ element ปัจจุบัน
   * - ถ้า element เดิมยังอยู่ ใช้ instance เดิมต่อ
   * - ถ้า element ถูกสร้างใหม่ (เช่น innerHTML ถูกเขียนทับ) จะ dispose ตัวเก่าแล้ว init ใหม่
   *   ป้องกัน instance เก่าค้างอยู่กับ DOM ที่ถูกถอดออกไปแล้ว (memory leak)
   */
  private ensureChart(existing: echarts.ECharts | null, el: HTMLElement): echarts.ECharts {
    if (existing && !existing.isDisposed() && existing.getDom() === el) return existing;
    if (existing && !existing.isDisposed()) existing.dispose();
    return echarts.init(el);
  }

  openCompareWith(communityId: string) {
    if (!this.items.length) return;
    const found = this.items.find((x) => x.id === communityId);
    if (found) {
      this.selectedId = found.id;
      const other = this.items.find((x) => x.id !== found.id);
      if (!this.compareId || this.compareId === found.id) {
        this.compareId = other ? other.id : found.id;
      }
    }
    this.render();
    document.getElementById("cs341-compare-dashboard")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  setCommunities(features: { properties?: Record<string, unknown> | null }[]) {
    this.items = evaluateAllCommunities(features);
    if (this.items.length > 0) {
      if (!this.selectedId || !this.items.some((x) => x.id === this.selectedId)) {
        this.selectedId = this.items[0].id;
      }
      if (!this.compareId || !this.items.some((x) => x.id === this.compareId)) {
        const fallback = this.items[this.items.length - 1];
        this.compareId = fallback ? fallback.id : this.items[0].id;
      }
    }
    this.render();
  }

  render() {
    const kpisEl = document.getElementById("cs341-kpis");
    const spotlightEl = document.getElementById("cs341-top-spotlight");
    const tbody = document.getElementById("cs341-ranking-body");
    const distSel = document.getElementById("cs341-filter-district") as HTMLSelectElement | null;
    const tierSel = document.getElementById("cs341-filter-tier") as HTMLSelectElement | null;
    const simPanelEl = document.getElementById("cs341-sim-dashboard");
    const cmpPanelEl = document.getElementById("cs341-compare-dashboard");

    if (!kpisEl || !tbody) return;

    if (!this.items.length) {
      kpisEl.innerHTML = `<div class="card"><p class="note">กำลังรอข้อมูลชุมชนจากระบบ (<code>curated/communities</code>)…</p></div>`;
      tbody.innerHTML = "";
      if (spotlightEl) spotlightEl.innerHTML = "";
      if (simPanelEl) simPanelEl.innerHTML = "";
      if (cmpPanelEl) cmpPanelEl.innerHTML = "";
      return;
    }

    if (distSel && distSel.options.length <= 1) {
      const districts = Array.from(new Set(this.items.map((x) => x.district).filter((d) => d && d !== "—")));
      districts.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d;
        opt.textContent = `เขต${d}`;
        distSel.appendChild(opt);
      });
      distSel.onchange = () => this.renderRowsAndCharts();
    }
    if (tierSel && !tierSel.onchange) {
      tierSel.onchange = () => this.renderRowsAndCharts();
    }

    const highCount = this.items.filter((x) => x.tier === "high").length;
    const avgScore = Math.round(this.items.reduce((s, x) => s + x.score, 0) / this.items.length);
    const incomplete = this.items.filter((x) => !x.dataComplete).length;
    const top1 = this.items[0];

    kpisEl.innerHTML = `
      <div class="kpi"><div class="l">ชุมชนทั้งหมด</div><div class="v">${this.items.length}</div><div class="l">เรียงลำดับจากเปราะบางมากสุด (#1) → น้อยสุด${incomplete ? ` · ⚠️ ข้อมูลไม่ครบ ${incomplete} แห่ง` : ""}</div></div>
      <div class="kpi"><div class="l">กลุ่มเปราะบางสูง</div><div class="v">${highCount}</div><div class="l">สีวงกลม = ความเปราะบาง (ไม่ใช่ °C)</div></div>
      <div class="kpi"><div class="l">คะแนนความเปราะบางเฉลี่ย</div><div class="v">${avgScore}</div><div class="l">จาก 100 คะแนน (ดัชนี 3 มิติ)</div></div>
      <div class="kpi"><div class="l">เปราะบางสูงสุด (#1)</div><div class="v" style="font-size:15px;">${escHtml(top1.name)}</div><div class="l">คะแนน ${top1.score}/100 · LST ${fmtNum(top1.lst300, 1)} °C</div></div>
    `;

    if (spotlightEl) {
      spotlightEl.innerHTML = this.items
        .slice(0, 3)
        .map(
          (c) => `
          <div class="card" style="padding:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
              <b>#${c.rank} ${escHtml(c.name)}</b>
              <span class="v-pill ${c.tier === "high" ? "danger" : c.tier === "moderate" ? "warn" : "cool"}">${c.score}/100</span>
            </div>
            <p class="note" style="margin:6px 0;">เขต${escHtml(c.district)} · LST ${fmtNum(c.lst300, 2)} °C · สีเขียว ${c.greenRatio300 != null ? fmtNum(c.greenRatio300 * 100, 1) + "%" : "—"}</p>
            ${buildCommunityDiagnosisHtml(c)}
            <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
              <button type="button" class="mini-btn" data-jump-comm="${escHtml(c.id)}">🗺️ ไปที่ชุมชนบนแผนที่</button>
              <button type="button" class="mini-btn" data-sim-comm="${escHtml(c.id)}">🧪 จำลองเพิ่มต้นไม้</button>
              <button type="button" class="mini-btn" data-cmp-comm="${escHtml(c.id)}">⚖️ เปรียบเทียบ</button>
            </div>
          </div>`
        )
        .join("");
    }

    this.renderRowsAndCharts();
    this.renderSimulationDashboard();
    this.renderCompareDashboard();
  }

  private buildRankedOptionsHtml(selectedVal: string | null): string {
    return this.items
      .map(
        (x) =>
          `<option value="${escHtml(x.id)}" ${x.id === selectedVal ? "selected" : ""}>#${x.rank} ${escHtml(x.name)} (${x.score}/100 · เขต${escHtml(x.district)})</option>`
      )
      .join("");
  }

  private renderSimulationDashboard() {
    const simPanelEl = document.getElementById("cs341-sim-dashboard");
    if (!simPanelEl) return;

    const target = this.items.find((x) => x.id === this.selectedId) || this.items[0];
    if (!target) return;

    const sim = simulateTreeAddition(target, this.selectedTrees);

    simPanelEl.innerHTML = `
      <div class="card" style="margin-bottom:12px;">
        <h3>
          <span>🧪 Dashboard เปรียบเทียบ: ปัจจุบัน vs ค่าสมมติการเพิ่มต้นไม้ (${escHtml(target.name)})</span>
          <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <button type="button" class="mini-btn" data-cmp-comm="${escHtml(target.id)}">⚖️ เปรียบเทียบกับชุมชนอื่น</button>
            <button type="button" class="mini-btn" data-jump-comm="${escHtml(target.id)}">🗺️ เปิดตำแหน่งบนแผนที่</button>
          </div>
        </h3>
        ${buildCommunityDiagnosisHtml(target, sim)}
        <p class="note" style="margin-top:8px;">
          <b>หลักการจำลอง:</b> คำนวณจากตัวแปรจริงของ <b>${escHtml(target.name)}</b> (<code>lst_mean_300</code> = ${fmtNum(target.lst300, 2)} °C, <code>lst_p90_300</code> = ${fmtNum(target.lstP90_300, 2)} °C, <code>builtup_pct_300</code> = ${fmtNum(target.builtup300, 1)}%, <code>green_ratio_300</code> = ${target.greenRatio300 != null ? fmtNum(target.greenRatio300 * 100, 1) + "%" : "—"}) โดยหักพื้นที่อาคารและสีเขียวเดิมออกเพื่อหาพื้นที่เปิดโล่งที่ปลูกแทรกได้ (<b>${sim.availableOpenSqm.toLocaleString()} ตร.ม.</b>) และใช้ slope จากการถดถอย ${SIM_MODEL.n} ชุมชนในการประมาณ LST
        </p>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin:10px 0;">
          ${TREE_CHOICES.map(
      (n) =>
        `<button type="button" class="mini-btn ${n === this.selectedTrees ? "active" : ""}" data-dash-trees="${n}">สมมติ +${n} ต้น</button>`
    ).join("")}
        </div>
        ${renderSimulationSummary(target, sim)}
      </div>
    `;

    simPanelEl.querySelectorAll<HTMLButtonElement>("[data-dash-trees]").forEach((btn) => {
      btn.onclick = () => {
        this.selectedTrees = Number(btn.dataset.dashTrees || 50);
        this.renderSimulationDashboard();
      };
    });

    simPanelEl.querySelectorAll<HTMLButtonElement>("[data-jump-comm]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.jumpComm;
        if (id && this.onJumpToMap) this.onJumpToMap(id);
      };
    });

    simPanelEl.querySelectorAll<HTMLButtonElement>("[data-cmp-comm]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.cmpComm;
        if (id) this.openCompareWith(id);
      };
    });
  }

  private renderCompareDashboard() {
    const cmpPanelEl = document.getElementById("cs341-compare-dashboard");
    if (!cmpPanelEl || this.items.length < 2) return;

    const a = this.items.find((x) => x.id === this.selectedId) || this.items[0];
    const b =
      this.items.find((x) => x.id === this.compareId && x.id !== a.id) ||
      this.items.find((x) => x.id !== a.id) ||
      this.items[1];
    this.compareId = b.id;

    const simA = simulateTreeAddition(a, this.selectedTrees);
    const simB = simulateTreeAddition(b, this.selectedTrees);

    const aGreen = a.greenRatio300 != null ? a.greenRatio300 * 100 : null;
    const bGreen = b.greenRatio300 != null ? b.greenRatio300 * 100 : null;

    const diffStr = (va: number | null, vb: number | null, dec = 2, unit = "") => {
      if (va == null || vb == null) return "—";
      const d = va - vb;
      const sign = d > 0 ? "+" : "";
      return `${sign}${d.toFixed(dec)}${unit}`;
    };

    cmpPanelEl.innerHTML = `
      <div class="card" style="margin-bottom:12px;">
        <h3>⚖️ Dashboard เปรียบเทียบ 2 ชุมชน (เรียงรายการจากเปราะบางสุด #1 → น้อยสุด #${this.items.length})</h3>
        <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:8px 0 12px;">
          <label class="note"><b>ชุมชนที่ 1:</b>
            <select id="cmp-sel-a" style="margin-left:4px; padding:4px 8px; border-radius:6px; border:1px solid #cbd5e1;">
              ${this.buildRankedOptionsHtml(a.id)}
            </select>
          </label>
          <label class="note"><b>เทียบกับชุมชนที่ 2:</b>
            <select id="cmp-sel-b" style="margin-left:4px; padding:4px 8px; border-radius:6px; border:1px solid #cbd5e1;">
              ${this.buildRankedOptionsHtml(b.id)}
            </select>
          </label>
          <button type="button" class="mini-btn" data-jump-comm="${escHtml(a.id)}">🗺️ ไปที่ #${a.rank} ${escHtml(a.name)}</button>
          <button type="button" class="mini-btn" data-jump-comm="${escHtml(b.id)}">🗺️ ไปที่ #${b.rank} ${escHtml(b.name)}</button>
        </div>

        <div class="grid2" style="margin-bottom:12px;">
          <div class="card" style="padding:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <b>#${a.rank} ${escHtml(a.name)}</b>
              <span class="v-pill ${a.tier === "high" ? "danger" : a.tier === "moderate" ? "warn" : "cool"}">${a.score}/100</span>
            </div>
            <p class="note" style="margin:4px 0 8px;">เขต${escHtml(a.district)} · สถานะ: ${escHtml(a.rawRiskLevel || "—")} · พื้นที่ว่างปลูกได้ ${simA.availableOpenSqm.toLocaleString()} ตร.ม.</p>
            ${buildCommunityDiagnosisHtml(a, simA)}
            <p class="note" style="margin-top:8px;">จำลอง +${this.selectedTrees} ต้น (ค่าสมมติ): สีเขียว <b>${simA.simulatedGreenPct.toFixed(2)}%</b> · คาด LST ลด <b>-${simA.lstDropLow.toFixed(3)} ถึง -${simA.lstDropHigh.toFixed(3)} °C</b></p>
          </div>
          <div class="card" style="padding:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <b>#${b.rank} ${escHtml(b.name)}</b>
              <span class="v-pill ${b.tier === "high" ? "danger" : b.tier === "moderate" ? "warn" : "cool"}">${b.score}/100</span>
            </div>
            <p class="note" style="margin:4px 0 8px;">เขต${escHtml(b.district)} · สถานะ: ${escHtml(b.rawRiskLevel || "—")} · พื้นที่ว่างปลูกได้ ${simB.availableOpenSqm.toLocaleString()} ตร.ม.</p>
            ${buildCommunityDiagnosisHtml(b, simB)}
            <p class="note" style="margin-top:8px;">จำลอง +${this.selectedTrees} ต้น (ค่าสมมติ): สีเขียว <b>${simB.simulatedGreenPct.toFixed(2)}%</b> · คาด LST ลด <b>-${simB.lstDropLow.toFixed(3)} ถึง -${simB.lstDropHigh.toFixed(3)} °C</b></p>
          </div>
        </div>

        <div class="table-scroll" style="margin-bottom:12px;">
          <table class="res-table">
            <thead>
              <tr>
                <th>ตัวชี้วัด</th>
                <th>#${a.rank} ${escHtml(a.name)}</th>
                <th>#${b.rank} ${escHtml(b.name)}</th>
                <th>ส่วนต่าง (A − B)</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>คะแนนความเปราะบาง (0–100)</td>
                <td><b>${a.score}</b></td>
                <td><b>${b.score}</b></td>
                <td><b>${diffStr(a.score, b.score, 0, " คะแนน")}</b></td>
              </tr>
              <tr>
                <td>อุณหภูมิพื้นผิวเฉลี่ย LST (300 ม.)</td>
                <td>${fmtNum(a.lst300, 2)} °C</td>
                <td>${fmtNum(b.lst300, 2)} °C</td>
                <td>${diffStr(a.lst300, b.lst300, 2, " °C")}</td>
              </tr>
              <tr>
                <td>อุณหภูมิพีค p90 (300 ม.)</td>
                <td>${fmtNum(a.lstP90_300, 2)} °C</td>
                <td>${fmtNum(b.lstP90_300, 2)} °C</td>
                <td>${diffStr(a.lstP90_300, b.lstP90_300, 2, " °C")}</td>
              </tr>
              <tr>
                <td>ความหนาแน่นอาคาร (300 ม.)</td>
                <td>${fmtNum(a.builtup300, 1)} %</td>
                <td>${fmtNum(b.builtup300, 1)} %</td>
                <td>${diffStr(a.builtup300, b.builtup300, 1, "%")}</td>
              </tr>
              <tr>
                <td>สัดส่วนพื้นที่สีเขียว (300 ม.)</td>
                <td>${aGreen != null ? aGreen.toFixed(1) + "%" : "—"}</td>
                <td>${bGreen != null ? bGreen.toFixed(1) + "%" : "—"}</td>
                <td>${diffStr(aGreen, bGreen, 1, "%")}</td>
              </tr>
              <tr>
                <td>ผลจำลอง +${this.selectedTrees} ต้น (คาด LST ลดกลาง)</td>
                <td>-${simA.lstDropMid.toFixed(3)} °C (สีเขียว ${simA.simulatedGreenPct.toFixed(2)}%)</td>
                <td>-${simB.lstDropMid.toFixed(3)} °C (สีเขียว ${simB.simulatedGreenPct.toFixed(2)}%)</td>
                <td>${diffStr(-simA.lstDropMid, -simB.lstDropMid, 3, " °C")}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="chart" id="cs341-c-compare"></div>
        <p class="note" style="margin-top:6px;">⚠️ หมายเหตุ: คะแนนความเปราะบางและผลลด LST เป็นการประเมินเชิงดัชนีและค่าสมมติจากข้อมูลชั้นพื้นที่ ยังไม่ได้ลงสำรวจหน้างานจริง</p>
      </div>
    `;

    const selA = document.getElementById("cmp-sel-a") as HTMLSelectElement | null;
    const selB = document.getElementById("cmp-sel-b") as HTMLSelectElement | null;
    if (selA) {
      selA.onchange = () => {
        this.selectedId = selA.value;
        this.renderSimulationDashboard();
        this.renderCompareDashboard();
      };
    }
    if (selB) {
      selB.onchange = () => {
        this.compareId = selB.value;
        this.renderCompareDashboard();
      };
    }

    cmpPanelEl.querySelectorAll<HTMLButtonElement>("[data-jump-comm]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.jumpComm;
        if (id && this.onJumpToMap) this.onJumpToMap(id);
      };
    });

    const cmpChartEl = document.getElementById("cs341-c-compare");
    if (cmpChartEl) {
      this.compareChart = this.ensureChart(this.compareChart, cmpChartEl);

      this.compareChart.setOption({
        tooltip: { trigger: "axis" },
        legend: { top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 42, right: 16, top: 30, bottom: 26 },
        xAxis: { type: "category", data: ["คะแนนเปราะบาง", "LST 300ม. (°C)", "อาคาร (%)", "พื้นที่สีเขียว (%)"] },
        yAxis: { type: "value" },
        series: [
          {
            name: `#${a.rank} ${a.name}`,
            type: "bar",
            data: [a.score, Number(a.lst300 ?? 0), Number(a.builtup300 ?? 0), Number(aGreen ?? 0)],
            itemStyle: { color: "#dc2626", borderRadius: [4, 4, 0, 0] },
          },
          {
            name: `#${b.rank} ${b.name}`,
            type: "bar",
            data: [b.score, Number(b.lst300 ?? 0), Number(b.builtup300 ?? 0), Number(bGreen ?? 0)],
            itemStyle: { color: "#0284c7", borderRadius: [4, 4, 0, 0] },
          },
        ],
      });
    }
  }

  private renderRowsAndCharts() {
    const tbody = document.getElementById("cs341-ranking-body");
    const distSel = document.getElementById("cs341-filter-district") as HTMLSelectElement | null;
    const tierSel = document.getElementById("cs341-filter-tier") as HTMLSelectElement | null;
    if (!tbody) return;

    const dFilter = distSel?.value || "all";
    const tFilter = tierSel?.value || "all";

    const filtered = this.items.filter((x) => {
      if (dFilter !== "all" && x.district !== dFilter) return false;
      if (tFilter !== "all" && x.tier !== tFilter) return false;
      return true;
    });

    tbody.innerHTML = filtered
      .map((c) => {
        const pillClass = c.tier === "high" ? "danger" : c.tier === "moderate" ? "warn" : "cool";
        const sim100 = simulateTreeAddition(c, 100);
        const diag = diagnoseCommunity(c, sim100);
        return `<tr>
          <td><b>#${c.rank}</b> <span class="note">(${c.score}/100)</span></td>
          <td><b>${escHtml(c.name)}</b>${c.dataComplete ? "" : ` <span class="v-pill warn">ข้อมูลไม่ครบ</span>`}<br><span class="note">เขต${escHtml(c.district)} · ${escHtml(c.id)}</span><br><span class="note">${escHtml(diag.headline)}</span></td>
          <td><span class="v-pill ${pillClass}">${escHtml(c.rawRiskLevel || c.tier)}</span><br><span class="note">สีวงกลม = ความเปราะบาง</span></td>
          <td><b>${fmtNum(c.lst300, 2)} °C</b><br><span class="note">p90: ${fmtNum(c.lstP90_300, 2)} °C</span></td>
          <td>${fmtNum(c.builtup300, 1)} %</td>
          <td>${c.greenRatio300 != null ? fmtNum(c.greenRatio300 * 100, 1) + " %" : "—"}</td>
          <td>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button type="button" class="mini-btn" data-jump-comm="${escHtml(c.id)}">🗺️ ดูบนแผนที่</button>
              <button type="button" class="mini-btn" data-sim-comm="${escHtml(c.id)}">🧪 จำลองต้นไม้</button>
              <button type="button" class="mini-btn" data-cmp-comm="${escHtml(c.id)}">⚖️ เปรียบเทียบ</button>
            </div>
            <div class="note" style="margin-top:4px;">ค่าสมมติ +100 ต้น: สีเขียว +${sim100.deltaGreenPct.toFixed(2)}% · คาด LST -${sim100.lstDropLow.toFixed(3)} ถึง -${sim100.lstDropHigh.toFixed(3)} °C</div>
          </td>
        </tr>`;
      })
      .join("");

    document.querySelectorAll<HTMLButtonElement>("[data-jump-comm]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.jumpComm;
        if (id && this.onJumpToMap) this.onJumpToMap(id);
      };
    });

    document.querySelectorAll<HTMLButtonElement>("[data-sim-comm]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.simComm;
        if (id) {
          this.selectedId = id;
          this.renderSimulationDashboard();
          document.getElementById("cs341-sim-dashboard")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      };
    });

    document.querySelectorAll<HTMLButtonElement>("[data-cmp-comm]").forEach((btn) => {
      btn.onclick = () => {
        const id = btn.dataset.cmpComm;
        if (id) this.openCompareWith(id);
      };
    });

    const barEl = document.getElementById("cs341-c-bar");
    if (barEl) {
      this.barChart = this.ensureChart(this.barChart, barEl);
      const top10 = this.items.slice(0, 10).reverse();
      this.barChart.setOption({
        tooltip: { trigger: "axis" },
        grid: { left: 120, right: 20, top: 16, bottom: 24 },
        xAxis: { type: "value", max: 100 },
        yAxis: { type: "category", data: top10.map((x) => x.name), axisLabel: { fontSize: 11 } },
        series: [{ type: "bar", data: top10.map((x) => x.score), itemStyle: { color: "#dc2626", borderRadius: [0, 4, 4, 0] } }],
      });
    }

    const scEl = document.getElementById("cs341-c-scatter");
    if (scEl) {
      this.scatterChart = this.ensureChart(this.scatterChart, scEl);
      this.scatterChart.setOption({
        tooltip: {
          trigger: "item",
          formatter: (p: { data?: [number, number, number, string, number] }) => {
            const d = p.data;
            if (!d) return "";
            return `<b>${escHtml(d[3])}</b><br>คะแนนเปราะบาง: ${d[4]}/100<br>อาคาร: ${d[0].toFixed(1)}% · LST: ${d[1].toFixed(2)} °C<br>ขาดแคลนสีเขียว: ${d[2].toFixed(0)}%`;
          },
        },
        grid: { left: 45, right: 18, top: 20, bottom: 32 },
        xAxis: { name: "อาคาร (%)", nameLocation: "middle", nameGap: 20, type: "value" },
        yAxis: { name: "LST (°C)", type: "value", scale: true },
        series: [
          {
            type: "scatter",
            symbolSize: (val: number[]) => clamp((val[2] || 20) * 0.25 + 8, 8, 28),
            data: this.items
              .filter((x) => x.builtup300 != null && x.lst300 != null)
              .map((x) => [
                x.builtup300!,
                x.lst300!,
                x.greenRatio300 != null ? (1 - clamp(x.greenRatio300 / 0.5, 0, 1)) * 100 : 50,
                x.name,
                x.score,
              ]),
            itemStyle: { color: "#f97316", opacity: 0.8 },
          },
        ],
      });
    }
  }

  resize() {
    this.barChart?.resize();
    this.scatterChart?.resize();
    this.compareChart?.resize();
  }

  /** เรียกเมื่อออกจากหน้า/ถอด component (SPA) เพื่อคืนหน่วยความจำของ ECharts ทั้ง 3 ตัว */
  dispose() {
    this.barChart?.dispose();
    this.scatterChart?.dispose();
    this.compareChart?.dispose();
    this.barChart = null;
    this.scatterChart = null;
    this.compareChart = null;
    this.items = [];
    this.selectedId = null;
    this.compareId = null;
  }
}