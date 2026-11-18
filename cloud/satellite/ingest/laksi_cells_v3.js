// ============================================================
// เขตหลักสี่: data layer ราย cell (LST, NDVI, NDBI, อาคาร, ถนน)
// v3 — แก้ไขจาก review:
//   [fix] cell_id format ตรงกับ grid.py: c0000_r0012 (มี 'r' + zero-pad 4 หลัก)
//   [fix] Fixed Origin (660015, 1545015) ตรงกับ grid.py ORIGIN_X/Y
//   [fix] ตัด raw.first() transform ออก (ไม่จำเป็นเมื่อใช้ Fixed Origin)
//   [fix] นับอาคาร (n_bldg) ด้วย reduceToImage+sum แทน NPIX×mean (แม่นกว่า)
//   [fix] floors / FAR ใช้ sum reducer ถูกต้อง
// !! ยืนยัน ORIGIN_X/Y กับ spike #26 ก่อน export จริง !!
// ============================================================

// ---------- 0. ข้อมูลนำเข้า ----------
var BASE  = 'projects/turnkey-tribute-511903-t6/assets/';
var laksi = ee.FeatureCollection(BASE + 'laksi_boundary');
var bld   = ee.FeatureCollection(BASE + 'building');
var roi   = laksi.geometry();

var ROAD_ASSET   = null;   // ใส่ asset path เมื่อมีข้อมูลถนน
var ROAD_IS_LINE = true;
var ROAD_WIDTH_M = 8;

// ---------- 1. ตั้งค่า ----------
var CELL      = 90;              // เมตร (ต้องหาร 30 ลงตัว)
var CRS       = 'EPSG:32647';   // UTM 47N — ตรงกับ grid.py
var VEC_SCALE = 5;              // ความละเอียดราสเตอร์อาคาร (เมตร)

// Fixed Origin — ต้องตรงกับ grid.py ORIGIN_X / ORIGIN_Y
// ยืนยันว่า (ORIGIN_X - tr[2]) % 30 === 0 หลัง spike #26
var ORIGIN_X = 660015.0;
var ORIGIN_Y = 1545015.0;

var START_YEAR  = 2021;
var END_YEAR    = 2025;
var SUMMER_ONLY = true;   // true = มี.ค.–พ.ค. เท่านั้น

Map.centerObject(roi, 13);

// ---------- 2. Landsat 8+9: ลบเมฆ, คำนวณ LST / NDVI / NDBI ----------
function prepScene(img) {
  var qa = img.select('QA_PIXEL');
  // bit 1-4: dilated cloud(2), cirrus(4), cloud(8), cloud shadow(16)
  // bitwiseAnd(30) = bitwiseAnd(0b00011110) ตรวจทั้ง 4 บิต
  var clear = qa.bitwiseAnd(30).eq(0);

  // LST: Landsat C2 L2 scale factor
  var lst = img.select('ST_B10')
    .multiply(0.00341802).add(149.0).subtract(273.15)
    .rename('lst_c');

  // Surface Reflectance scale factor
  var sr   = img.select(['SR_B4','SR_B5','SR_B6']).multiply(0.0000275).add(-0.2);
  var red  = sr.select('SR_B4');
  var nir  = sr.select('SR_B5');
  var swir = sr.select('SR_B6');

  var ndvi = nir.subtract(red).divide(nir.add(red)).rename('ndvi');
  var ndbi = swir.subtract(nir).divide(swir.add(nir)).rename('ndbi');

  return lst.addBands([ndvi, ndbi])
    .updateMask(clear)
    .copyProperties(img, ['system:time_start']);
}

var raw = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
  .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
  .filterBounds(roi)
  .filterDate(START_YEAR + '-01-01', (END_YEAR + 1) + '-01-01');

if (SUMMER_ONLY) {
  raw = raw.filter(ee.Filter.calendarRange(3, 5, 'month'));
}

var col  = raw.map(prepScene);
// composite = median ทุกฉากที่ผ่านเมฆ + จำนวนฉากที่ใช้ต่อ pixel
var comp = col.median()
  .addBands(col.select('lst_c').count().rename('n_valid'))
  .clip(roi);   // clip ไว้เพื่อแสดงผลบนแผนที่เท่านั้น

print('จำนวนภาพที่ใช้ (ก่อนลบเมฆ)', col.size());

// ---------- 3. สร้าง grid จาก Fixed Origin (ตรงกับ grid.py) ----------
// ORIGIN คือมุม NW ของกริดทั้งระบบ — ห้ามเปลี่ยนหลังมีข้อมูลจริง
// col นับจาก W→E, row นับจาก N→S (เหมือน grid.py)

var bb   = roi.bounds(1, CRS).coordinates().get(0).getInfo();
var xs   = bb.map(function(p) { return p[0]; });
var ys   = bb.map(function(p) { return p[1]; });
var xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
print('ขอบเขต UTM (x ≈ 666xxx–672xxx, y ≈ 152xxxx–154xxxx)', xmin, xmax, ymin, ymax);

// col/row ของ cell แรกที่ครอบ AOI (อาจ < 0 ถ้า AOI อยู่ซ้าย/เหนือ origin)
var col0 = Math.floor((xmin - ORIGIN_X) / CELL);
var row0 = Math.floor((ORIGIN_Y - ymax) / CELL);
var col1 = Math.ceil( (xmax - ORIGIN_X) / CELL);
var row1 = Math.ceil( (ORIGIN_Y - ymin) / CELL);

var feats = [];
for (var c = col0; c < col1; c++) {
  for (var r = row0; r < row1; r++) {
    var x = ORIGIN_X + c * CELL;
    var y = ORIGIN_Y - r * CELL;   // y ลดลงตาม row (ลงใต้)

    // cell_id: c0000_r0012 — ตรงกับ grid.py make_cell_id(col, row)
    var cid = 'c' + String(c).padStart(4, '0') +
              '_r' + String(r).padStart(4, '0');

    feats.push(ee.Feature(
      ee.Geometry.Rectangle([x, y - CELL, x + CELL, y], CRS, false),
      {cell_id: cid}
    ));
  }
}

var cells = ee.FeatureCollection(feats)
  .filterBounds(roi)
  .map(function(cell) {
    // เก็บเฉพาะ cell ที่จุดกึ่งกลางอยู่ในเขต
    var inside = roi.contains(cell.geometry().centroid(1), 1);
    return cell.set('inside', ee.Algorithms.If(inside, 1, 0));
  })
  .filter(ee.Filter.eq('inside', 1))
  .select(['cell_id']);

print('จำนวน cell ในเขตหลักสี่', cells.size());

// ---------- 4. Layer อาคาร: coverage, จำนวน, ชั้น, FAR ----------

// 4a. Coverage ratio (สัดส่วนพื้นที่) — paint polygon → mean ที่ 5m
function paintMask(fc, name) {
  return ee.Image().byte().paint(fc, 1).unmask(0).rename(name);
}

var bldMask  = paintMask(bld, 'bldg_r');
var bldCover = paintMask(                           // in_cover flag
  bld.map(function(f) { return f.buffer(100, 5); }),
  'cov_r'
);

// Material coverage (BL_MATL)
var matBands = [0, 1, 2, 3, 8, 9].map(function(code) {
  return paintMask(bld.filter(ee.Filter.eq('BL_MATL', code)),
                   'mat' + code + '_r');
});

var coverageStack = ee.Image.cat([bldMask, bldCover].concat(matBands));

// 4b. Count, floors, FAR จาก centroid — ใช้ reduceToImage+sum (ถูกต้องกว่า NPIX×mean)
var centroids = bld.map(function(f) {
  var fl   = ee.Number(f.get('BL_NSTOREY'));
  var area = ee.Number(f.get('BL_AREA'));
  var flOk = fl.gt(0);                           // 0 = ไม่ทราบ → ไม่นับ
  return ee.Feature(f.geometry().centroid(1), {
    cnt:    1,
    fl_ok:  flOk,                                // 1 ถ้ารู้จำนวนชั้น
    fl_sum: fl.multiply(flOk),                   // ผลรวมชั้น (เฉพาะที่รู้)
    gfa:    area.multiply(fl.multiply(flOk))     // GFA = area × floors
  });
});

// image แต่ละแบนด์ = sum ของ centroid ที่ตกใน pixel 5m
// reduceRegions ด้วย sum ที่ scale 5m → ผลรวมใน cell (แม่นกว่า mean×NPIX)
var cntImg  = centroids.reduceToImage(['cnt'],    ee.Reducer.sum()).unmask(0);
var flvImg  = centroids.reduceToImage(['fl_ok'],  ee.Reducer.sum()).unmask(0);
var flsImg  = centroids.reduceToImage(['fl_sum'], ee.Reducer.sum()).unmask(0);
var gfaImg  = centroids.reduceToImage(['gfa'],    ee.Reducer.sum()).unmask(0);
var cntStack = ee.Image.cat([cntImg, flvImg, flsImg, gfaImg])
  .rename(['cnt_sum', 'fl_ok_sum', 'fl_sum', 'gfa_sum']);

// 4c. ถนน (optional)
var roadBands = [];
if (ROAD_ASSET) {
  var roads = ee.FeatureCollection(ROAD_ASSET);
  if (ROAD_IS_LINE) {
    roads = roads.map(function(f) { return f.buffer(ROAD_WIDTH_M / 2); });
  }
  roadBands = [paintMask(roads, 'road_r')];
}

// ---------- 5. รวมค่าราย cell ----------
function buildCellTable(fc) {
  // 5a. LST/NDVI/NDBI ที่ 30m
  var step1 = comp.reduceRegions({
    collection: fc,
    reducer:    ee.Reducer.mean(),
    scale:      30,
    crs:        CRS
  });

  // 5b. Coverage ratio อาคาร/วัสดุ ที่ 5m
  var coverImg = roadBands.length
    ? coverageStack.addBands(ee.Image.cat(roadBands))
    : coverageStack;
  var step2 = coverImg.reduceRegions({
    collection: step1,
    reducer:    ee.Reducer.mean(),
    scale:      VEC_SCALE,
    crs:        CRS
  });

  // 5c. Count/floors/FAR จาก centroid image ที่ 5m (sum reducer)
  var step3 = cntStack.reduceRegions({
    collection: step2,
    reducer:    ee.Reducer.sum(),
    scale:      VEC_SCALE,
    crs:        CRS
  });

  // 5d. คำนวณ derived fields
  return step3.map(function(cell) {
    var cnt   = ee.Number(cell.get('cnt_sum'));    // จำนวนอาคาร
    var flv   = ee.Number(cell.get('fl_ok_sum')); // จำนวนอาคารที่รู้ชั้น
    var fls   = ee.Number(cell.get('fl_sum'));     // ผลรวมชั้น
    var gfa   = ee.Number(cell.get('gfa_sum'));    // ผลรวม GFA

    var floors = ee.Algorithms.If(
      flv.gt(0),
      fls.divide(flv),   // ชั้นเฉลี่ยเฉพาะอาคารที่มีข้อมูล
      null
    );
    var far = gfa.divide(CELL * CELL);             // GFA / พื้นที่ cell

    var inCover = ee.Algorithms.If(
      ee.Number(cell.get('cov_r')).gte(0.5), 1, 0
    );

    return cell.set({
      n_bldg:   cnt.round(),
      floors:   floors,
      far:      far,
      in_cover: inCover
    });
  });
}

// ---------- 6. แสดงผล ----------
var visLST  = {min:30, max:46,
               palette:['2c7bb6','abd9e9','ffffbf','fdae61','d7191c']};
var visNDVI = {min:0,  max:0.7,
               palette:['8c510a','f6e8c3','a6d96a','1a9850']};

Map.addLayer(comp.select('lst_c'),  visLST,  'LST (°C)');
Map.addLayer(comp.select('ndvi'),   visNDVI, 'NDVI',  false);
Map.addLayer(comp.select('ndbi'),   {min:-0.3,max:0.3,
             palette:['green','white','brown']}, 'NDBI', false);
Map.addLayer(bld.filterBounds(roi).style(
             {color:'444444',fillColor:'44444455',width:1}), {}, 'อาคาร');
Map.addLayer(cells.style({color:'ffffff',fillColor:'00000000',width:1}),
             {}, 'grid ' + CELL + ' m');
Map.addLayer(laksi.style({color:'blue',fillColor:'00000000'}),
             {}, 'เขตหลักสี่');

// ---------- 7. กดแผนที่ → เห็นค่าของ cell นั้น ----------
var panel = ui.Panel({style:{position:'bottom-left',padding:'8px',width:'300px'}});
Map.add(panel);
var selLayer = null;

function fmt(v, d) {
  return (v === null || v === undefined) ? '–' : Number(v).toFixed(d);
}
function row(label, val) {
  panel.add(ui.Label(label + ': ' + val, {margin:'1px 0'}));
}

Map.onClick(function(coords) {
  panel.clear();
  panel.add(ui.Label('กำลังคำนวณ...'));

  var pt   = ee.Geometry.Point([coords.lon, coords.lat]);
  var cell = cells.filterBounds(pt).limit(1);

  if (selLayer) Map.layers().remove(selLayer);
  selLayer = ui.Map.Layer(
    cell.style({color:'yellow',fillColor:'00000000',width:3}),
    {}, 'cell ที่เลือก'
  );
  Map.layers().add(selLayer);

  buildCellTable(cell).first().evaluate(function(f) {
    panel.clear();
    if (!f) { panel.add(ui.Label('คลิกนอกพื้นที่เขต')); return; }
    var p = f.properties;

    panel.add(ui.Label('Cell ' + p.cell_id, {fontWeight:'bold'}));
    row('LST (°C)',   fmt(p.lst_c, 1));
    row('NDVI',       fmt(p.ndvi,  3));
    row('NDBI',       fmt(p.ndbi,  3));
    row('ภาพที่ใช้ (n_valid)', fmt(p.n_valid, 0));

    var coverLabel = p.in_cover
      ? '— อาคาร —'
      : '— อาคาร (นอกพื้นที่ที่มีข้อมูล ค่า 0 ไม่น่าเชื่อถือ) —';
    panel.add(ui.Label(coverLabel, {fontWeight:'bold'}));

    row('จำนวนอาคาร (n_bldg)',         fmt(p.n_bldg, 0));
    row('สัดส่วนพื้นที่อาคาร (%)',      fmt(p.bldg_r * 100, 1));
    row('จำนวนชั้นเฉลี่ย (floors)',     fmt(p.floors, 1));
    row('FAR',                          fmt(p.far, 2));
    row('วัสดุ mat1/2/3/8/9 (% ของ cell)',
        [p.mat1_r,p.mat2_r,p.mat3_r,p.mat8_r,p.mat9_r]
          .map(function(v){return fmt(v*100,0);}).join(' / '));
    row('สัดส่วนถนน (%)',
        p.road_r === undefined ? 'ยังไม่มีข้อมูลถนน' : fmt(p.road_r*100,1));
  });
});

// ---------- 8. Export ----------
// ส่งออกเป็น Shapefile ไปยัง Google Drive (แท็บ Tasks → Run)
// แปลงเป็น CSV ต่อได้ด้วย QGIS หรือ ogr2ogr
var KEEP = [
  'cell_id',
  'lst_c', 'ndvi', 'ndbi', 'n_valid',
  'n_bldg', 'bldg_r', 'floors', 'far', 'in_cover',
  'mat0_r', 'mat1_r', 'mat2_r', 'mat3_r', 'mat8_r', 'mat9_r'
];
if (ROAD_ASSET) KEEP.push('road_r');

Export.table.toDrive({
  collection:  buildCellTable(cells).select(KEEP),
  description: 'Laksi_cells_' + CELL + 'm_v3',
  folder:      'GEE_exports',
  fileFormat:  'SHP'
});
