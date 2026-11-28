const ESC_MAP: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

/** escape ข้อความก่อนแทรกลงใน innerHTML / template string */
export const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ESC_MAP[c]);

/** หา element ตาม id (โยน error ถ้าไม่เจอ เพื่อให้รู้ทันทีว่า index.html กับโค้ดไม่ตรงกัน) */
export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} not found in index.html`);
  return el as T;
}

/** เหมือน byId แต่คืน null เมื่อไม่เจอ */
export const maybeById = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T | null;

/** ข้อความ error สีแดงสำหรับแทรกในแผงต่างๆ */
export const errHtml = (msg: string) => `<span class="err">${esc(msg)}</span>`;
