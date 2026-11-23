import type { ApiLogEntry, AppConfig, ReadingMinuteRow, ReadingsResponse } from "./types";
import { FakeLiveSocket, handleMockApi } from "./mock-api";

export interface RuntimeEnv extends AppConfig {
  deploy_id?: string;
}

// ตัวแปรกลางสำหรับเก็บ Endpoint ปัจจุบัน (เทียบเท่า A = api_url)
export const CFG: RuntimeEnv = { api_url: "", hub_live_url: "", mock: true, deploy_id: "" };
const MOCK_FALLBACK_ROUTES = /^\/analytics\//;

export const lastResponses: Record<string, ApiLogEntry> = {};
type LogListener = (entry: ApiLogEntry) => void;
const listeners: LogListener[] = [];
export const onApiLog = (fn: LogListener) => listeners.push(fn);

function normalizeApiUrl(raw: string): string {
  let u = raw.trim().replace(/\/$/, "");
  if (u && !/^https?:\/\//i.test(u)) u = "https://" + u;
  return u;
}

export async function loadConfig(): Promise<RuntimeEnv> {
  // 1. ค่าเริ่มต้นจากไฟล์ .env ตอน build (ถ้ามี)
  let deployedApi = import.meta.env.VITE_API_URL || "";
  let deployedLive = import.meta.env.VITE_HUB_LIVE_URL || "";
  let deployId = "";

  // 2. ดึงค่าล่าสุดจาก ./config.json แบบไม่ติด Cache (ไฟล์ที่ Terraform/CI สร้างให้ตอน deploy)
  try {
    const r = await fetch(`./config.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) {
      const j = await r.json();
      if (j.api_url) deployedApi = String(j.api_url);
      if (j.hub_live_url) deployedLive = String(j.hub_live_url);
      if (j.deploy_id) deployId = String(j.deploy_id);
    }
  } catch {
    // กรณีรันแบบไม่มี config.json
  }

  // ถ้ามีการ deploy ใหม่ (config.json มีค่าหรือ deploy_id เปลี่ยน) ให้ล้างค่าค้างใน localStorage อัตโนมัติ
  const prevDeploy = localStorage.getItem("deploy_id") || "";
  if (deployedApi || (deployId && deployId !== prevDeploy)) {
    localStorage.removeItem("api_url");
    localStorage.removeItem("live_url");
    if (deployId) localStorage.setItem("deploy_id", deployId);
  }

  // 3. ลำดับความสำคัญ: Query String -> config.json (Deploy ล่าสุด) -> localStorage (ที่ผู้ใช้กรอกทดสอบเอง)
  const urlq = new URLSearchParams(location.search);
  if (urlq.get("api") != null) localStorage.setItem("api_url", urlq.get("api")!);
  if (urlq.get("live") != null) localStorage.setItem("live_url", urlq.get("live")!);

  const manualApi = localStorage.getItem("api_url");
  const manualLive = localStorage.getItem("live_url");

  CFG.api_url = normalizeApiUrl(manualApi !== null ? manualApi : deployedApi);
  CFG.hub_live_url = (manualLive !== null ? manualLive : deployedLive).trim();
  CFG.deploy_id = deployId;
  CFG.mock = !CFG.api_url;
  return CFG;
}

export async function api<T = unknown>(path: string, params: Record<string, unknown> = {}): Promise<T> {
  const qs = new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => [k, String(v)])
  ).toString();
  const url = path + (qs ? "?" + qs : "");
  const t0 = performance.now();
  let status = 0;
  let body: unknown;

  const tryRealFirst = !CFG.mock;
  let source: "mock" | "real" = tryRealFirst ? "real" : "mock";

  if (tryRealFirst) {
    try {
      const r = await fetch(CFG.api_url + url);
      status = r.status;
      body = await r.json().catch(() => ({ error: "response is not JSON" }));
      if ((status === 404 || status === 501) && MOCK_FALLBACK_ROUTES.test(path)) {
        source = "mock";
        ({ status, body } = await handleMockApi(path, params));
      }
    } catch (e: unknown) {
      if (MOCK_FALLBACK_ROUTES.test(path)) {
        source = "mock";
        ({ status, body } = await handleMockApi(path, params));
      } else {
        status = 0;
        body = { error: "เรียก API ไม่ได้ (เน็ต/CORS/URL ผิด): " + (e instanceof Error ? e.message : String(e)) };
      }
    }
  } else {
    ({ status, body } = await handleMockApi(path, params));
  }

  const ms = Math.round(performance.now() - t0);
  const key = /^\/grid\/./.test(path) ? "/grid/{cell_id}" : path;
  const entry: ApiLogEntry = { url, status, body, ms, at: new Date(), source };
  lastResponses[key] = entry;
  listeners.forEach((fn) => fn(entry));

  if (status !== 200) {
    const errMsg = (body as { error?: string })?.error || "HTTP " + status;
    throw Object.assign(new Error(errMsg), { status, body });
  }
  return body as T;
}

export function createLiveSocket(device = "all"): WebSocket | FakeLiveSocket | null {
  if (!CFG.mock && !CFG.hub_live_url) return null;
  const url = CFG.mock
    ? `mock://live?device=${encodeURIComponent(device)}`
    : `${CFG.hub_live_url}${CFG.hub_live_url.includes("?") ? "&" : "?"}device=${encodeURIComponent(device)}`;
  return CFG.mock ? new FakeLiveSocket(url) : new WebSocket(url);
}

export function toMinuteObjects(res: ReadingsResponse): ReadingMinuteRow[] {
  return res.rows.map((r) => Object.fromEntries(res.columns.map((c, i) => [c, r[i]])) as unknown as ReadingMinuteRow);
}