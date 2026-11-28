import maplibregl from "maplibre-gl";
import type { LayersResponse } from "../types";
import { byId } from "../utils/html";
import { BuildingsLayer } from "./buildings-layer";
import type { MapContext, MatchMode } from "./context";
import { DevicesLayer } from "./devices-layer";
import { DetailDrawer } from "./drawer";
import { GridLayer } from "./grid-layer";
import { HotspotLayer } from "./hotspot-layer";
import { LivePosition } from "./live-position";
import { SatelliteLayers } from "./satellite-layers";
import { TrackLayer } from "./track-layer";

/** จุดกึ่งกลางเริ่มต้น (เขตหลักสี่) — เมื่อโหลด layer ดาวเทียมหรืออาคารแล้วจะซูมไปที่ข้อมูลจริง */
const START_CENTER: [number, number] = [100.575, 13.883];

const BASE_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
    imagery: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Imagery © Esri",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#eef0f2" } },
    { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6, "raster-opacity": 0.9 } },
    { id: "imagery", type: "raster", source: "imagery", layout: { visibility: "none" } },
  ],
};

/**
 * แท็บแผนที่: สร้าง MapLibre แล้วประกอบ layer ต่างๆ (แต่ละ layer อยู่ในไฟล์ของตัวเองใน map/)
 * ลำดับโหลด: ดาวเทียม → กริด → อาคาร → hotspot → เครื่อง/station → เส้นทางเดิน
 */
export class MapController {
  readonly map: maplibregl.Map;
  private ctx: MapContext;
  private satellite: SatelliteLayers;
  private grid: GridLayer;
  private buildings: BuildingsLayer;
  private hotspot: HotspotLayer;
  private devices: DevicesLayer;
  private track: TrackLayer;

  constructor(
    getPeriod: () => string | null,
    onPeriods: (period: string, periods: LayersResponse["periods"]) => void,
    getMatch: () => MatchMode,
  ) {
    this.map = new maplibregl.Map({ container: "map", style: BASE_STYLE, center: START_CENTER, zoom: 14.2, pitch: 30 });
    this.map.addControl(new maplibregl.NavigationControl(), "top-right");
    this.map.addControl(new maplibregl.ScaleControl(), "bottom-left");

    byId<HTMLSelectElement>("basemap").onchange = (e) => {
      const val = (e.target as HTMLSelectElement).value;
      this.map.setLayoutProperty("osm", "visibility", val === "osm" ? "visible" : "none");
      this.map.setLayoutProperty("imagery", "visibility", val === "imagery" ? "visible" : "none");
    };

    this.ctx = { map: this.map, drawer: new DetailDrawer(this.map), getPeriod, getMatch, grid: null, devices: [], fitted: false };
    this.satellite = new SatelliteLayers(this.ctx, onPeriods);
    this.grid = new GridLayer(this.ctx);
    this.buildings = new BuildingsLayer(this.ctx);
    this.hotspot = new HotspotLayer(this.ctx);
    this.devices = new DevicesLayer(this.ctx);
    this.track = new TrackLayer(this.ctx, new LivePosition(this.map));
  }

  /** โหลดทุก layer เมื่อ style ของแผนที่พร้อม */
  init() {
    this.map.on("load", async () => {
      await this.satellite.load();
      await this.grid.load();
      await this.buildings.load();
      await this.hotspot.load();
      await this.devices.load();
      await this.track.load();
    });
  }

  /** period หรือโหมดเวลาเปลี่ยน → โหลด layer ที่ขึ้นกับ period ใหม่ */
  async refreshPeriod() {
    await this.satellite.load();
    await this.grid.load();
    await this.hotspot.load();
  }

  resize() {
    this.map.resize();
  }
}
