/**
 * ค่าตั้งตอน runtime ของเว็บ — อ่านจาก `config.json` ข้าง index.html ครั้งเดียวตอนเปิดหน้า
 * (ไฟล์นี้สร้างตอน deploy ด้วย make-config.sh / deploy.py จึงเปลี่ยน URL ได้โดยไม่ต้อง build ใหม่)
 *
 * - `api_url` ว่าง → ทุกแผงขึ้นข้อความว่ายังไม่ได้ตั้งค่า API
 * - `hub_live_url` ว่าง → หน้า Realtime ใช้ /latest แทน WebSocket
 */
export interface RuntimeConfig {
  api_url: string;
  hub_live_url: string;
  deploy_id: string;
}

export const CFG: RuntimeConfig = { api_url: "", hub_live_url: "", deploy_id: "" };

function normalizeApiUrl(raw: string): string {
  let u = raw.trim().replace(/\/$/, "");
  if (u && !/^https?:\/\//i.test(u)) u = "https://" + u;
  return u;
}

export async function loadConfig(): Promise<RuntimeConfig> {
  let apiUrl = import.meta.env.VITE_API_URL || "";
  let liveUrl = "";
  let deployId = "";

  try {
    const r = await fetch(`./config.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) {
      const j = await r.json();
      if (j.api_url) apiUrl = String(j.api_url);
      // ws_url คือชื่อเก่าที่ iot-live เคยรองรับ
      if (j.hub_live_url || j.ws_url) liveUrl = String(j.hub_live_url || j.ws_url);
      if (j.deploy_id) deployId = String(j.deploy_id);
    }
  } catch {
    // ไม่มี config.json (เช่นรัน npm run dev โดยไม่ได้ตั้ง VITE_API_URL)
  }

  CFG.api_url = normalizeApiUrl(apiUrl);
  CFG.hub_live_url = liveUrl.trim();
  CFG.deploy_id = deployId;
  return CFG;
}
