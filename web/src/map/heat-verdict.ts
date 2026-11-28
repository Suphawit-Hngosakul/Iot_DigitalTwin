/** ข้อความประเมินความร้อน (LST / อากาศ IoT) และคำอธิบายตัวแปร ที่แสดงใน drawer ด้านขวาของแผนที่ */

const GLOSSARY_ITEMS: { code: string; name: string; desc: string }[] = [
  {
    code: "lst_mean",
    name: "อุณหภูมิพื้นผิวเฉลี่ย (Land Surface Temperature)",
    desc: "ค่าเฉลี่ยอุณหภูมิพื้นผิวโลก (°C) จากภาพถ่ายดาวเทียม Landsat 8/9 สะท้อนความร้อนสะสมของหลังคาอาคาร ถนน และพื้นดิน",
  },
  {
    code: "lst_p90",
    name: "อุณหภูมิพื้นผิวสูงสุด 90th Percentile",
    desc: "ค่าอุณหภูมิพื้นผิวที่เปอร์เซ็นไทล์ที่ 90 (°C) ใช้ดูระดับความร้อนจัดในช่วงพีคของพื้นที่",
  },
  {
    code: "air_temp_mean",
    name: "อุณหภูมิอากาศจริงระดับคนเดินเท้า (IoT)",
    desc: "ค่าเฉลี่ยอุณหภูมิอากาศ (°C) ที่ตรวจวัดจากอุปกรณ์ IoT ระดับความสูง ~1.5 เมตร ซึ่งเป็นอุณหภูมิที่คนเดินเท้าสัมผัสจริง",
  },
  {
    code: "ndvi_mean",
    name: "ดัชนีพืชพรรณ (NDVI: -1 ถึง +1)",
    desc: "วัดความหนาแน่นของต้นไม้และพื้นที่สีเขียว ยิ่งค่าสูง (เข้าใกล้ +1) แปลว่ามีต้นไม้มาก ช่วยคายความชื้นลดความร้อนได้ดี",
  },
  {
    code: "ndbi_mean",
    name: "ดัชนีสิ่งปลูกสร้าง (NDBI: -1 ถึง +1)",
    desc: "วัดสัดส่วนสิ่งปลูกสร้างและพื้นผิวคอนกรีต ยิ่งค่าสูง (เป็นบวก) แปลว่ามีอาคารและพื้นผิวแข็งที่ดูดซับความร้อนหนาแน่น",
  },
  {
    code: "green_ratio",
    name: "สัดส่วนพื้นที่สีเขียว (%)",
    desc: "ร้อยละของพื้นที่ปกคลุมด้วยพืชพรรณและเรือนยอดไม้เทียบกับพื้นที่ทั้งหมดในรัศมีที่ศึกษา",
  },
  {
    code: "builtup_pct",
    name: "ความหนาแน่นพื้นที่อาคาร (Building Coverage Ratio)",
    desc: "ร้อยละของพื้นที่ฐานอาคารทั้งหมดเทียบกับพื้นที่บล็อก 90×90 ม. (8,100 ตร.ม.) สะท้อนการบังลมและการสะสมความร้อน",
  },
  {
    code: "building_count",
    name: "จำนวนอาคาร (หลัง)",
    desc: "จำนวนหลังคาเรือนและสิ่งปลูกสร้างทั้งหมดจากชั้นข้อมูล building.shp ที่อยู่ในขอบเขตพื้นที่ศึกษา",
  },
  {
    code: "floors / floors_mean",
    name: "จำนวนชั้นอาคาร / จำนวนชั้นเฉลี่ย",
    desc: "ความสูงในหน่วยชั้นของอาคารรายหลัง หรือค่าเฉลี่ยจำนวนชั้นของกลุ่มอาคารภายในบล็อก",
  },
  {
    code: "height_m / area_sqm",
    name: "ความสูงอาคาร (ม.) และพื้นที่ฐาน (ตร.ม.)",
    desc: "ความสูงโดยประมาณจากพื้นถึงหลังคา (เมตร) และขนาดพื้นที่หน้าตัดฐานของตัวอาคาร (ตารางเมตร)",
  },
  {
    code: "far",
    name: "อัตราส่วนพื้นที่อาคารรวมต่อพื้นที่ดิน (FAR)",
    desc: "Floor Area Ratio คำนวณจากผลรวมพื้นที่ใช้สอยทุกชั้นหารด้วยพื้นที่ดิน สะท้อนความหนาแน่นแนวตั้งและมวลความร้อน (Thermal Mass)",
  },
  {
    code: "db_spl_mean",
    name: "ระดับความดังเสียงเฉลี่ย (dB SPL)",
    desc: "ค่าเฉลี่ยระดับความดังเสียงสภาพแวดล้อมจากไมโครโฟน IoT สะท้อนความหนาแน่นของการจราจรและกิจกรรมเมือง",
  },
  {
    code: "n_iot_minutes",
    name: "จำนวนนาทีที่มีข้อมูล IoT",
    desc: "จำนวนตัวอย่างข้อมูลรายนาทีที่บันทึกได้จริงภายในบล็อกกริดนั้น (ใช้กรองความน่าเชื่อถือของค่าอากาศเฉลี่ย)",
  },
];

export function buildGlossaryHtml(): string {
  const rows = GLOSSARY_ITEMS.map(
    (g) => `<div class="gloss-item">
      <div class="g-head"><span class="g-tag">${g.code}</span><strong>${g.name}</strong></div>
      <p>${g.desc}</p>
    </div>`,
  ).join("");

  return `<details class="glossary-card">
    <summary>
      <span>📖 คำอธิบายตัวแปรและตัวชี้วัดคืออะไร?</span>
      <span class="gloss-hint">กดเพื่อดูความหมาย</span>
    </summary>
    <div class="glossary-list">${rows}</div>
  </details>`;
}

interface HeatStatus {
  level: "cool" | "moderate" | "warn" | "danger" | "none";
  badge: string;
  desc: string;
}

function evalSurfaceLst(lst: unknown): HeatStatus {
  const v = Number(lst);
  if (lst == null || Number.isNaN(v)) {
    return { level: "none", badge: "ไม่มีข้อมูล LST", desc: "ยังไม่มีค่าอุณหภูมิพื้นผิวจากดาวเทียมในบล็อกนี้" };
  }
  if (v >= 44) {
    return {
      level: "danger",
      badge: "ร้อนจัดวิกฤต (Extreme Hotspot)",
      desc: `พื้นผิวร้อนจัด ${v.toFixed(2)} °C (สูงกว่าโซนสีเขียว >10 °C) สะสมความร้อนรุนแรง`,
    };
  }
  if (v >= 40) {
    return {
      level: "warn",
      badge: "ร้อนสะสมสูง (UHI Hotspot)",
      desc: `พื้นผิวหลังคา/คอนกรีต ${v.toFixed(2)} °C สูงกว่าโซนสีเขียวทั่วไป ~6–9 °C`,
    };
  }
  if (v >= 35) {
    return {
      level: "moderate",
      badge: "สะสมความร้อนปานกลาง",
      desc: `อุณหภูมิพื้นผิว ${v.toFixed(2)} °C เริ่มมีการสะสมความร้อนจากสิ่งปลูกสร้างและพื้นผิวดาดแข็ง`,
    };
  }
  return {
    level: "cool",
    badge: "โซนเย็น / ระบายความร้อนดี",
    desc: `อุณหภูมิพื้นผิว ${v.toFixed(2)} °C อยู่ในเกณฑ์เย็นสบาย`,
  };
}

function evalAirTemp(air: unknown): HeatStatus {
  const v = Number(air);
  if (air == null || Number.isNaN(v)) {
    return { level: "none", badge: "ไม่มีข้อมูลอากาศ IoT", desc: "จุดนี้ยังไม่มีเส้นทางเดินวัดหรือสถานี IoT ในช่วงเวลาที่เลือก" };
  }
  if (v >= 36) {
    return {
      level: "danger",
      badge: "ร้อนจัดอันตรายมาก (เสี่ยง Heatstroke)",
      desc: `อากาศระดับทางเดินเท้า ${v.toFixed(2)} °C อันตรายต่อสุขภาพ หลีกเลี่ยงการอยู่กลางแจ้ง`,
    };
  }
  if (v >= 33) {
    return {
      level: "danger",
      badge: "ร้อนอันตราย (เสี่ยงเพลียแดด)",
      desc: `อากาศระดับทางเดินเท้า ${v.toFixed(2)} °C เมื่อรวมความชื้นจะรู้สึกร้อนจัด ควรพักในที่ร่มเป็นระยะ`,
    };
  }
  if (v >= 30) {
    return {
      level: "warn",
      badge: "เริ่มร้อนอบอ้าว (เฝ้าระวัง)",
      desc: `อากาศระดับทางเดินเท้า ${v.toFixed(2)} °C (ความชื้นแบบไทยทำให้รู้สึกร้อนเทียบเท่า ~37–40 °C)`,
    };
  }
  return {
    level: "cool",
    badge: "อากาศสบาย / ปกติ",
    desc: `อุณหภูมิอากาศระดับทางเดินเท้า ${v.toFixed(2)} °C อยู่ในเกณฑ์สบายตัว`,
  };
}

export function buildVerdictHtml(lst: unknown, air?: unknown, includeAir = true): string {
  const s = evalSurfaceLst(lst);
  const a = includeAir ? evalAirTemp(air) : null;
  return `
    <div class="heat-verdict">
      <div class="verdict-row">
        <div class="verdict-top">
          <span class="verdict-label">ประเมินความร้อนพื้นผิว (LST)</span>
          <span class="v-pill ${s.level}">${s.badge}</span>
        </div>
        <p class="verdict-desc ${s.level === "danger" ? "danger-text" : ""}">${s.desc}</p>
      </div>
      ${
        a
          ? `<div class="verdict-row">
              <div class="verdict-top">
                <span class="verdict-label">ประเมินความร้อนคนเดินเท้า (IoT Air)</span>
                <span class="v-pill ${a.level}">${a.badge}</span>
              </div>
              <p class="verdict-desc ${a.level === "danger" ? "danger-text" : ""}">${a.desc}</p>
            </div>`
          : ""
      }
    </div>
  `;
}
