import { CFG } from "./config";
import type { ReadingMinuteRow, ReadingsResponse } from "./types";

export type ApiParams = Record<string, unknown>;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
  }
}

function buildQuery(params: ApiParams): string {
  return new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}

/** เรียก API ตัวเดียวของทั้งเว็บ: GET api_url + path แล้วคืน JSON (status ≠ 200 → ApiError) */
export async function api<T = unknown>(path: string, params: ApiParams = {}): Promise<T> {
  if (!CFG.api_url) throw new ApiError("ยังไม่ได้ตั้งค่า api_url ใน config.json", 0);

  const qs = buildQuery(params);
  let r: Response;
  try {
    r = await fetch(CFG.api_url + path + (qs ? "?" + qs : ""));
  } catch (e: unknown) {
    throw new ApiError("ไม่สามารถเชื่อมต่อ API ได้: " + errMsg(e), 0);
  }
  const body = await r.json().catch(() => ({ error: "response is not JSON" }));
  if (r.status !== 200) {
    throw new ApiError((body as { error?: string })?.error || "HTTP " + r.status, r.status, body);
  }
  return body as T;
}

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function toMinuteObjects(res: ReadingsResponse): ReadingMinuteRow[] {
  return res.rows.map((r) => Object.fromEntries(res.columns.map((c, i) => [c, r[i]])) as unknown as ReadingMinuteRow);
}
