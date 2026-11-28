/** ตัวเลข → ทศนิยม d ตำแหน่ง, ค่าว่าง/NaN → "—", ข้อความคืนตามเดิม */
export const fmt = (v: unknown, d = 1) => (v == null || Number.isNaN(Number(v)) ? "—" : typeof v === "number" ? v.toFixed(d) : String(v));

/** เหมือน fmt แต่แปลงข้อความตัวเลขเป็น number ก่อน (ใช้กับค่าจาก /readings) */
export const fmtNum = (v: unknown, d = 1) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));

/** "2026-11-21T11:00:00" → "2026-11-21" */
export const dayOf = (s?: string | null) => (s || "").slice(0, 10);

/** "2026-11-21" → "2026-11-22" */
export const nextDay = (day: string) => new Date(Date.parse(day + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);

/** ช่วงเวลาทั้งวันในรูปแบบที่ /readings, /quality ต้องการ */
export const dayRange = (day: string) => ({ from: day + "T00:00:00", to: nextDay(day) + "T00:00:00" });

export function statsOf(vals: (number | null | undefined)[]) {
  const clean = vals.filter((v): v is number => typeof v === "number" && !Number.isNaN(v));
  if (!clean.length) return { avg: null, min: null, max: null };
  const sum = clean.reduce((a, b) => a + b, 0);
  return { avg: sum / clean.length, min: Math.min(...clean), max: Math.max(...clean) };
}
