import "./style.css";
import { loadConfig } from "./api";
import { MapController } from "./map-view";
import { StatsController } from "./stats-view";
import { ResearchController } from "./research-view";
import { IotController } from "./iot-view";
import { CascadingPeriodController } from "./period-selector";
import type { LayersResponse } from "./types";

async function bootstrap() {
  await loadConfig();

  let currentPeriod: string | null = null;
  let currentMatch: "all" | "window" = "all";
  let activeTab = "map";

  const statsCtrl = new StatsController(() => currentPeriod, () => currentMatch);
  const researchCtrl = new ResearchController(() => currentPeriod, () => currentMatch);
  const iotCtrl = new IotController();

  const handlePeriodSelected = async (nextPeriod: string) => {
    currentPeriod = nextPeriod;
    await mapCtrl.refreshPeriod();
    if (activeTab === "stats") {
      if (!researchSub.classList.contains("hidden")) researchCtrl.load(true);
      else statsCtrl.load(true);
    } else {
      statsCtrl.invalidate();
      researchCtrl.invalidate();
    }
  };

  const periodCtrl = new CascadingPeriodController(handlePeriodSelected);

  let mapCtrl!: MapController;
  mapCtrl = new MapController(
    () => periodCtrl.getPeriod() || currentPeriod,
    (p: string, periods: LayersResponse["periods"]) => {
      currentPeriod = p;
      periodCtrl.setPeriods(periods, p, false);
    },
    () => currentMatch
  );

  const chartsSub = document.getElementById("stats-sub-charts")!;
  const researchSub = document.getElementById("stats-sub-research")!;
  const subChartsBtn = document.getElementById("sub-charts-btn")!;
  const subResearchBtn = document.getElementById("sub-research-btn")!;

  function syncStatsView() {
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

  function showTab(name: string) {
    activeTab = name;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", (x as HTMLElement).dataset.tab === name));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "map") mapCtrl.map.resize();
    if (name === "stats") {
      syncStatsView();
    }
    if (name === "iot") iotCtrl.load();
    statsCtrl.resize();
    iotCtrl.resize();
  }

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

  (document.getElementById("match") as HTMLSelectElement).onchange = async (e) => {
    currentMatch = (e.target as HTMLSelectElement).value as "all" | "window";
    await mapCtrl.refreshPeriod();
    if (activeTab === "stats") {
      if (!researchSub.classList.contains("hidden")) researchCtrl.load(true);
      else statsCtrl.load(true);
    } else {
      statsCtrl.invalidate();
      researchCtrl.invalidate();
    }
  };

  window.addEventListener("resize", () => {
    statsCtrl.resize();
    iotCtrl.resize();
  });

  await mapCtrl.initData();
}

bootstrap();