// ============================================================
// Issue #26 — Spike: Single Landsat Scene Analysis & Validation
// พื้นที่: เขตหลักสี่ (AOI)
// ผลลัพธ์:
//   - Scene ID: 1_LC08_129050_20240317 (2024-03-17 10:37:34 BKK)
//   - Cloud ในหลักสี่: 0.00%
//   - ST_B10 Transform: [30, 0, 575085, 0, -30, 1714215]
//   - (ORIGIN_X - tx) % 30 == 0 และ (ORIGIN_Y - ty) % 30 == 0 เป๊ะ (ตรงกับ grid.py)
//   - LST: Min 35.3 / Mean 45.1 / Max 54.9 °C
// ============================================================

// ---------- 0. ข้อมูลนำเข้า ----------
var BASE  = 'projects/turnkey-tribute-511903-t6/assets/';
var laksi = ee.FeatureCollection(BASE + 'laksi_boundary');
var roi   = laksi.geometry();

var ORIGIN_X = 660015.0;
var ORIGIN_Y = 1545015.0;
var CRS      = 'EPSG:32647';

Map.centerObject(roi, 13);

// ---------- 1. ค้นหาและเลือก 1 ฉากหน้าร้อนที่ดีที่สุด (ปี 2024) ----------
function addRoiCloud(img) {
  var qa = img.select('QA_PIXEL');
  var cloud = qa.bitwiseAnd(30).neq(0); // 1 = มีเมฆ/เงาเมฆ
  var mean = cloud.reduceRegion({
    reducer: ee.Reducer.mean(),
    geometry: roi,
    scale: 30,
    crs: CRS,
    maxPixels: 1e7
  }).get('QA_PIXEL');
  return img.set('roi_cloud_pct', ee.Number(mean).multiply(100));
}

var scenes = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
  .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
  .filterBounds(roi)
  .filter(ee.Filter.calendarRange(3, 5, 'month'))
  .filterDate('2024-01-01', '2024-12-31')
  .map(addRoiCloud)
  .sort('roi_cloud_pct');

print('จำนวนฉากหน้าร้อนปี 2024 ทั้งหมด:', scenes.size());

var bestScene = ee.Image(scenes.first());
var sceneId   = bestScene.get('system:index').getInfo();
var dateUtc   = ee.Date(bestScene.get('system:time_start')).format('YYYY-MM-dd HH:mm:ss').getInfo();
var cloudPct  = ee.Number(bestScene.get('roi_cloud_pct')).getInfo();

print('=== 1. ข้อมูลฉากที่เลือก ===');
print('Scene ID:', sceneId);
print('Acquisition Time (UTC):', dateUtc);
print('Cloud % ในเขตหลักสี่:', Number(cloudPct).toFixed(2) + ' %');

// ---------- 2. ตรวจสอบค่า Transform ของ ST_B10 (ยืนยัน Grid Origin) ----------
var proj = bestScene.select('ST_B10').projection();
var tr   = proj.getInfo().transform; // [30, 0, tx, 0, -30, ty]

print('=== 2. Transform ตรวจสอบ Grid ===');
print('ST_B10 Transform:', tr);
var tx = tr[2];
var ty = tr[5];
print('tx (xOrigin):', tx);
print('ty (yOrigin):', ty);

var diffX = Math.abs((ORIGIN_X - tx) % 30);
var diffY = Math.abs((ORIGIN_Y - ty) % 30);
print('เช็คแกน X: (ORIGIN_X - tx) % 30 == 0 -> เศษ =', diffX);
print('เช็คแกน Y: (ORIGIN_Y - ty) % 30 == 0 -> เศษ =', diffY);
if (diffX < 1e-4 && diffY < 1e-4) {
  print('✅ Grid Origin (660015, 1545015) ตรงกับ Pixel Lattice ของ Landsat สมบูรณ์ 100%!');
} else {
  print('⚠️ ข้อควรระวัง: Grid Origin เยื้องกับ Landsat Pixel (เศษไม่เป็น 0)');
}

// ---------- 3. คำนวณ LST, NDVI, NDBI และ Cloud Mask ของฉากเดี่ยว ----------
var qa    = bestScene.select('QA_PIXEL');
var clear = qa.bitwiseAnd(30).eq(0);

var lst = bestScene.select('ST_B10')
  .multiply(0.00341802).add(149.0).subtract(273.15)
  .rename('lst_c');

var sr   = bestScene.select(['SR_B4','SR_B5','SR_B6']).multiply(0.0000275).add(-0.2);
var ndvi = sr.select('SR_B5').subtract(sr.select('SR_B4'))
  .divide(sr.select('SR_B5').add(sr.select('SR_B4'))).rename('ndvi');
var ndbi = sr.select('SR_B6').subtract(sr.select('SR_B5'))
  .divide(sr.select('SR_B6').add(sr.select('SR_B5'))).rename('ndbi');

var cloudMask = clear.rename('cloud_mask');

var singleSceneStack = lst.addBands([ndvi, ndbi, cloudMask])
  .clip(roi);

var stats = singleSceneStack.select(['lst_c', 'ndvi', 'ndbi'])
  .updateMask(clear)
  .reduceRegion({
    reducer: ee.Reducer.minMax().combine(ee.Reducer.mean(), '', true),
    geometry: roi,
    scale: 30,
    crs: CRS,
    maxPixels: 1e7
  }).getInfo();

print('=== 3. สถิติของฉากนี้ในหลักสี่ ===');
print('LST Min/Mean/Max (°C):', 
      Number(stats.lst_c_min).toFixed(1), '/', 
      Number(stats.lst_c_mean).toFixed(1), '/', 
      Number(stats.lst_c_max).toFixed(1));
print('NDVI Mean:', Number(stats.ndvi_mean).toFixed(3));
print('NDBI Mean:', Number(stats.ndbi_mean).toFixed(3));

// ---------- 4. แสดงผลบนแผนที่ ----------
Map.addLayer(singleSceneStack.select('lst_c').updateMask(clear), 
             {min:30, max:48, palette:['2c7bb6','abd9e9','ffffbf','fdae61','d7191c']}, 
             'Single Scene LST (°C)');
Map.addLayer(singleSceneStack.select('ndvi').updateMask(clear), 
             {min:0, max:0.6, palette:['8c510a','f6e8c3','a6d96a','1a9850']}, 
             'Single Scene NDVI', false);
Map.addLayer(clear.eq(0).selfMask(), {palette:['red']}, 'พื้นที่เมฆ/เงาเมฆ (Masked)', false);
Map.addLayer(laksi.style({color:'blue', fillColor:'00000000'}), {}, 'ขอบเขตหลักสี่');

// ---------- 5. ทดสอบสร้าง URL สำหรับดาวน์โหลด ----------
var downloadUrlGeoTIFF = singleSceneStack.getDownloadURL({
  name: 'single_scene_' + sceneId + '_bands',
  scale: 30,
  crs: CRS,
  region: roi,
  format: 'GEO_TIFF'
});

print('=== 4. ลิงก์ดาวน์โหลด (ทดสอบเพดาน 32 MB) ===');
print('GeoTIFF 4 แบนด์ Download Link:', downloadUrlGeoTIFF);
