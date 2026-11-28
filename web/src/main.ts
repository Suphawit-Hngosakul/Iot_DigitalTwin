import "./styles/index.css";
import { CFG, loadConfig } from "./config";
import { IotHistoryController } from "./iot/iot-history";
import { IotLiveController } from "./iot/iot-live";
import type { MatchMode } from "./map/context";
import { MapController } from "./map/map-controller";
import { CascadingPeriodController } from "./period-selector";
import { ResearchController } from "./stats/research-view";
import { StatsController } from "./stats/stats-view";
import { byId } from "./utils/html";

type TabName = "map" | "stats" | "iot";

async function bootstrap() {
  await loadConfig();
  showConfigNotice();

  let currentPeriod: string | null = null;
  let currentMatch: MatchMode = "all";
  let activeTab: TabName = "map";

  const stats = new StatsController(
    () => currentPeriod,
    () => currentMatch,
  );
  const research = new ResearchController(
    () => currentPeriod,
    () => currentMatch,
  );
  const iotHistory = new IotHistoryController();
  const iotLive = new IotLiveController();

  const periodCtrl = new CascadingPeriodController((p) => {
    currentPeriod = p;
    onPeriodOrMatchChanged();
  });

  const mapCtrl = new MapController(
    () => periodCtrl.getPeriod() || currentPeriod,
    (p, periods) => {
      currentPeriod = p;
      periodCtrl.setPeriods(periods, p, false);
    },
    () => currentMatch,
  );

  // ── แท็บ 2: กราฟวิเคราะห์ ↔ คำนวณ & เทียบงานวิจัย ─────────────
  const chartsSub = byId("stats-sub-charts");
  const researchSub = byId("stats-sub-research");
  const isResearch = () => !researchSub.classList.contains("hidden");

  function loadStatsView(force = false) {
    if (isResearch()) {
      research.load(force);
    } else {
      stats.load(force);
      stats.resize();
    }
  }

  function showStatsSub(sub: "charts" | "research") {
    const research = sub === "research";
    chartsSub.classList.toggle("hidden", research);
    researchSub.classList.toggle("hidden", !research);
    byId("sub-charts-btn").classList.toggle("active", !research);
    byId("sub-research-btn").classList.toggle("active", research);
    loadStatsView();
  }

  byId("sub-charts-btn").onclick = () => showStatsSub("charts");
  byId("sub-research-btn").onclick = () => showStatsSub("research");
  byId("sub-back-btn").onclick = () => showStatsSub("charts");

  // ── แท็บ 3: Realtime ↔ ข้อมูลย้อนหลัง ─────────────────────────
  function showIotSub(sub: "live" | "hist") {
    const hist = sub === "hist";
    byId("iot-mode-live").classList.toggle("active", !hist);
    byId("iot-mode-hist").classList.toggle("active", hist);
    byId("iot-sub-live").classList.toggle("hidden", hist);
    byId("iot-sub-hist").classList.toggle("hidden", !hist);
    if (hist) iotHistory.resize();
    else iotLive.resize();
  }

  byId("iot-mode-live").onclick = () => showIotSub("live");
  byId("iot-mode-hist").onclick = () => showIotSub("hist");

  // ── แท็บหลัก ───────────────────────────────────────────────
  function showTab(name: TabName) {
    activeTab = name;
    document.querySelectorAll<HTMLElement>("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "map") mapCtrl.resize();
    if (name === "stats") loadStatsView();
    if (name === "iot") {
      iotHistory.load();
      requestAnimationFrame(() => iotLive.resize());
    }
    stats.resize();
    iotHistory.resize();
  }

  document.querySelectorAll<HTMLElement>("#tabs button").forEach((b) => {
    b.onclick = () => showTab(b.dataset.tab as TabName);
  });

  // ── period / โหมดเวลา เปลี่ยน → แผนที่โหลดใหม่ทันที, สถิติโหลดใหม่เมื่ออยู่ในแท็บนั้น ──
  async function onPeriodOrMatchChanged() {
    await mapCtrl.refreshPeriod();
    if (activeTab === "stats") {
      loadStatsView(true);
    } else {
      stats.invalidate();
      research.invalidate();
    }
  }

  byId<HTMLSelectElement>("match").onchange = (e) => {
    currentMatch = (e.target as HTMLSelectElement).value as MatchMode;
    onPeriodOrMatchChanged();
  };

  window.addEventListener("resize", () => {
    stats.resize();
    iotHistory.resize();
  });

  mapCtrl.init();
  iotLive.init();
}

/** ไม่มี api_url ใน config.json → บอกที่หัวเว็บ (ทุกแผงจะขึ้น error ของตัวเองด้วย) */
function showConfigNotice() {
  if (CFG.api_url) return;
  const badge = byId("config-badge");
  badge.textContent = "ยังไม่ได้ตั้งค่า API";
  badge.title = "ใส่ api_url ใน config.json (สร้างด้วย make-config.sh หรือ cloud/infra/deploy.py)";
  badge.classList.remove("hidden");
}

bootstrap();
