/*
 * app.js — เอา JSON จาก API มาวาดบนจอ
 * ทุกครั้งที่ดึงข้อมูลจะผ่าน api() ตัวเดียว: โหมด MOCK ส่งไป mock-api.js, โหมดจริงใช้ fetch(API_URL + path)
 * โค้ดส่วนวาด (MapLibre / ECharts) จึงเหมือนกันทั้งสองโหมด
 */
(function () {
  "use strict";
  // ------------------------------------------------------------ config
  const CFG = Object.assign({ API_URL: "", LIVE_URL: "" }, window.APP_CONFIG || {});
  const urlq = new URLSearchParams(location.search);
  if (urlq.get("api") != null) localStorage.setItem("api_url", urlq.get("api"));
  if (urlq.get("live") != null) localStorage.setItem("live_url", urlq.get("live"));
  if (localStorage.getItem("api_url") != null) CFG.API_URL = localStorage.getItem("api_url");
  if (localStorage.getItem("live_url") != null) CFG.LIVE_URL = localStorage.getItem("live_url");
  CFG.API_URL = (CFG.API_URL || "").trim().replace(/\/$/, "");
  CFG.LIVE_URL = (CFG.LIVE_URL || "").trim();
  CFG.MOCK = !CFG.API_URL;
  // endpoint ที่ยังไม่มีบน API จริง → ใช้ mock เสมอ (/analytics รอ #33)
  const MOCK_ONLY = /^\/analytics\//;
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const state = { period: null, match: "all", last: {}, grid: null, live: {}, charts: {} };

  // ------------------------------------------------------------ API client
  async function api(path, params = {}) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")).toString();
    const url = path + (qs ? "?" + qs : "");
    const t0 = performance.now();
    let status, body;
    const source = CFG.MOCK || MOCK_ONLY.test(path) ? "mock" : "real";
    if (source === "mock") ({ status, body } = await MockAPI.handle(path, params));
    else {
      try { const r = await fetch(CFG.API_URL + url); status = r.status; body = await r.json().catch(() => ({ error: "response is not JSON" })); }
      catch (e) { status = 0; body = { error: "เรียกไม่ได้ (เน็ต/CORS/URL ผิด): " + e.message }; }
    }
    const ms = Math.round(performance.now() - t0);
    const key = /^\/grid\/./.test(path) ? "/grid/{cell_id}" : path;
    const entry = { url, status, body, ms, at: new Date(), source };
    state.last[key] = entry;
    addLog(entry);
    if (status !== 200) throw Object.assign(new Error(body.error || "HTTP " + status), { status, body });
    return body;
  }
  // แปลง /readings (columns + rows) เป็น array ของ object
  const toObjects = (res) => res.rows.map((r) => Object.fromEntries(res.columns.map((c, i) => [c, r[i]])));

  // ------------------------------------------------------------ JSON viewer + log
  function preview(obj) {
    return JSON.stringify(obj, function (k, v) {
      if (typeof v === "string" && v.startsWith("data:image")) return v.slice(0, 40) + `…(PNG ${Math.round(v.length / 1024)} KB — ของจริงเป็น presigned URL ของ S3)`;
      if (Array.isArray(v) && v.length > 8 && !(k === "coordinates")) return [...v.slice(0, 8), `… อีก ${v.length - 8} รายการ`];
      return v;
    }, 2);
  }
  const size = (o) => { const n = JSON.stringify(o).length; return n > 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B"; };
  function addLog(e) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="srcTag ${e.source}">${e.source}</span><span class="st s${String(e.status)[0]}">${e.status}</span> GET ${escapeHtml(e.url)} <small>${e.ms} ms · ${size(e.body)}</small>`;
    li.onclick = () => openModal("GET " + e.url, e.body, `${e.source === "real" ? CFG.API_URL : "mock-api.js"} · HTTP ${e.status} · ${e.ms} ms · ${size(e.body)}`);
    $("#log-list").prepend(li);
    while ($("#log-list").children.length > 80) $("#log-list").lastChild.remove();
  }
  function openModal(title, body, note) {
    $("#modal-title").textContent = title; $("#modal-note").textContent = note || "";
    $("#modal-json").textContent = typeof body === "string" ? body : preview(body);
    $("#modal").classList.remove("hidden");
  }
  const escapeHtml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  $("#modal-close").onclick = () => $("#modal").classList.add("hidden");
  $("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };
  $("#log-toggle").onclick = () => $("#log").classList.toggle("open");
  $("#log-close").onclick = () => $("#log").classList.remove("open");
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".src"); if (!b) return;
    const ep = b.dataset.ep;
    if (ep === "ws") return openModal("ws /live — ข้อความล่าสุด", state.lastWs || "(ยังไม่มีข้อความ)", "WebSocket text frame (spec 01 §3, spec 22) — hub เติม received_at");
    const e2 = state.last[ep];
    if (!e2) return openModal(ep, "(ยังไม่ได้เรียก endpoint นี้ — เปิดแท็บที่ใช้ก่อน)", "");
    openModal("GET " + e2.url, e2.body, `${e2.source === "real" ? CFG.API_URL : "mock-api.js"} · HTTP ${e2.status} · ${e2.ms} ms · ${size(e2.body)}`);
  });
  if (!CFG.MOCK) {
    $("#mode-badge").textContent = "REAL API (analytics = mock)"; $("#mode-badge").classList.add("live");
    $("#mode-badge").title = CFG.API_URL; $("#stats-banner").classList.remove("hidden");
  }
  $("#cfg-api").value = CFG.API_URL; $("#cfg-live").value = CFG.LIVE_URL;
  $("#cfg-toggle").onclick = () => { $("#cfg-bar").classList.toggle("hidden"); $("header").classList.toggle("cfg-open"); };
  $("#cfg-save").onclick = () => { localStorage.setItem("api_url", $("#cfg-api").value.trim()); localStorage.setItem("live_url", $("#cfg-live").value.trim()); location.search = ""; };
  $("#cfg-mock").onclick = () => { localStorage.setItem("api_url", ""); localStorage.setItem("live_url", ""); location.search = ""; };
  const showErr = (sel, e) => { $(sel).innerHTML = `<span class="err">${escapeHtml(e.status ? "HTTP " + e.status + ": " : "")}${escapeHtml(e.message)}</span>`; };
  const dayOf = (s) => (s || "").slice(0, 10);
  const nextDay = (day) => new Date(Date.parse(day + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);

  // ------------------------------------------------------------ tabs
  $$("#tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
  function showTab(name) {
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
    $$(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + name));
    if (name === "map" && state.map) state.map.resize();
    if (name === "stats") loadStats();
    if (name === "iot") loadIot();
    Object.values(state.charts).forEach((c) => c.resize());
  }

  // ------------------------------------------------------------ try box
  async function tryUrl(u) {
    $("#try-url").value = u;
    const [path, qs] = u.split("?");
    const params = Object.fromEntries(new URLSearchParams(qs || ""));
    try { $("#try-out").textContent = preview(await api(path, params)); }
    catch (e) { $("#try-out").textContent = `HTTP ${e.status}\n` + preview(e.body || { error: e.message }); }
  }
  $("#try-go").onclick = () => tryUrl($("#try-url").value.trim());
  $$("[data-try]").forEach((a) => a.onclick = (e) => { e.preventDefault(); tryUrl(a.dataset.try); });

  // ------------------------------------------------------------ colour helpers
  const GRID_STOPS = {
    lst_mean: MockAPI.LEGENDS.lst.stops, ndvi_mean: MockAPI.LEGENDS.ndvi.stops, ndbi_mean: MockAPI.LEGENDS.ndbi.stops,
    builtup_pct: MockAPI.LEGENDS.building_density.stops,
    air_temp_mean: [[28, "#2c7bb6"], [30, "#ffffbf"], [32.5, "#d7191c"]],
    db_spl_mean: [[55, "#1a9850"], [65, "#fee08b"], [78, "#d73027"]],
  };
  const TRACK_STOPS = { temp_c_mean: [[27, "#2c7bb6"], [30, "#ffffbf"], [33, "#d7191c"]], db_spl_mean: GRID_STOPS.db_spl_mean, dba_mean: [[50, "#1a9850"], [60, "#fee08b"], [72, "#d73027"]] };
  // legend ของจริง (satellite_grid._legend): stops = [t 0..1, [r,g,b]] + min/max → แปลงเป็น [ค่า, "#hex"]
  const hex = (c) => "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
  function normStops(lg) {
    if (!lg || !lg.stops) return null;
    return lg.stops.map(([t, c]) => (Array.isArray(c) ? [Math.round((lg.min + t * (lg.max - lg.min)) * 100) / 100, hex(c)] : [t, c]));
  }
  const interp = (prop, stops) => ["interpolate", ["linear"], ["get", prop], ...stops.flat()];
  function legendHtml(stops, unit = "") {
    const g = `linear-gradient(90deg, ${stops.map(([, c]) => c).join(",")})`;
    return `<span>${stops[0][0]}${unit}</span><span class="bar" style="background:${g}"></span><span>${stops[stops.length - 1][0]}${unit}</span>`;
  }
  const fmt = (v, d = 1) => (v == null ? "—" : typeof v === "number" ? v.toFixed(d) : v);

  // ------------------------------------------------------------ ② MAP
  const LAYER_NAMES = { lst: "LST อุณหภูมิพื้นผิว", ndvi: "NDVI พืชพรรณ", ndbi: "NDBI สิ่งปลูกสร้าง", building_density: "ความหนาแน่นอาคาร (90 m)" };
  function initMap() {
    const map = new maplibregl.Map({
      container: "map",
      style: {
        version: 8,
        // basemap OSM (ไม่ต้องใช้ key) — CARTO ต้องมี API key แล้ว
        // ภาพถ่ายดาวเทียม: Esri World Imagery (ไม่ต้องใช้ key)
        sources: {
          osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" },
          imagery: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, maxzoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#eef0f2" } },
          { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6, "raster-opacity": 0.9 } },
          { id: "imagery", type: "raster", source: "imagery", layout: { visibility: "none" } },
        ],
      },
      center: [MockAPI.AOI.west + (MockAPI.AOI.east - MockAPI.AOI.west) / 2, MockAPI.AOI.south + (MockAPI.AOI.north - MockAPI.AOI.south) / 2],
      zoom: 13.7,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    $("#basemap").onchange = (e) => {
      map.setLayoutProperty("osm", "visibility", e.target.value === "osm" ? "visible" : "none");
      map.setLayoutProperty("imagery", "visibility", e.target.value === "imagery" ? "visible" : "none");
    };
    map.addControl(new maplibregl.ScaleControl(), "bottom-left");
    state.map = map;
    map.on("load", async () => {
      await loadLayers();
      await loadGrid();
      await loadHotspot();
      await loadDevicesOnMap();
      await loadTrack();
      startLive();
    });
    map.on("click", "grid-hit", (e) => cellPopup(e.features[0].properties.cell_id, e.lngLat));
    map.on("mouseenter", "grid-hit", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "grid-hit", () => (map.getCanvas().style.cursor = ""));
  }

  // /layers → image source ต่อ layer
  async function loadLayers() {
    const map = state.map;
    let res;
    try { res = await api("/layers", { period: state.period && !state.period.startsWith("month_") ? state.period : null }); $("#layer-note").textContent = ""; }
    catch (e) { showErr("#layer-note", e); return; }
    const all = [...res.layers, ...res.static_layers].filter((L) => L.url && L.coordinates);
    for (const L of all) { const st = normStops(L.legend); if (st) { L.stops = st; const g = { lst: "lst_mean", ndvi: "ndvi_mean", ndbi: "ndbi_mean", building_density: "builtup_pct" }[L.layer]; if (g) GRID_STOPS[g] = st; } }
    if (!state.fitted && all.length) { const cs = all[0].coordinates; state.map.fitBounds([[Math.min(...cs.map((c) => c[0])), Math.min(...cs.map((c) => c[1]))], [Math.max(...cs.map((c) => c[0])), Math.max(...cs.map((c) => c[1]))]], { padding: 30, duration: 0 }); state.fitted = true; }
    const box = $("#layer-controls");
    for (const L of all) {
      const id = "img-" + L.layer;
      if (map.getSource(id)) { map.getSource(id).updateImage({ url: L.url, coordinates: L.coordinates }); continue; }
      map.addSource(id, { type: "image", url: L.url, coordinates: L.coordinates });
      const on = L.layer === "lst";
      map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": 0.75, "raster-resampling": "nearest" }, layout: { visibility: on ? "visible" : "none" } });
      const div = document.createElement("div");
      div.innerHTML = `<div class="layer-row"><label class="row"><input type="checkbox" ${on ? "checked" : ""}/> ${LAYER_NAMES[L.layer]}</label><input type="range" min="0" max="100" value="75" title="ความโปร่งใส"/></div>
        <div class="legend">${L.stops ? legendHtml(L.stops, L.legend.unit) : ""}</div>`;
      const [cb, rg] = div.querySelectorAll("input");
      cb.onchange = () => map.setLayoutProperty(id, "visibility", cb.checked ? "visible" : "none");
      rg.oninput = () => map.setPaintProperty(id, "raster-opacity", rg.value / 100);
      box.appendChild(div);
    }
    if (!state.period) fillPeriods(res);
    return res;
  }
  function fillPeriods(res) {
    const sel = $("#period");
    sel.innerHTML = res.periods.map((p) => `<option value="${p.period}">${p.period}</option>`).join("") + (CFG.MOCK ? `<option value="month_2026-11">month_2026-11 (grid/สถิติ)</option>` : "");
    sel.value = res.period; state.period = res.period;
  }

  // /grid → GeoJSON fill
  async function loadGrid() {
    const map = state.map;
    const vars = ["lst_mean", "ndvi_mean", "ndbi_mean", "builtup_pct", "air_temp_mean", "db_spl_mean"].join(",");
    let res;
    try { res = await api("/grid", { period: state.period, vars, match: state.match }); $("#grid-note").textContent = `${res.features.length} เซลล์ · period ${res.period}`; }
    catch (e) { showErr("#grid-note", e); state.grid = null; return; }
    state.grid = res;
    if (!state.fitted && res.features.length) {
      const pts = res.features.flatMap((f) => f.geometry.coordinates[0]);
      map.fitBounds([[Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1]))], [Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))]], { padding: 30, duration: 0 }); state.fitted = true;
    }
    if (map.getSource("grid")) map.getSource("grid").setData(res);
    else {
      map.addSource("grid", { type: "geojson", data: res, promoteId: "cell_id" });
      map.addLayer({ id: "grid-fill", type: "fill", source: "grid", layout: { visibility: "none" }, paint: { "fill-opacity": 0.75 } });
      map.addLayer({ id: "grid-line", type: "line", source: "grid", layout: { visibility: "none" }, paint: { "line-color": "#334155", "line-width": 0.3, "line-opacity": 0.5 } });
      map.addLayer({ id: "grid-hit", type: "fill", source: "grid", paint: { "fill-opacity": 0 } });
    }
    styleGrid();
  }
  function styleGrid() {
    if (!state.map.getLayer("grid-fill")) return;
    const v = $("#grid-var").value, stops = GRID_STOPS[v];
    state.map.setPaintProperty("grid-fill", "fill-color", ["case", ["==", ["get", v], null], "rgba(0,0,0,0)", interp(v, stops)]);
    $("#grid-legend").innerHTML = legendHtml(stops) + (v.includes("air") || v.includes("db_") ? " <small>(เซลล์ว่าง = ไม่มี IoT ≥ 5 นาที)</small>" : "");
  }
  $("#grid-on").onchange = (e) => ["grid-fill", "grid-line"].filter((l) => state.map.getLayer(l)).forEach((l) => state.map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));
  $("#grid-var").onchange = styleGrid;

  async function cellPopup(id, lngLat) {
    const pop = new maplibregl.Popup({ maxWidth: "360px" }).setLngLat(lngLat).setHTML(`<div class="pop">กำลังโหลด <code>/grid/${id}</code>…</div>`).addTo(state.map);
    let res;
    try { res = await api("/grid/" + id, { period: state.period }); } catch (e) { pop.setHTML(`<div class="pop err">${escapeHtml(e.message)}</div>`); return; }
    res.match.all ||= {}; res.match.window ||= {};
    const keys = ["lst_mean", "lst_p90", "ndvi_mean", "ndbi_mean", "builtup_pct", "building_count", "floors_mean", "far", "in_cover", "bldg_source", "air_temp_mean", "humidity_mean", "db_spl_mean", "lux_mean", "n_iot_minutes"];
    const rows = keys.map((k) => `<tr><td>${k}</td><td>${fmt(res.match.all[k], 2)}</td><td>${fmt(res.match.window[k], 2)}</td></tr>`).join("");
    pop.setHTML(`<div class="pop"><b>${res.cell_id}</b> · ${res.period}<br><small>GET /grid/${id} → curated.cell_period</small>
      <table><tr><td></td><td><b>all</b></td><td><b>window</b></td></tr>${rows}</table></div>`);
  }

  // /analytics/hotspot + geometry จาก /grid
  async function loadHotspot() {
    const map = state.map;
    if (!CFG.MOCK || !state.grid) { $("#hot-note").textContent = !CFG.MOCK ? "ยังไม่มีบน API จริง (รอ #33) จึงไม่แสดง" : ""; $("#hot-on").disabled = !CFG.MOCK; return; }
    const res = await api("/analytics/hotspot", { period: state.period, match: state.match });
    const geom = Object.fromEntries(state.grid.features.map((f) => [f.id, f.geometry]));
    const fc = { type: "FeatureCollection", features: res.cells.filter((c) => c.class !== "not_significant").map((c) => ({ type: "Feature", geometry: geom[c.cell_id], properties: c })) };
    if (map.getSource("hot")) map.getSource("hot").setData(fc);
    else {
      map.addSource("hot", { type: "geojson", data: fc });
      map.addLayer({ id: "hot-fill", type: "fill", source: "hot", layout: { visibility: "none" }, paint: { "fill-opacity": 0.7, "fill-color": ["match", ["get", "class"], "hot_99", "#b2182b", "hot_95", "#ef8a62", "cold_95", "#67a9cf", "cold_99", "#2166ac", "#ccc"] } }, "grid-hit");
    }
  }
  $("#hot-on").onchange = (e) => state.map.getLayer("hot-fill") && state.map.setLayoutProperty("hot-fill", "visibility", e.target.checked ? "visible" : "none");

  // /readings → เส้นทางเดิน
  async function loadTrack() {
    const map = state.map;
    const day = $("#track-date").value, dev = $("#track-dev").value;
    if (!dev) { $("#track-info").textContent = "ไม่มีเครื่องแบบ Mobile ใน /devices"; return; }
    let res;
    try { res = await api("/readings", { device: dev, from: day + "T00:00:00", to: nextDay(day) + "T00:00:00", interval: "1min" }); }
    catch (e) { showErr("#track-info", e); return; }
    const rows = toObjects(res).filter((r) => r.lat != null);
    const fc = { type: "FeatureCollection", features: rows.map((r) => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: r })) };
    const line = { type: "Feature", geometry: { type: "LineString", coordinates: rows.map((r) => [r.lon, r.lat]) }, properties: {} };
    $("#track-info").innerHTML = `${res.rows.length} แถวจาก <code>readings_1min</code> (${rows.length} แถวมีพิกัด)` + (res.rows.length ? "" : " — วันนี้เครื่องนี้ไม่มีข้อมูล");
    if (map.getSource("track")) { map.getSource("track").setData(fc); map.getSource("track-line").setData(line); }
    else {
      map.addSource("track-line", { type: "geojson", data: line });
      map.addSource("track", { type: "geojson", data: fc });
      map.addLayer({ id: "track-line", type: "line", source: "track-line", paint: { "line-color": "#111827", "line-width": 1, "line-opacity": 0.5 } });
      map.addLayer({ id: "track-pts", type: "circle", source: "track", paint: { "circle-radius": 3.5, "circle-stroke-color": "#fff", "circle-stroke-width": 0.6 } });
      map.on("click", "track-pts", (e) => {
        const p = e.features[0].properties;
        new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<div class="pop"><b>${p.minute_local}</b><br><small>/readings 1 แถว</small><table>
          ${["db_spl_mean", "dba_mean", "temp_c_mean", "humidity_pct_mean", "lux_mean", "n_rows", "cell_id"].map((k) => `<tr><td>${k}</td><td>${p[k]}</td></tr>`).join("")}</table></div>`).addTo(map);
      });
    }
    styleTrack();
    if (!state.fitted && rows.length) {
      map.fitBounds([[Math.min(...rows.map((r) => r.lon)), Math.min(...rows.map((r) => r.lat))], [Math.max(...rows.map((r) => r.lon)), Math.max(...rows.map((r) => r.lat))]], { padding: 60, maxZoom: 16, duration: 0 }); state.fitted = true;
    }
  }
  function styleTrack() {
    const v = $("#track-color").value;
    state.map.setPaintProperty("track-pts", "circle-color", interp(v, TRACK_STOPS[v]));
    $("#track-legend").innerHTML = legendHtml(TRACK_STOPS[v]);
  }
  $("#track-color").onchange = styleTrack;
  $("#track-dev").onchange = () => { const d = (state.devices || []).find((x) => x.device_id === $("#track-dev").value); if (d && d.latest_hour_local) $("#track-date").value = dayOf(d.latest_hour_local); loadTrack(); };
  $("#track-date").onchange = loadTrack;
  $("#track-on").onchange = (e) => ["track-pts", "track-line"].filter((l) => state.map.getLayer(l)).forEach((l) => state.map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));

  // /devices → หมุด station
  async function loadDevicesOnMap() {
    let res;
    try { res = await api("/devices"); } catch (e) { showErr("#live-note", e); return; }
    state.devices = res.devices;
    const mobiles = res.devices.filter((x) => x.device_type !== "Stationary");
    $("#track-dev").innerHTML = mobiles.map((d) => `<option value="${d.device_id}">${d.device_id}${d.latest_hour_local ? "" : " (ยังไม่มีข้อมูล)"}</option>`).join("");
    const first = mobiles.find((d) => d.latest_hour_local) || mobiles[0];
    if (first) { $("#track-dev").value = first.device_id; if (first.latest_hour_local) $("#track-date").value = dayOf(first.latest_hour_local); }
    for (const d of res.devices.filter((x) => x.device_type === "Stationary" && x.fixed_lat != null)) {
      const el = document.createElement("div"); el.className = "pin"; el.title = d.site_name;
      new maplibregl.Marker({ element: el }).setLngLat([d.fixed_lon, d.fixed_lat]).addTo(state.map);
      el.onclick = (ev) => { ev.stopPropagation(); stationPopup(d); };
    }
  }
  async function stationPopup(d) {
    const div = document.createElement("div"); div.className = "pop"; div.style.width = "300px";
    div.innerHTML = `<b>${d.device_id}</b> · ${d.site_name}<br><small>GET /latest + /readings (1 วัน)</small><div class="kv small" id="sp-kv">…</div><div style="height:150px" id="sp-chart"></div>`;
    new maplibregl.Popup({ maxWidth: "320px" }).setLngLat([d.fixed_lon, d.fixed_lat]).setDOMContent(div).addTo(state.map);
    let latest;
    try { latest = await api("/latest", { device: d.device_id }); } catch (e) { div.querySelector("#sp-kv").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`; return; }
    const r = latest.row;
    div.querySelector("#sp-kv").innerHTML = `<span>เวลา</span><b>${r.minute_local}</b><span>อุณหภูมิ</span><b>${r.temp_c_mean} °C</b><span>ความชื้น</span><b>${r.humidity_pct_mean} %</b><span>dB(SPL)</span><b>${r.db_spl_mean}</b>`;
    const day = dayOf(r.minute_local);
    const res = await api("/readings", { device: d.device_id, from: day + "T00:00:00", to: nextDay(day) + "T00:00:00", interval: "1min" });
    const rows = toObjects(res).filter((_, i) => i % 10 === 0);
    const c = echarts.init(div.querySelector("#sp-chart"));
    c.setOption({ grid: { left: 34, right: 8, top: 20, bottom: 20 }, title: { text: "temp_c_mean " + day, textStyle: { fontSize: 11 } }, tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: rows.map((r) => r.minute_local.slice(11, 16)) }, yAxis: { type: "value", scale: true },
      series: [{ type: "line", data: rows.map((r) => r.temp_c_mean), showSymbol: false, lineStyle: { color: "#7c3aed" } }] });
  }

  // ------------------------------------------------------------ realtime (ws /live)
  function startLive() {
    if (!CFG.MOCK && !CFG.LIVE_URL) { $("#live-note").textContent = "ไม่ได้ใส่ hub_live_url จึงไม่มีค่าสด"; state.ws = null; return; }
    const url = CFG.MOCK ? "mock://live?device=all" : CFG.LIVE_URL + (CFG.LIVE_URL.includes("?") ? "&" : "?") + "device=all";
    let ws;
    try { ws = CFG.MOCK ? new MockAPI.FakeLiveSocket(url) : new WebSocket(url); }
    catch (e) { $("#live-note").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`; return; }
    ws.onerror = () => { $("#live-note").innerHTML = `<span class="err">ต่อ ${escapeHtml(url)} ไม่ได้ (EC2 อาจปิดอยู่ — Learner Lab ปิดทุก ~4 ชม.)</span>`; };
    ws.onopen = () => { $("#live-note").textContent = "ต่อ " + url + " แล้ว"; };
    ws.binaryType = "arraybuffer";
    state.ws = ws; state.liveMarkers = {};
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) return onAudioFrame(ev.data);
      const m = JSON.parse(ev.data);
      if (m.t !== "audio_stats") state.lastWs = m;
      const L = (state.live[m.device_id] ||= { online: false });
      if (m.t === "status") L.online = m.online;
      if (m.t === "reading") { Object.assign(L, m, { online: true }); moveLiveMarker(m); }
      if (m.t === "audio_stats") onAudioStats(m);
      renderLive();
    };
  }
  function moveLiveMarker(m) {
    if (!state.map || m.lat == null) return;
    let mk = state.liveMarkers[m.device_id];
    if (!mk) {
      const el = document.createElement("div"); el.className = "live-dot"; el.title = m.device_id + " (realtime)";
      mk = state.liveMarkers[m.device_id] = new maplibregl.Marker({ element: el }).setLngLat([m.lon, m.lat]).addTo(state.map);
      if (!$("#live-on").checked) el.style.display = "none";
    }
    mk.setLngLat([m.lon, m.lat]);
  }
  $("#live-on").onchange = (e) => Object.values(state.liveMarkers || {}).forEach((mk) => (mk.getElement().style.display = e.target.checked ? "" : "none"));
  function renderLive() {
    $("#live-mini").innerHTML = Object.entries(state.live).map(([id, L]) =>
      `<div class="lv"><span class="dot ${L.online ? "on" : "off"}"></span><b>${id}</b> ${L.online ? `${fmt(L.temp_c)} °C · ${fmt(L.db_spl)} dB(SPL) · ${L.ts ? L.ts.slice(11) : ""}` : "offline"}</div>`).join("");
    const id = $("#iot-dev").value; const L = state.live[id];
    if (!L) { $("#live-big").innerHTML = `<p class="note">ยังไม่มีข้อความของ ${id}</p>`; return; }
    if (!L.online || !L.ts) { $("#live-big").innerHTML = `<p><span class="dot off"></span>${id} offline (hub ส่ง <code>{"t":"status","online":false}</code>)</p>`; return; }
    $("#live-big").innerHTML = `<div><span class="dot on"></span>${id} · ${L.ts}</div><div class="big">${fmt(L.db_spl)} dB(SPL)</div>
      <div class="kv small"><span>dBA</span><b>${fmt(L.dba)}</b><span>อุณหภูมิ</span><b>${fmt(L.temp_c)} °C</b><span>ความชื้น</span><b>${fmt(L.humidity_pct)} %</b><span>แสง</span><b>${fmt(L.lux, 0)} lux</b>
      <span>GPS</span><b>${fmt(L.lat, 5)}, ${fmt(L.lon, 5)}</b><span>heap / rssi</span><b>${L.health.heap_free} B · ${L.health.rssi} dBm</b></div>`;
    if (state.audio) state.audio.gain.gain.value = Math.min(0.35, Math.max(0.01, (L.db_spl - 50) / 120));
  }

  // เสียงสด (spec 90): ส่ง listen → ได้ binary frame
  function onAudioFrame(buf) {
    const dv = new DataView(buf);
    const f = { type: dv.getUint8(0), version: dv.getUint8(1), samples: dv.getUint16(2, true), session: dv.getUint32(4, true), frame_seq: dv.getUint32(8, true), bytes: buf.byteLength };
    const A = state.audioStat ||= { frames: 0, lost: 0, last: null };
    A.frames++; A.last = f;
    // ของจริง: ส่งไบต์ ADPCM เข้า AudioWorklet ถอดเป็น PCM 16 kHz แล้วเล่นผ่าน jitter buffer 300–500 ms
    if (A.frames % 5 === 0) renderAudio();
  }
  function onAudioStats(m) { const A = state.audioStat ||= { frames: 0, lost: 0 }; A.lost = m.lost; renderAudio(); }
  function renderAudio() {
    const A = state.audioStat; if (!A || !A.last) return;
    $("#audio-stats").innerHTML = `<span>frame ล่าสุด</span><b>type=0x0${A.last.type} v${A.last.version} samples=${A.last.samples} seq=${A.last.frame_seq} (${A.last.bytes} B)</b>
      <span>รับแล้ว</span><b>${A.frames} frame (~10/วินาที ≈ 8 KB/s)</b><span>audio_stats.lost</span><b class="${A.lost > 5 ? "warn" : ""}">${A.lost}${A.lost > 5 ? " สัญญาณขาด" : ""}</b>`;
  }
  $("#listen").onclick = () => {
    const id = $("#iot-dev").value, b = $("#listen");
    if (!state.ws || state.ws.readyState !== 1) { $("#audio-status").textContent = "ยังไม่ได้ต่อ /live"; return; }
    if (!state.audio) {
      state.ws.send(JSON.stringify({ t: "listen", device_id: id }));
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const len = ctx.sampleRate * 2, buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
      let last = 0; for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = last * 3.5; } // brown noise
      const src = ctx.createBufferSource(); src.buffer = buf; src.loop = true; const gain = ctx.createGain(); gain.gain.value = 0.1;
      src.connect(gain).connect(ctx.destination); src.start();
      state.audio = { ctx, src, gain, id }; state.audioStat = { frames: 0, lost: 0 };
      b.textContent = "■ หยุดฟัง"; b.classList.add("stop");
      $("#audio-status").innerHTML = `ส่ง <code>{"t":"listen","device_id":"${id}"}</code> แล้ว`;
    } else {
      state.ws.send(JSON.stringify({ t: "unlisten", device_id: state.audio.id }));
      state.audio.src.stop(); state.audio.ctx.close(); state.audio = null;
      b.textContent = "▶ ฟังเสียงสด"; b.classList.remove("stop");
      $("#audio-status").innerHTML = `ส่ง <code>{"t":"unlisten"}</code> แล้ว — ไม่มีผู้ฟัง 30 วินาที hub จะส่ง <code>audio_off</code>`;
    }
  };

  // ------------------------------------------------------------ ③ STATS
  function chart(id) { return (state.charts[id] ||= echarts.init(document.getElementById(id))); }
  const LABEL = { lst_mean: "LST", ndvi_mean: "NDVI", ndbi_mean: "NDBI", builtup_pct: "builtup", air_temp_mean: "air_temp", db_spl_mean: "dB(SPL)" };
  let statsKey = null;
  async function loadStats(force) {
    const key = state.period + "|" + state.match;
    if (!force && key === statsKey) return; statsKey = key;
    const q = { period: state.period, match: state.match };
    const [sum, cor, reg, imp, grp, sac, s9, grid] = await Promise.all([
      api("/analytics/summary", q), api("/analytics/correlation", q), api("/analytics/regression", q), api("/analytics/importance", q),
      api("/analytics/groups", q), api("/analytics/spatial_autocorr", q), api("/analytics/station_diurnal", q),
      api("/grid", { period: state.period, match: state.match, vars: "lst_mean,ndvi_mean,ndbi_mean,builtup_pct,air_temp_mean" }).catch(() => null),
    ]);
    // S1 + ข้อความสรุป (สร้างจากผล correlation)
    const li = cor.variables.indexOf("lst_mean");
    let best = null; cor.variables.forEach((v, i) => { if (i !== li && v !== "db_spl_mean" && (!best || Math.abs(cor.r[li][i]) > Math.abs(best.r))) best = { v, r: cor.r[li][i] }; });
    $("#headline").textContent = `${LABEL[best.v]} สัมพันธ์กับ LST สูงสุด (r = ${best.r.toFixed(2)}) · พื้นที่หนาแน่นร้อนกว่าพื้นที่เขียว ${grp.diff_dense_minus_green.toFixed(1)} °C`;
    const unit = { lst_mean: "°C", air_temp_mean: "°C", builtup_pct: "%", db_spl_mean: "dB" };
    $("#s1-cards").innerHTML = sum.rows.map((r) => `<div class="kpi"><div class="l">${LABEL[r.variable]} เฉลี่ย (n=${r.n})</div><div class="v">${fmt(r.mean, 2)} ${unit[r.variable] || ""}</div><div class="l">SD ${fmt(r.sd, 2)} · ${fmt(r.min, 2)}–${fmt(r.max, 2)} · p90 ${fmt(r.p90, 2)}</div></div>`).join("");
    // S2
    const hm = []; cor.r.forEach((row, i) => row.forEach((v, j) => hm.push([j, i, v])));
    chart("c-corr").setOption({ tooltip: { formatter: (p) => `${LABEL[cor.variables[p.value[1]]]} × ${LABEL[cor.variables[p.value[0]]]}<br>r = ${p.value[2]}<br>p = ${cor.p_value[p.value[1]][p.value[0]]} · n = ${cor.n[p.value[1]][p.value[0]]}` },
      grid: { left: 70, right: 70, top: 10, bottom: 50 }, xAxis: { type: "category", data: cor.variables.map((v) => LABEL[v]), axisLabel: { interval: 0 } }, yAxis: { type: "category", data: cor.variables.map((v) => LABEL[v]), axisLabel: { interval: 0 } },
      visualMap: { min: -1, max: 1, calculable: true, orient: "vertical", right: 0, top: "center", inRange: { color: ["#2166ac", "#f7f7f7", "#b2182b"] } },
      series: [{ type: "heatmap", data: hm, label: { show: true, formatter: (p) => p.value[2].toFixed(2), fontSize: 10 } }] }, true);
    // scatter
    state.scGrid = grid; drawScatter();
    // S3 vs S5
    const s3 = reg.models.find((m) => m.model === "s3"), s4 = reg.models.find((m) => m.model === "s4");
    chart("c-coef").setOption({ tooltip: { trigger: "axis" }, legend: { top: 0 }, grid: { left: 50, right: 10, top: 30, bottom: 30 },
      xAxis: { type: "category", data: s3.terms.map((t) => LABEL[t.term]) }, yAxis: { type: "value" },
      series: [{ name: "S3 |std coef|", type: "bar", data: s3.terms.map((t) => Math.abs(t.std_coef)), itemStyle: { color: "#0f766e" } },
        { name: "S5 RF importance", type: "bar", data: s3.terms.map((t) => (imp.features.find((f) => f.feature === t.term) || {}).importance), itemStyle: { color: "#f59e0b" } }] }, true);
    const vifWarn = s3.terms.filter((t) => t.vif > 5);
    $("#s3-note").innerHTML = `S3: <code>${s3.formula}</code> R² = ${s3.r2} (n=${s3.n}) · ` + s3.terms.map((t) => `${LABEL[t.term]} β*=${t.std_coef} VIF=${t.vif}`).join(", ") +
      (vifWarn.length ? ` <span class="warn">VIF > 5: ${vifWarn.map((t) => LABEL[t.term]).join(", ")} ซ้อนกัน</span>` : "") +
      `<br>S4: <code>${s4.formula}</code> R² = ${s4.r2 ?? "—"} (n=${s4.n}) ${s4.note || ""}`;
    // S8
    chart("c-groups").setOption({ tooltip: {}, grid: { left: 50, right: 10, top: 20, bottom: 30 }, xAxis: { type: "category", data: grp.groups.map((g) => `${g.group} (NDVI ${g.ndvi_range.join("–")})`) },
      yAxis: { type: "value", scale: true, name: "LST °C" },
      series: [{ type: "bar", data: grp.groups.map((g) => g.lst_mean), itemStyle: { color: (p) => ["#1a9641", "#fdae61", "#d7191c"][p.dataIndex] }, label: { show: true, position: "top", formatter: (p) => p.value.toFixed(2) + " °C" } }] }, true);
    // S7
    $("#s7").innerHTML = sac.rows.map((r) => `<span>Moran's I (${r.target})</span><b>${r.morans_i} (p=${r.p_value})</b>`).join("") + `<span>R² ของ S3</span><b>${s3.r2}</b><span>R² ของ S4</span><b>${s4.r2 ?? "—"}</b>`;
    // S9
    chart("c-s9").setOption({ tooltip: { trigger: "axis" }, legend: { top: 0 }, grid: { left: 40, right: 10, top: 30, bottom: 30 },
      xAxis: { type: "category", data: [...Array(24).keys()].map((h) => h + ":00") }, yAxis: { type: "value", scale: true, name: "°C" },
      series: s9.stations.map((s) => ({ name: `${s.device_id} ${s.site_name}`, type: "line", smooth: true, data: s.hours.map((h) => h.air_temp_mean) })) }, true);
  }
  function drawScatter() {
    const g = state.scGrid; if (!g) { chart("c-scatter").setOption({ title: { text: "/grid ไม่มีข้อมูล", left: "center", top: "middle" } }, true); return; }
    const sel = $("#sc-x").value;
    const [xk, yk] = sel === "air" ? ["lst_mean", "air_temp_mean"] : [sel, "lst_mean"];
    const pts = g.features.map((f) => f.properties).filter((p) => p[xk] != null && p[yk] != null).map((p) => [p[xk], p[yk]]);
    // เส้นแนวโน้มอย่างง่ายเพื่อแสดงผล (ตัวเลข regression จริงมาจาก /analytics/regression)
    if (!pts.length) { chart("c-scatter").setOption({ title: { text: "ไม่มีเซลล์ที่มีทั้ง " + LABEL[xk] + " และ " + LABEL[yk], left: "center", top: "middle", textStyle: { fontSize: 12 } } }, true); return; }
    const n = pts.length, mx = pts.reduce((s, p) => s + p[0], 0) / n, my = pts.reduce((s, p) => s + p[1], 0) / n;
    const b = pts.reduce((s, p) => s + (p[0] - mx) * (p[1] - my), 0) / pts.reduce((s, p) => s + (p[0] - mx) ** 2, 0), a = my - b * mx;
    const xs = pts.map((p) => p[0]), x0 = Math.min(...xs), x1 = Math.max(...xs);
    chart("c-scatter").setOption({ tooltip: {}, grid: { left: 50, right: 10, top: 20, bottom: 40 },
      xAxis: { type: "value", scale: true, name: LABEL[xk], nameLocation: "middle", nameGap: 25 }, yAxis: { type: "value", scale: true, name: LABEL[yk] },
      series: [{ type: "scatter", data: pts, symbolSize: 4, itemStyle: { color: "#0f766e", opacity: 0.5 } },
        { type: "line", data: [[x0, a + b * x0], [x1, a + b * x1]], showSymbol: false, lineStyle: { color: "#b91c1c", width: 2 } }],
      title: { text: `n = ${n} เซลล์ · slope = ${b.toFixed(2)}`, right: 10, top: 0, textStyle: { fontSize: 11, color: "#6b7785" } } }, true);
  }
  $("#sc-x").onchange = drawScatter;

  // ------------------------------------------------------------ ④ IOT
  let iotKey = null;
  async function loadIot(force) {
    if (!state.devices) { try { state.devices = (await api("/devices")).devices; } catch (e) { $("#c-spl").innerHTML = `<p class="err">${escapeHtml(e.message)}</p>`; return; } }
    const sel = $("#iot-dev");
    if (!sel.options.length) {
      sel.innerHTML = state.devices.map((d) => `<option value="${d.device_id}">${d.device_id} (${d.device_type}${d.site_name ? " · " + d.site_name : ""})</option>`).join("");
      const first = state.devices.find((d) => d.latest_hour_local) || state.devices[0];
      if (first) { sel.value = first.device_id; if (first.latest_hour_local) $("#iot-date").value = dayOf(first.latest_hour_local); }
    }
    const dev = sel.value, day = $("#iot-date").value; const key = dev + day + $("#iot-dba").checked;
    if (!force && key === iotKey) return; iotKey = key;
    const range = { device: dev, from: day + "T00:00:00", to: nextDay(day) + "T00:00:00" };
    const [rd, ql] = await Promise.all([
      api("/readings", Object.assign({ interval: "1min" }, range)).catch((e) => ({ columns: [], rows: [], _err: e })),
      api("/quality", range).catch((e) => ({ hours: [], _err: e })),
    ]);
    const rows = toObjects(rd); const x = rows.map((r) => r.minute_local.slice(11, 16));
    const empty = rd._err ? "error: " + rd._err.message : rows.length ? "" : `ไม่มีข้อมูล ${dev} วันที่ ${day}`;
    const base = (name) => ({ title: { text: empty, left: "center", top: "middle", textStyle: { fontSize: 12, color: "#999" } }, tooltip: { trigger: "axis" }, grid: { left: 45, right: 10, top: 25, bottom: 25 }, xAxis: { type: "category", data: x }, yAxis: { type: "value", scale: true, name }, dataZoom: [{ type: "inside" }] });
    const spl = [
      { name: "min", type: "line", data: rows.map((r) => r.db_spl_min), stack: "band", lineStyle: { opacity: 0 }, showSymbol: false },
      { name: "max-min", type: "line", data: rows.map((r) => r.db_spl_max - r.db_spl_min), stack: "band", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(15,118,110,.15)" }, showSymbol: false, tooltip: { show: false } },
      { name: "db_spl_mean", type: "line", data: rows.map((r) => r.db_spl_mean), showSymbol: false, lineStyle: { color: "#0f766e" } },
    ];
    if ($("#iot-dba").checked) spl.push({ name: "dba_mean", type: "line", data: rows.map((r) => r.dba_mean), showSymbol: false, lineStyle: { color: "#f59e0b" } });
    chart("c-spl").setOption(Object.assign(base("dB"), { legend: { top: 0, data: ["db_spl_mean", "dba_mean"] }, series: spl }), true);
    chart("c-temp").setOption(Object.assign(base("°C"), { series: [{ type: "line", data: rows.map((r) => r.temp_c_mean), showSymbol: false, lineStyle: { color: "#d7191c" } }] }), true);
    chart("c-hum").setOption(Object.assign(base("%"), { series: [{ type: "line", data: rows.map((r) => r.humidity_pct_mean), showSymbol: false, lineStyle: { color: "#2c7bb6" } }] }), true);
    chart("c-lux").setOption(Object.assign(base("lux"), { series: [{ type: "line", data: rows.map((r) => r.lux_mean), showSymbol: false, lineStyle: { color: "#eab308" } }] }), true);
    chart("c-quality").setOption({ tooltip: { trigger: "axis", formatter: (ps) => { const h = ql.hours[ps[0].dataIndex]; return `${h.batch_id}<br>loss_rate ${(h.loss_rate * 100).toFixed(2)}%<br>expected ${h.expected_rows} · missing ${h.missing_rows}<br>duplicate ${h.duplicate_rows} · invalid ${h.invalid_rows} · sessions ${h.sessions}`; } },
      grid: { left: 45, right: 10, top: 20, bottom: 25 }, xAxis: { type: "category", data: ql.hours.map((h) => h.hour_local.slice(11, 13) + ":00") },
      yAxis: { type: "value", name: "loss %", axisLabel: { formatter: (v) => v + "%" } },
      series: [{ type: "bar", data: ql.hours.map((h) => +(h.loss_rate * 100).toFixed(2)), itemStyle: { color: (p) => (p.value > 2 ? "#dc2626" : "#16a34a") } }] }, true);
    renderLive();
  }
  $("#iot-dev").onchange = () => { const d = state.devices.find((x) => x.device_id === $("#iot-dev").value); if (d && d.latest_hour_local) $("#iot-date").value = dayOf(d.latest_hour_local); loadIot(); renderLive(); };
  $("#iot-date").onchange = () => loadIot();
  $("#iot-dba").onchange = () => loadIot();

  // ------------------------------------------------------------ globals
  $("#period").onchange = async (e) => { state.period = e.target.value; await refreshPeriod(); };
  $("#match").onchange = async (e) => { state.match = e.target.value; await refreshPeriod(); };
  async function refreshPeriod() {
    if (state.map) { await loadLayers(); await loadGrid(); await loadHotspot(); }
    if ($("#tab-stats").classList.contains("active")) loadStats(true); else statsKey = null;
  }
  window.addEventListener("resize", () => Object.values(state.charts).forEach((c) => c.resize()));

  initMap();
  if (!CFG.MOCK) $("#try-url").value = "/devices";
  tryUrl($("#try-url").value);
  window._app = { state, api, showTab }; // ไว้ลองใน DevTools console
})();
