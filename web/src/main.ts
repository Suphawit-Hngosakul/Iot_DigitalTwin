import "./style.css";
import { api, CFG, createLiveSocket, lastResponses, loadConfig, onApiLog } from "./api";
import { MapController } from "./map-view";
import { StatsController } from "./stats-view";
import { ResearchController } from "./research-view";
import { IotController } from "./iot-view";
import type { ApiLogEntry, LayersResponse, LiveDeviceState } from "./types";

const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

function preview(obj: unknown): string {
  return JSON.stringify(
    obj,
    (k, v) => {
      if (typeof v === "string" && v.startsWith("data:image")) {
        return v.slice(0, 40) + `…(PNG ${Math.round(v.length / 1024)} KB — ของจริงเป็น presigned URL ของ S3)`;
      }
      if (Array.isArray(v) && v.length > 8 && k !== "coordinates") {
        return [...v.slice(0, 8), `… อีก ${v.length - 8} รายการ`];
      }
      return v;
    },
    2
  );
}

const sizeLabel = (o: unknown) => {
  const n = JSON.stringify(o).length;
  return n > 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B";
};

function openModal(title: string, body: unknown, note = "") {
  document.getElementById("modal-title")!.textContent = title;
  document.getElementById("modal-note")!.textContent = note;
  document.getElementById("modal-json")!.textContent = typeof body === "string" ? body : preview(body);
  document.getElementById("modal")!.classList.remove("hidden");
}

function addLog(e: ApiLogEntry) {
  const list = document.getElementById("log-list")!;
  const li = document.createElement("li");
  li.innerHTML = `<span class="srcTag ${e.source}">${e.source}</span><span class="st s${String(e.status)[0]}">${e.status}</span> GET ${esc(e.url)} <small>${e.ms} ms · ${sizeLabel(e.body)}</small>`;
  li.onclick = () =>
    openModal("GET " + e.url, e.body, `${e.source === "real" ? CFG.api_url : "mock-api"} · HTTP ${e.status} · ${e.ms} ms · ${sizeLabel(e.body)}`);
  list.prepend(li);
  if (!CFG.mock && e.url.startsWith("/analytics/")) {
    const banner = document.getElementById("stats-banner");
    if (banner) banner.classList.toggle("hidden", e.source === "real");
  }
  while (list.children.length > 80) list.lastChild?.remove();
}

async function bootstrap() {
  await loadConfig();
  onApiLog(addLog);

  let currentPeriod: string | null = null;
  let currentMatch: "all" | "window" = "all";
  const liveState: Record<string, LiveDeviceState> = {};
  let lastWs: unknown = null;
  let wsConn: ReturnType<typeof createLiveSocket> = null;

  if (!CFG.mock) {
    const badge = document.getElementById("mode-badge")!;
    badge.textContent = "REAL API";
    badge.classList.add("live");
    badge.title = CFG.api_url;
    // ซ่อน banner ไว้ก่อน จะแสดงอัตโนมัติเฉพาะเมื่อ /analytics/* fallback ไปใช้ mock
  }

  (document.getElementById("cfg-api") as HTMLInputElement).value = CFG.api_url;
  (document.getElementById("cfg-live") as HTMLInputElement).value = CFG.hub_live_url;

  document.getElementById("cfg-toggle")!.onclick = () => {
    document.getElementById("cfg-bar")!.classList.toggle("hidden");
    document.querySelector("header")!.classList.toggle("cfg-open");
  };
  document.getElementById("cfg-save")!.onclick = () => {
    localStorage.setItem("api_url", (document.getElementById("cfg-api") as HTMLInputElement).value.trim());
    localStorage.setItem("live_url", (document.getElementById("cfg-live") as HTMLInputElement).value.trim());
    location.search = "";
  };
  document.getElementById("cfg-mock")!.onclick = () => {
    localStorage.setItem("api_url", "");
    localStorage.setItem("live_url", "");
    location.search = "";
  };

  document.getElementById("modal-close")!.onclick = () => document.getElementById("modal")!.classList.add("hidden");
  document.getElementById("modal")!.onclick = (e) => {
    if ((e.target as HTMLElement).id === "modal") document.getElementById("modal")!.classList.add("hidden");
  };
  document.getElementById("log-toggle")!.onclick = () => document.getElementById("log")!.classList.toggle("open");
  document.getElementById("log-close")!.onclick = () => document.getElementById("log")!.classList.remove("open");

  document.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest(".src") as HTMLElement | null;
    if (!b) return;
    const ep = b.dataset.ep!;
    if (ep === "ws") return openModal("ws /live — ข้อความล่าสุด", lastWs || "(ยังไม่มีข้อความ)", "WebSocket text frame (spec 01 §3, spec 22)");
    const entry = lastResponses[ep];
    if (!entry) return openModal(ep, "(ยังไม่ได้เรียก endpoint นี้ — เปิดแท็บที่ใช้ก่อน)", "");
    openModal("GET " + entry.url, entry.body, `${entry.source === "real" ? CFG.api_url : "mock-api"} · HTTP ${entry.status} · ${entry.ms} ms · ${sizeLabel(entry.body)}`);
  });

  const statsCtrl = new StatsController(() => currentPeriod, () => currentMatch);
  const researchCtrl = new ResearchController(() => currentPeriod, () => currentMatch);
  const iotCtrl = new IotController(
    () => liveState,
    (payload) => {
      if (wsConn && wsConn.readyState === 1) {
        wsConn.send(JSON.stringify(payload));
        return true;
      }
      return false;
    }
  );
  const mapCtrl = new MapController(
    () => currentPeriod,
    (p: string, periods: LayersResponse["periods"]) => {
      const sel = document.getElementById("period") as HTMLSelectElement;
      sel.innerHTML =
        periods.map((x) => `<option value="${esc(x.period)}">${esc(x.period)}</option>`).join("") +
        (CFG.mock ? `<option value="month_2026-11">month_2026-11 (grid/สถิติ)</option>` : "");
      sel.value = p;
      currentPeriod = p;
    },
    () => currentMatch
  );

  function startLive() {
    const note = document.getElementById("live-note")!;
    wsConn = createLiveSocket("all");
    if (!wsConn) {
      note.textContent = "ไม่ได้ใส่ hub_live_url จึงไม่มีค่าสด";
      return;
    }
    wsConn.binaryType = "arraybuffer";
    wsConn.onerror = () => {
      note.innerHTML = `<span class="err">ต่อ ${esc(wsConn?.url)} ไม่ได้ (EC2 hub อาจปิดอยู่)</span>`;
    };
    wsConn.onopen = () => {
      note.textContent = "ต่อ " + wsConn?.url + " แล้ว";
    };
    wsConn.onmessage = (ev: { data: string | ArrayBuffer }) => {
      if (ev.data instanceof ArrayBuffer) {
        iotCtrl.onAudioFrame(ev.data);
        return;
      }
      const m = JSON.parse(ev.data);
      if (m.t !== "audio_stats") lastWs = m;
      const L = (liveState[m.device_id] ||= { device_id: m.device_id, online: false });
      if (m.t === "status") L.online = Boolean(m.online);
      if (m.t === "reading") {
        Object.assign(L, m, { online: true });
        mapCtrl.moveLiveMarker(L);
      }
      if (m.t === "audio_stats") iotCtrl.onAudioStats(m.lost ?? 0);
      iotCtrl.renderLive();
    };
  }

  function showTab(name: string) {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", (x as HTMLElement).dataset.tab === name));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "map") mapCtrl.map.resize();
    if (name === "stats") statsCtrl.load();
    if (name === "research") researchCtrl.load();
    if (name === "iot") iotCtrl.load();
    statsCtrl.resize();
    iotCtrl.resize();
  }

  document.querySelectorAll("#tabs button").forEach((b) => {
    (b as HTMLElement).onclick = () => showTab((b as HTMLElement).dataset.tab!);
  });

  (document.getElementById("period") as HTMLSelectElement).onchange = async (e) => {
    currentPeriod = (e.target as HTMLSelectElement).value;
    await mapCtrl.refreshPeriod();
    if (document.getElementById("tab-stats")!.classList.contains("active")) statsCtrl.load(true);
    else statsCtrl.invalidate();
    if (document.getElementById("tab-research")!.classList.contains("active")) researchCtrl.load(true); else researchCtrl.invalidate();
  };

  (document.getElementById("match") as HTMLSelectElement).onchange = async (e) => {
    currentMatch = (e.target as HTMLSelectElement).value as "all" | "window";
    await mapCtrl.refreshPeriod();
    if (document.getElementById("tab-stats")!.classList.contains("active")) statsCtrl.load(true);
    else statsCtrl.invalidate();
    if (document.getElementById("tab-research")!.classList.contains("active")) researchCtrl.load(true); else researchCtrl.invalidate();
  };

  async function tryUrl(u: string) {
    (document.getElementById("try-url") as HTMLInputElement).value = u;
    const [path, qs] = u.split("?");
    const params = Object.fromEntries(new URLSearchParams(qs || ""));
    const out = document.getElementById("try-out")!;
    try {
      out.textContent = preview(await api(path, params));
    } catch (e: unknown) {
      const errObj = e as { status?: number; body?: unknown; message?: string };
      out.textContent = `HTTP ${errObj.status ?? 0}\n` + preview(errObj.body || { error: errObj.message });
    }
  }

  document.getElementById("try-go")!.onclick = () => tryUrl((document.getElementById("try-url") as HTMLInputElement).value.trim());
  document.querySelectorAll("[data-try]").forEach((a) => {
    (a as HTMLElement).onclick = (e) => {
      e.preventDefault();
      tryUrl((a as HTMLElement).dataset.try!);
    };
  });

  window.addEventListener("resize", () => {
    statsCtrl.resize();
    iotCtrl.resize();
  });

  await mapCtrl.initData(startLive);
  if (!CFG.mock) (document.getElementById("try-url") as HTMLInputElement).value = "/devices";
  tryUrl((document.getElementById("try-url") as HTMLInputElement).value);
}

bootstrap();