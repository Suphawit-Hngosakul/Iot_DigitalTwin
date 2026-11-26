import * as echarts from "echarts";

export type RiskTier = "high" | "moderate" | "low";

export interface CommunityPriorityResult {
    id: string;
    name: string;
    district: string;
    rank: number;
    total: number;
    score: number;
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
    heatExcessC: number;
    peakSpreadC: number;
    builtupFactor: number;
    lstDropLow: number;
    lstDropHigh: number;
    lstDropMid: number;
    simulatedLstMid: number | null;
}

const CANOPY_SQM_PER_TREE = 25; // เรือนยอดไม้ยืนต้นเขตเมืองโตเต็มวัย (~รัศมี 2.8 ม. ≈ 25 ตร.ม./ต้น)
const RADIUS_300_AREA_SQM = Math.round(Math.PI * 300 * 300); // 282,743 ตร.ม.
const BASELINE_COOL_LST = 35.0; // อุณหภูมิพื้นผิวอ้างอิงโซนระบายความร้อนดี (°C)

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

export function simulateTreeAddition(
    c: Pick<
        CommunityPriorityResult,
        "lst300" | "lstP90_300" | "builtup300" | "greenRatio300" | "ndvi300" | "score"
    >,
    trees: number
): TreeSimulationResult {
    const safeTrees = Math.max(0, Math.round(trees));
    const addedCanopySqm = safeTrees * CANOPY_SQM_PER_TREE;

    const builtupFrac = clamp((c.builtup300 ?? 30) / 100, 0, 0.85);
    const baseGreenFrac = clamp(c.greenRatio300 ?? 0.2, 0, 0.9);

    const builtupSqm = Math.round(RADIUS_300_AREA_SQM * builtupFrac);
    const existingGreenSqm = Math.round(RADIUS_300_AREA_SQM * baseGreenFrac);

    // พื้นที่เปิดโล่งที่ปลูกแทรกได้จริง = พื้นที่ทั้งหมด - พื้นที่อาคาร - พื้นที่สีเขียวเดิม (ขั้นต่ำ 10% ของบัฟเฟอร์)
    const openFrac = clamp(1 - builtupFrac - baseGreenFrac, 0.1, 0.95);
    const availableOpenSqm = Math.round(RADIUS_300_AREA_SQM * openFrac);
    const availableOpenPct = openFrac * 100;

    // สัดส่วนพื้นที่เปิดโล่งของชุมชนที่ถูกเติมด้วยเรือนยอดไม้ใหม่
    const openAreaFillPct = clamp((addedCanopySqm / Math.max(availableOpenSqm, 1)) * 100, 0, 100);

    // พื้นที่สีเขียวใหม่เทียบกับรัศมี 300 ม. ทั้งหมด
    const addedGreenFrac = clamp(addedCanopySqm / RADIUS_300_AREA_SQM, 0, openFrac);
    const simGreenFrac = clamp(baseGreenFrac + addedGreenFrac, 0, 0.98);

    const currentGreenPct = baseGreenFrac * 100;
    const simulatedGreenPct = simGreenFrac * 100;
    const deltaGreenPct = simulatedGreenPct - currentGreenPct;

    // NDVI ปรับเพิ่มตามสัดส่วนเรือนยอดเทียบกับพื้นที่เปิดโล่งและช่องว่าง NDVI ที่เหลือ (1 - NDVI_current)
    const baseNdvi = clamp(c.ndvi300 ?? 0.25, -0.2, 0.9);
    const ndviHeadroom = clamp(0.85 - baseNdvi, 0.1, 0.9);
    const deltaNdvi = clamp((openAreaFillPct / 100) * ndviHeadroom * 0.65, 0, 0.35);
    const simulatedNdvi = clamp(baseNdvi + deltaNdvi, -0.2, 0.95);

    // ตัวคูณความไวต่อความร้อนจากตัวแปรจริงของชุมชนนั้น
    const lstVal = c.lst300 ?? 41.0;
    const lstP90Val = c.lstP90_300 ?? lstVal + 1.5;
    const heatExcessC = Math.max(0, lstVal - BASELINE_COOL_LST);
    const peakSpreadC = Math.max(0, lstP90Val - lstVal);
    const builtupFactor = 1 + builtupFrac * 0.8;

    // สเกลความเย็นเชิงสมมติ (อิงความรุนแรงความร้อน × ความหนาแน่นอาคาร × สัดส่วนพื้นที่เปิดโล่งที่ถูกเติมร่มเงา)
    const coolingPotential =
        (openAreaFillPct / 100) * (heatExcessC * 0.22 + peakSpreadC * 0.15) * builtupFactor;

    const lstDropLow = Number(clamp(coolingPotential * 0.7, 0.02, 1.5).toFixed(2));
    const lstDropHigh = Number(clamp(coolingPotential * 1.35, 0.04, 2.5).toFixed(2));
    const lstDropMid = Number(((lstDropLow + lstDropHigh) / 2).toFixed(2));
    const simulatedLstMid =
        c.lst300 != null ? Number((c.lst300 - lstDropMid).toFixed(2)) : null;

    // คะแนนความเปราะบางใหม่คำนวณซ้ำจากสมการดัชนีเดียวกัน (40% LST + 30% อาคาร + 30% ขาดแคลนสีเขียว)
    const newLst = simulatedLstMid ?? lstVal;
    const newHeatNorm = clamp(((newLst - 34) / 14) * 100, 0, 100);
    const bldgNorm = clamp(((c.builtup300 ?? 30) / 60) * 100, 0, 100);
    const newGreenDeficitNorm = clamp((1 - clamp(simGreenFrac / 0.5, 0, 1)) * 100, 0, 100);
    const rawNewScore = Math.round(newHeatNorm * 0.4 + bldgNorm * 0.3 + newGreenDeficitNorm * 0.3);

    const currentScore = c.score;
    const simulatedScore = Math.min(currentScore, clamp(rawNewScore, 0, 100));
    const deltaScore = simulatedScore - currentScore;

    return {
        trees: safeTrees,
        canopyPerTreeSqm: CANOPY_SQM_PER_TREE,
        addedCanopySqm,
        totalBufferSqm: RADIUS_300_AREA_SQM,
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
        heatExcessC,
        peakSpreadC,
        builtupFactor,
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
        const id = String(p.community_id || `COMM-${idx + 1}`);
        const name = String(p.name || id);
        const district = String(p.district || "—");
        const rawRiskLevel = String(p.risk_level || "").toLowerCase();

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

        const heatNorm = lst300 != null ? clamp(((lst300 - 34) / 14) * 100, 0, 100) : 50;
        const bldgNorm = builtup300 != null ? clamp((builtup300 / 60) * 100, 0, 100) : 45;
        const greenDeficitNorm =
            greenRatio300 != null ? clamp((1 - clamp(greenRatio300 / 0.5, 0, 1)) * 100, 0, 100) : 50;

        let score = Math.round(heatNorm * 0.4 + bldgNorm * 0.3 + greenDeficitNorm * 0.3);
        if (rawRiskLevel === "high") score = Math.max(score, 68);
        if (rawRiskLevel === "low") score = Math.min(score, 54);
        score = clamp(score, 0, 100);

        let tier: RiskTier = "moderate";
        let tierLabel = "ความเปราะบางปานกลาง (ควรเฝ้าระวัง)";
        let theme: "red" | "amber" | "teal" = "amber";
        if (score >= 65 || rawRiskLevel === "high") {
            tier = "high";
            tierLabel = "ความเปราะบางสูง (ควรพิจารณาก่อน)";
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
            "หมายเหตุ: การจำลองนี้คำนวณจากตัวแปรจริงของชุมชนนี้ (พื้นที่อาคาร, พื้นที่สีเขียวเดิม, LST และ p90) เพื่อเปรียบเทียบสมมติฐาน ยังไม่ใช่ผลตรวจวัดจริงหน้างาน";

        return {
            id,
            name,
            district,
            rank: 0,
            total: features.length,
            score,
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
            primaryDriver,
            simulationNote,
        };
    });

    parsed.sort((a, b) => b.score - a.score);
    parsed.forEach((item, i) => {
        item.rank = i + 1;
        item.total = parsed.length;
    });

    return parsed;
}

export function buildCommunitySimulationHtml(
    c: CommunityPriorityResult,
    defaultTrees = 30
): string {
    const treeChoices = [10, 20, 30, 40, 50];
    const sim = simulateTreeAddition(c, defaultTrees);
    const badgeClass = c.tier === "high" ? "danger" : c.tier === "moderate" ? "warn" : "cool";

    const buttonsHtml = treeChoices
        .map(
            (n) =>
                `<button type="button" class="mini-btn ${n === defaultTrees ? "active" : ""}" data-sim-trees="${n}">+${n} ต้น</button>`
        )
        .join(" ");

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
              <div class="lbl">สถานะในชั้นข้อมูล</div>
              <div class="val" style="font-size:14px;">${escHtml(c.rawRiskLevel || "—")}</div>
              <div class="sub">สีวงกลม = ความเปราะบาง ไม่ใช่ °C</div>
            </div>
          </div>
          <span class="v-pill ${badgeClass}">${escHtml(c.tierLabel)}</span>
        </div>
      </div>
      <p class="note" style="margin-top:8px;"><b>${escHtml(c.primaryDriver)}</b></p>
      <p class="note" style="margin-top:4px;">${escHtml(c.simulationNote)}</p>
    </div>

    <div class="d-card" id="comm-sim-box">
      <h4>🧪 จำลองสมมติฐานการเพิ่มต้นไม้ (อิงตัวแปรจริงของชุมชนนี้)</h4>
      <p class="note">เลือกจำนวนต้นไม้สมมติ (เรือนยอดเฉลี่ย ~${CANOPY_SQM_PER_TREE} ตร.ม./ต้น) เพื่อคำนวณเทียบกับพื้นที่เปิดโล่งจริงของชุมชน:</p>
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
    c: Pick<CommunityPriorityResult, "name" | "lst300" | "lstP90_300" | "builtup300" | "greenRatio300" | "ndvi300">,
    s: TreeSimulationResult
): string {
    return `
    <div class="d-kpis" style="grid-template-columns: 1fr 1fr; margin-bottom:8px;">
      <div class="d-kpi cool">
        <div class="lbl">พื้นที่สีเขียวใหม่ (+${s.trees} ต้น)</div>
        <div class="val">${s.simulatedGreenPct.toFixed(2)} %</div>
        <div class="sub">เดิม ${s.currentGreenPct.toFixed(2)}% (+${s.deltaGreenPct.toFixed(2)}% · เติมพื้นที่เปิดโล่ง ${s.openAreaFillPct.toFixed(2)}%)</div>
      </div>
      <div class="d-kpi warm">
        <div class="lbl">ช่วง LST ที่คาดว่าจะลด (ค่าสมมติ)</div>
        <div class="val">-${s.lstDropLow.toFixed(2)} ถึง -${s.lstDropHigh.toFixed(2)} °C</div>
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
          <td><b>${s.simulatedLstMid != null ? s.simulatedLstMid.toFixed(2) + " °C" : "—"}</b> (ช่วง -${s.lstDropLow} ถึง -${s.lstDropHigh} °C)</td>
        </tr>
        <tr>
          <td>คะแนนความเปราะบางรวม</td>
          <td>${s.currentScore} / 100</td>
          <td><b>${s.simulatedScore} / 100</b> (${s.deltaScore <= 0 ? s.deltaScore : "+" + s.deltaScore} คะแนน)</td>
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
          <div class="g-head"><span class="g-tag">ขั้นที่ 1</span><strong>คำนวณพื้นที่เปิดโล่งที่ปลูกแทรกได้จริง (A_open)</strong></div>
          <p>
            พื้นที่บัฟเฟอร์ 300 ม. = <b>${s.totalBufferSqm.toLocaleString()} ตร.ม.</b><br>
            หักพื้นที่อาคาร (<code>builtup_pct_300</code> = ${fmtNum(c.builtup300, 1)}%) = <b>${s.builtupSqm.toLocaleString()} ตร.ม.</b><br>
            หักพื้นที่สีเขียวเดิม (<code>green_ratio_300</code> = ${s.currentGreenPct.toFixed(1)}%) = <b>${s.existingGreenSqm.toLocaleString()} ตร.ม.</b><br>
            → เหลือพื้นที่เปิดโล่ง/ดาดแข็งที่ปลูกแทรกได้ <b>${s.availableOpenSqm.toLocaleString()} ตร.ม.</b> (${s.availableOpenPct.toFixed(1)}%)
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
          <div class="g-head"><span class="g-tag">ขั้นที่ 3</span><strong>ตัวคูณความไวต่อความร้อนจากข้อมูลจริงของชุมชน</strong></div>
          <p>
            ส่วนต่างความร้อนเหนือฐาน 35 °C (<code>lst_mean_300</code> = ${fmtNum(c.lst300, 2)} °C) = <b>+${s.heatExcessC.toFixed(2)} °C</b><br>
            ความรุนแรงช่วงพีค (<code>lst_p90_300 - lst_mean_300</code>) = <b>+${s.peakSpreadC.toFixed(2)} °C</b><br>
            ตัวคูณความแออัดอาคาร (<code>1 + 0.8 × builtup</code>) = <b>${s.builtupFactor.toFixed(2)}x</b><br>
            → เมื่อนำมาถ่วงน้ำหนักกับสัดส่วนร่มเงาใหม่ จึงประเมินช่วงการลดลงของ LST ได้ <b>-${s.lstDropLow.toFixed(2)} ถึง -${s.lstDropHigh.toFixed(2)} °C</b>
          </p>
        </div>
        <div class="gloss-item">
          <div class="g-head"><span class="g-tag">ขั้นที่ 4</span><strong>คำนวณคะแนนความเปราะบางใหม่ (Vulnerability Re-scoring)</strong></div>
          <p>
            ใช้สมการถ่วงน้ำหนัก 3 มิติ (ความร้อน 40% + ความหนาแน่นอาคาร 30% + การขาดแคลนสีเขียว 30%) แทนค่า <code>LST</code> และ <code>green_ratio</code> ใหม่หลังจำลอง ได้คะแนน <b>${s.simulatedScore}/100</b> (เปลี่ยนแปลง <b>${s.deltaScore}</b> คะแนน)
          </p>
        </div>
      </div>
    </details>
    <p class="note" style="margin-top:6px;">⚠️ <b>ข้อจำกัดของแบบจำลอง:</b> ตัวเลขทั้งหมดคำนวณจากตัวแปรเชิงพื้นที่ของชุมชนนี้เพื่อใช้เปรียบเทียบสมมติฐานเท่านั้น ยังไม่รวมปัจจัยทิศทางลม ความสูงตึกบังแดด และการสำรวจพื้นที่ว่างจริงหน้างาน</p>
  `;
}

export function bindCommunitySimulationEvents(
    container: HTMLElement | null,
    c: CommunityPriorityResult
): void {
    if (!container) return;
    const btns = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-sim-trees]"));
    const resultEl = container.querySelector<HTMLElement>("#comm-sim-result");
    if (!resultEl) return;

    btns.forEach((btn) => {
        btn.onclick = () => {
            const trees = Number(btn.dataset.simTrees || 30);
            btns.forEach((b) => b.classList.toggle("active", b === btn));
            resultEl.innerHTML = renderSimulationSummary(c, simulateTreeAddition(c, trees));
        };
    });
}

export class CommunityStatsController {
    private items: CommunityPriorityResult[] = [];
    private selectedId: string | null = null;
    private selectedTrees = 30;
    private barChart: echarts.ECharts | null = null;
    private scatterChart: echarts.ECharts | null = null;
    private onJumpToMap?: (communityId: string) => void;

    setJumpHandler(handler: (communityId: string) => void) {
        this.onJumpToMap = handler;
    }

    setCommunities(features: { properties?: Record<string, unknown> | null }[]) {
        this.items = evaluateAllCommunities(features);
        if (!this.selectedId && this.items.length > 0) {
            this.selectedId = this.items[0].id;
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

        if (!kpisEl || !tbody) return;

        if (!this.items.length) {
            kpisEl.innerHTML = `<div class="card"><p class="note">กำลังรอข้อมูลชุมชนจากระบบ (<code>/community</code>)…</p></div>`;
            tbody.innerHTML = "";
            if (spotlightEl) spotlightEl.innerHTML = "";
            if (simPanelEl) simPanelEl.innerHTML = "";
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
        const top1 = this.items[0];

        kpisEl.innerHTML = `
      <div class="kpi"><div class="l">ชุมชนทั้งหมด</div><div class="v">${this.items.length}</div><div class="l">จัดอันดับตามคะแนนความเปราะบาง</div></div>
      <div class="kpi"><div class="l">กลุ่มเปราะบางสูง</div><div class="v">${highCount}</div><div class="l">สีวงกลม = ความเปราะบาง (ไม่ใช่ °C)</div></div>
      <div class="kpi"><div class="l">คะแนนความเปราะบางเฉลี่ย</div><div class="v">${avgScore}</div><div class="l">จาก 100 คะแนน (ดัชนี 3 มิติ)</div></div>
      <div class="kpi"><div class="l">อันดับ #1 ปัจจุบัน</div><div class="v" style="font-size:15px;">${escHtml(top1.name)}</div><div class="l">คะแนน ${top1.score}/100 · LST ${fmtNum(top1.lst300, 1)} °C</div></div>
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
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button type="button" class="mini-btn" data-jump-comm="${escHtml(c.id)}">🗺️ ไปที่ชุมชนบนแผนที่</button>
              <button type="button" class="mini-btn" data-sim-comm="${escHtml(c.id)}">🧪 จำลองเพิ่มต้นไม้</button>
            </div>
          </div>`
                )
                .join("");
        }

        this.renderRowsAndCharts();
        this.renderSimulationDashboard();
    }

    private renderSimulationDashboard() {
        const simPanelEl = document.getElementById("cs341-sim-dashboard");
        if (!simPanelEl) return;

        const target = this.items.find((x) => x.id === this.selectedId) || this.items[0];
        if (!target) return;

        const sim = simulateTreeAddition(target, this.selectedTrees);
        const choices = [10, 20, 30, 40, 50];

        simPanelEl.innerHTML = `
      <div class="card" style="margin-bottom:12px;">
        <h3>
          <span>🧪 Dashboard เปรียบเทียบ: ปัจจุบัน vs ค่าสมมติการเพิ่มต้นไม้ (${escHtml(target.name)})</span>
          <button type="button" class="mini-btn" data-jump-comm="${escHtml(target.id)}">🗺️ เปิดตำแหน่งบนแผนที่</button>
        </h3>
        <p class="note">
          <b>หลักการจำลอง:</b> คำนวณจากตัวแปรจริงของ <b>${escHtml(target.name)}</b> (<code>lst_mean_300</code> = ${fmtNum(target.lst300, 2)} °C, <code>lst_p90_300</code> = ${fmtNum(target.lstP90_300, 2)} °C, <code>builtup_pct_300</code> = ${fmtNum(target.builtup300, 1)}%, <code>green_ratio_300</code> = ${target.greenRatio300 != null ? fmtNum(target.greenRatio300 * 100, 1) + "%" : "—"}) โดยหักพื้นที่อาคารและสีเขียวเดิมออกเพื่อหาพื้นที่เปิดโล่งที่ปลูกแทรกได้จริง (<b>${sim.availableOpenSqm.toLocaleString()} ตร.ม.</b>)
        </p>
        <div style="display:flex; gap:8px; flex-wrap:wrap; margin:10px 0;">
          ${choices
                .map(
                    (n) =>
                        `<button type="button" class="mini-btn ${n === this.selectedTrees ? "active" : ""}" data-dash-trees="${n}">ปลูกสมมติ ${n} ต้น</button>`
                )
                .join("")}
        </div>
        ${renderSimulationSummary(target, sim)}
      </div>
    `;

        simPanelEl.querySelectorAll<HTMLButtonElement>("[data-dash-trees]").forEach((btn) => {
            btn.onclick = () => {
                this.selectedTrees = Number(btn.dataset.dashTrees || 30);
                this.renderSimulationDashboard();
            };
        });

        simPanelEl.querySelectorAll<HTMLButtonElement>("[data-jump-comm]").forEach((btn) => {
            btn.onclick = () => {
                const id = btn.dataset.jumpComm;
                if (id && this.onJumpToMap) this.onJumpToMap(id);
            };
        });
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
                const sim50 = simulateTreeAddition(c, 50);
                return `<tr>
          <td><b>#${c.rank}</b> <span class="note">(${c.score}/100)</span></td>
          <td><b>${escHtml(c.name)}</b><br><span class="note">เขต${escHtml(c.district)} · ${escHtml(c.id)}</span></td>
          <td><span class="v-pill ${pillClass}">${escHtml(c.rawRiskLevel || c.tier)}</span><br><span class="note">สีวงกลม = ความเปราะบาง</span></td>
          <td><b>${fmtNum(c.lst300, 2)} °C</b><br><span class="note">p90: ${fmtNum(c.lstP90_300, 2)} °C</span></td>
          <td>${fmtNum(c.builtup300, 1)} %</td>
          <td>${c.greenRatio300 != null ? fmtNum(c.greenRatio300 * 100, 1) + " %" : "—"}</td>
          <td>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button type="button" class="mini-btn" data-jump-comm="${escHtml(c.id)}">🗺️ ดูบนแผนที่</button>
              <button type="button" class="mini-btn" data-sim-comm="${escHtml(c.id)}">🧪 จำลองต้นไม้</button>
            </div>
            <div class="note" style="margin-top:4px;">สมมติ +50 ต้น: สีเขียว +${sim50.deltaGreenPct.toFixed(2)}% · คาด LST -${sim50.lstDropLow} ถึง -${sim50.lstDropHigh} °C</div>
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

        const barEl = document.getElementById("cs341-c-bar");
        if (barEl) {
            if (!this.barChart) this.barChart = echarts.init(barEl);
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
            if (!this.scatterChart) this.scatterChart = echarts.init(scEl);
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
    }
}