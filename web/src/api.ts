import type { AppConfig, ReadingMinuteRow, ReadingsResponse } from "./types";
import { handleMockApi } from "./mock-api";

export interface RuntimeEnv extends AppConfig {
  deploy_id?: string;
}

export const CFG: RuntimeEnv = { api_url: "", hub_live_url: "", mock: false, deploy_id: "" };
const MOCK_FALLBACK_ROUTES = /^\/analytics\//;

function normalizeApiUrl(raw: string): string {
  let u = raw.trim().replace(/\/$/, "");
  if (u && !/^https?:\/\//i.test(u)) u = "https://" + u;
  return u;
}

export async function loadConfig(): Promise<RuntimeEnv> {
  let deployedApi = import.meta.env.VITE_API_URL || "";
  let deployId = "";

  try {
    const r = await fetch(`./config.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) {
      const j = await r.json();
      if (j.api_url) deployedApi = String(j.api_url);
      if (j.deploy_id) deployId = String(j.deploy_id);
    }
  } catch {
    // กรณีรันในเครื่อง Local ที่ไม่มี config.json
  }

  CFG.api_url = normalizeApiUrl(deployedApi);
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
  let status = 0;
  let body: unknown;

  if (!CFG.mock) {
    try {
      const r = await fetch(CFG.api_url + url);
      status = r.status;
      body = await r.json().catch(() => ({ error: "response is not JSON" }));
      if ((status === 404 || status === 501) && MOCK_FALLBACK_ROUTES.test(path)) {
        ({ status, body } = await handleMockApi(path, params));
      }
    } catch (e: unknown) {
      if (MOCK_FALLBACK_ROUTES.test(path)) {
        ({ status, body } = await handleMockApi(path, params));
      } else {
        status = 0;
        body = { error: "ไม่สามารถเชื่อมต่อ API ได้: " + (e instanceof Error ? e.message : String(e)) };
      }
    }
  } else {
    ({ status, body } = await handleMockApi(path, params));
  }

  if (status !== 200) {
    const errMsg = (body as { error?: string })?.error || "HTTP " + status;
    throw Object.assign(new Error(errMsg), { status, body });
  }
  return body as T;
}

export function toMinuteObjects(res: ReadingsResponse): ReadingMinuteRow[] {
  return res.rows.map((r) => Object.fromEntries(res.columns.map((c, i) => [c, r[i]])) as unknown as ReadingMinuteRow);
}