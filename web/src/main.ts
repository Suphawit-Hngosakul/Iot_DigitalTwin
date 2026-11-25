import "./style.css";
import { loadConfig } from "./api";
import { MapController, type ProjectMode } from "./map-view";
import { StatsController } from "./stats-view";
import { ResearchController } from "./research-view";
import { IotController } from "./iot-view";
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
  const mapCtrl = new MapController(
    () => currentPeriod,
    (p: string, periods: LayersResponse["periods"]) => {
      const sel = document.getElementById("period") as HTMLSelectElement;
      sel.innerHTML = periods.map((x) => `<option value="${esc(x.period)}">${esc(x.period)}</option>`).join("");
      sel.value = p;
      currentPeriod = p;
    },
    () => currentMatch
  );

  function showTab(name: string) {
    if (projectMode === "cs341" && name === "iot") name = "map";
    activeTab = name;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", (x as HTMLElement).dataset.tab === name));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "map") mapCtrl.map.resize();
    if (name === "stats") {
      if (!document.getElementById("stats-sub-research")!.classList.contains("hidden")) {
        researchCtrl.load();
      } else {
        statsCtrl.load();
      }
    }
    if (name === "iot") iotCtrl.load();
    statsCtrl.resize();
    iotCtrl.resize();
  }

  function switchProjectMode(mode: ProjectMode) {
    projectMode = mode;
    document.getElementById("mode-p2")!.classList.toggle("active", mode === "project2");
    document.getElementById("mode-cs341")!.classList.toggle("active", mode === "cs341");

    // โหมด CS341 ซ่อนแท็บ IoT ทั้งหมด
    document.getElementById("tab-btn-iot")!.classList.toggle("hidden", mode === "cs341");

    mapCtrl.setProjectMode(mode);
    if (mode === "cs341" && activeTab === "iot") {
      showTab("map");
    }
  }

  // ปุ่มสลับ 2 โปรเจกต์
  document.getElementById("mode-p2")!.onclick = () => switchProjectMode("project2");
  document.getElementById("mode-cs341")!.onclick = () => switchProjectMode("cs341");

  // ปุ่มแท็บหลัก 3 แท็บ
  document.querySelectorAll("#tabs button").forEach((b) => {
    (b as HTMLElement).onclick = () => showTab((b as HTMLElement).dataset.tab!);
  });

  // ปุ่มสลับมุมมองย่อยในหน้า "วิเคราะห์ปัจจัยความร้อน" <-> "คำนวณ & เทียบงานวิจัย UHI"
  const chartsSub = document.getElementById("stats-sub-charts")!;
  const researchSub = document.getElementById("stats-sub-research")!;
  const subChartsBtn = document.getElementById("sub-charts-btn")!;
  const subResearchBtn = document.getElementById("sub-research-btn")!;

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

  // ตัวเลือกช่วงเวลาและโหมดเวลาด้านบน
  (document.getElementById("period") as HTMLSelectElement).onchange = async (e) => {
    currentPeriod = (e.target as HTMLSelectElement).value;
    await mapCtrl.refreshPeriod();
    if (activeTab === "stats") {
      if (!researchSub.classList.contains("hidden")) researchCtrl.load(true);
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