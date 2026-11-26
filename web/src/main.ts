import "./style.css";
import { loadConfig } from "./api";
import { MapController, type ProjectMode } from "./map-view";
import { StatsController } from "./stats-view";
import { ResearchController } from "./research-view";
import { IotController } from "./iot-view";
import { CommunityStatsController } from "./community-priority";
import type { LayersResponse } from "./types";

const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

async function bootstrap() {
  await loadConfig();

  let currentPeriod: string | null = null;
  let currentMatch: "all" | "window" = "all";
  let projectMode: ProjectMode = "project2";
  let activeTab = "map";

  const statsCtrl = new StatsController(() => currentPeriod, () => currentMatch);
  const researchCtrl = new ResearchController(() => currentPeriod, () => currentMatch);
  const iotCtrl = new IotController();
  const commStatsCtrl = new CommunityStatsController();

  let mapCtrl!: MapController;
  mapCtrl = new MapController(
    () => currentPeriod,
    (p: string, periods: LayersResponse["periods"]) => {
      const sel = document.getElementById("period") as HTMLSelectElement;
      sel.innerHTML = periods.map((x) => `<option value="${esc(x.period)}">${esc(x.period)}</option>`).join("");
      sel.value = p;
      currentPeriod = p;
    },
    () => currentMatch,
    (features) => {
      commStatsCtrl.setCommunities(features);
    }
  );

  commStatsCtrl.setJumpHandler(async (communityId: string) => {
    switchProjectMode("cs341");
    showTab("map");
    await mapCtrl.focusCommunityById(communityId);
  });

  const chartsSub = document.getElementById("stats-sub-charts")!;
  const researchSub = document.getElementById("stats-sub-research")!;
  const cs341StatsView = document.getElementById("cs341-stats-view")!;
  const statsToolbar = document.querySelector("#tab-stats .stats-toolbar")!;
  const subChartsBtn = document.getElementById("sub-charts-btn")!;
  const subResearchBtn = document.getElementById("sub-research-btn")!;

  function syncStatsModeView() {
    const isCs341 = projectMode === "cs341";
    statsToolbar.classList.toggle("hidden", isCs341);
    cs341StatsView.classList.toggle("hidden", !isCs341);
    if (isCs341) {
      chartsSub.classList.add("hidden");
      researchSub.classList.add("hidden");
      commStatsCtrl.render();
      commStatsCtrl.resize();
    } else {
      const isResearch = subResearchBtn.classList.contains("active");
      chartsSub.classList.toggle("hidden", isResearch);
      researchSub.classList.toggle("hidden", !isResearch);
      if (isResearch) {
        researchCtrl.load();
      } else {
        statsCtrl.load();
        statsCtrl.resize();
      }
    }
  }

  function showTab(name: string) {
    if (projectMode === "cs341" && name === "iot") name = "map";
    activeTab = name;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", (x as HTMLElement).dataset.tab === name));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "map") mapCtrl.map.resize();
    if (name === "stats") {
      syncStatsModeView();
    }
    if (name === "iot") iotCtrl.load();
    statsCtrl.resize();
    iotCtrl.resize();
    commStatsCtrl.resize();
  }

  function switchProjectMode(mode: ProjectMode) {
    projectMode = mode;
    document.getElementById("mode-p2")!.classList.toggle("active", mode === "project2");
    document.getElementById("mode-cs341")!.classList.toggle("active", mode === "cs341");

    document.getElementById("tab-btn-iot")!.classList.toggle("hidden", mode === "cs341");

    mapCtrl.setProjectMode(mode);
    if (mode === "cs341" && activeTab === "iot") {
      showTab("map");
    } else if (activeTab === "stats") {
      syncStatsModeView();
    }
  }

  document.getElementById("mode-p2")!.onclick = () => switchProjectMode("project2");
  document.getElementById("mode-cs341")!.onclick = () => switchProjectMode("cs341");

  document.querySelectorAll("#tabs button").forEach((b) => {
    (b as HTMLElement).onclick = () => showTab((b as HTMLElement).dataset.tab!);
  });

  const showStatsSub = (sub: "charts" | "research") => {
    const isResearch = sub === "research";
    chartsSub.classList.toggle("hidden", isResearch);
    researchSub.classList.toggle("hidden", !isResearch);
    subChartsBtn.classList.toggle("active", !isResearch);
    subResearchBtn.classList.toggle("active", isResearch);
    if (isResearch) {
      researchCtrl.load();
    } else {
      statsCtrl.load();
      statsCtrl.resize();
    }
  };

  subChartsBtn.onclick = () => showStatsSub("charts");
  subResearchBtn.onclick = () => showStatsSub("research");
  document.getElementById("sub-back-btn")!.onclick = () => showStatsSub("charts");

  (document.getElementById("period") as HTMLSelectElement).onchange = async (e) => {
    currentPeriod = (e.target as HTMLSelectElement).value;
    await mapCtrl.refreshPeriod();
    if (activeTab === "stats") {
      if (projectMode === "cs341") commStatsCtrl.render();
      else if (!researchSub.classList.contains("hidden")) researchCtrl.load(true);
      else statsCtrl.load(true);
    } else {
      statsCtrl.invalidate();
      researchCtrl.invalidate();
    }
  };

  (document.getElementById("match") as HTMLSelectElement).onchange = async (e) => {
    currentMatch = (e.target as HTMLSelectElement).value as "all" | "window";
    await mapCtrl.refreshPeriod();
    if (activeTab === "stats") {
      if (projectMode === "cs341") commStatsCtrl.render();
      else if (!researchSub.classList.contains("hidden")) researchCtrl.load(true);
      else statsCtrl.load(true);
    } else {
      statsCtrl.invalidate();
      researchCtrl.invalidate();
    }
  };

  window.addEventListener("resize", () => {
    statsCtrl.resize();
    iotCtrl.resize();
    commStatsCtrl.resize();
  });

  await mapCtrl.initData();
}

bootstrap();