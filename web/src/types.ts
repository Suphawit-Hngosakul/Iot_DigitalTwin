export interface AppConfig {
  api_url: string;
  hub_live_url: string;
  mock: boolean;
}

export interface ApiResult<T = unknown> {
  status: number;
  body: T;
}

export interface ApiLogEntry {
  url: string;
  status: number;
  body: unknown;
  ms: number;
  at: Date;
  source: "mock" | "real";
}

export interface DeviceInfo {
  device_id: string;
  device_type: "Mobile" | "Stationary";
  site_name: string | null;
  fixed_lat: number | null;
  fixed_lon: number | null;
  latest_batch_id: string | null;
  latest_hour_local: string | null;
  published_at: string | null;
}

export interface ReadingMinuteRow {
  minute_local: string;
  minute_utc: string;
  n_rows: number;
  db_spl_mean: number;
  db_spl_max: number | null;
  db_spl_min: number | null;
  dba_mean: number;
  temp_c_mean: number;
  humidity_pct_mean: number;
  lux_mean: number;
  lat: number | null;
  lon: number | null;
  cell_id: string | null;
}

export interface ReadingsResponse {
  device: string;
  interval: "1s" | "1min";
  from: string;
  to: string;
  columns: string[];
  rows: unknown[][];
}

export interface QualityHour {
  batch_id: string;
  hour_local: string;
  sessions: number;
  expected_rows: number;
  received_rows: number;
  missing_rows: number;
  duplicate_rows: number;
  invalid_rows: number;
  dropped_rows: number;
  loss_rate: number;
}

export interface QualityResponse {
  device: string;
  from: string;
  to: string;
  hours: QualityHour[];
}

export interface LayerLegend {
  column: string;
  min: number;
  max: number;
  unit: string;
  stops: [number, string | [number, number, number]][];
}

export interface LayerItem {
  layer: "lst" | "ndvi" | "ndbi" | "building_density";
  url: string;
  url_expires_in_s: number;
  coordinates: [number, number][];
  legend: LayerLegend;
  stops?: [number, string][];
}

export interface LayersResponse {
  period: string;
  period_type: string;
  periods: { period: string; period_type: string; date_start: string; date_end: string }[];
  layers: LayerItem[];
  static_layers: LayerItem[];
}

export interface GridFeature {
  type: "Feature";
  id: string;
  geometry: { type: "Polygon"; coordinates: [number, number][][] };
  properties: Record<string, number | string | null>;
}

export interface GridFeatureCollection {
  type: "FeatureCollection";
  period: string;
  match: "all" | "window";
  vars: string[];
  features: GridFeature[];
}

export interface GridCellDetail {
  cell_id: string;
  period: string;
  geometry: { type: "Polygon"; coordinates: [number, number][][] };
  match: {
    window: Record<string, number | string | null>;
    all: Record<string, number | string | null>;
  };
}

export interface AnalyticsSummary {
  period: string;
  match: string;
  rows: { variable: string; n: number; mean: number; sd: number; min: number; max: number; p90: number }[];
}

export interface AnalyticsCorrelation {
  period: string;
  match: string;
  method: string;
  variables: string[];
  r: number[][];
  p_value: number[][];
  n: number[][];
}

export interface RegressionTerm {
  term: string;
  coef: number;
  std_coef: number;
  p_value: number;
  vif: number;
}

export interface RegressionModel {
  model: string;
  formula: string;
  n: number;
  r2: number | null;
  adj_r2?: number | null;
  terms: RegressionTerm[];
  note?: string;
}

export interface AnalyticsRegression {
  period: string;
  match: string;
  models: RegressionModel[];
}

export interface AnalyticsImportance {
  period: string;
  match: string;
  model: string;
  features: { feature: string; importance: number; std: number }[];
}

export interface AnalyticsHotspot {
  period: string;
  match: string;
  variable: string;
  distance_m: number;
  cells: { cell_id: string; gi_z: number; gi_p: number; class: string }[];
}

export interface AnalyticsSpatialAutocorr {
  period: string;
  match: string;
  rows: { target: string; morans_i: number; p_value: number }[];
}

export interface AnalyticsGroups {
  period: string;
  match: string;
  by: string;
  groups: { group: string; n: number; ndvi_range: [number, number]; lst_mean: number; lst_sd: number }[];
  diff_dense_minus_green: number;
}

export interface AnalyticsStationDiurnal {
  period: string;
  match: string;
  stations: { device_id: string; site_name: string | null; hours: { hour: number; air_temp_mean: number | null }[] }[];
}

export interface LiveDeviceState {
  device_id: string;
  online: boolean;
  ts?: string;
  dba?: number;
  db_spl?: number;
  lux?: number;
  temp_c?: number;
  humidity_pct?: number;
  lat?: number;
  lon?: number;
  health?: { heap_free: number; rssi: number; sd_errors?: number; backlog_rows?: number };
}
export interface BuildingFeatureProperties {
  bldg_id: string;
  floors: number;
  height_m: number;
  area_sqm: number;
  bldg_type: string;
  bldg_source: string;
  cell_id: string | null;
}

export interface BuildingsGeoJson {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: { type: "Polygon"; coordinates: [number, number][][] };
    properties: BuildingFeatureProperties;
  }[];
}