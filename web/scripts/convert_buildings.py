import argparse
import json
from pathlib import Path
import geopandas as gpd
from shapely.ops import transform

PRESET_AOI = {
    "laksi": (100.545, 13.855, 100.610, 13.910),
    "tu": (100.585, 14.055, 100.625, 14.090),
}

def round_coords(geom, decimals=6):
    return transform(lambda x, y, z=None: (round(x, decimals), round(y, decimals)), geom)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shp", required=True, help="Path ไปยังไฟล์ building.shp")
    ap.add_argument("--out", default="public/data/buildings.geojson", help="ไฟล์ GeoJSON ปลายทาง")
    ap.add_argument("--aoi", choices=["laksi", "tu", "all", "auto"], default="auto", help="ตัดขอบเขตพื้นที่ศึกษา")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MINX", "MINY", "MAXX", "MAXY"), help="กำหนดกรอบพิกัดเอง (WGS84)")
    args = ap.parse_args()

    gdf = gpd.read_file(args.shp)
    print("คอลัมน์ที่พบใน building.shp:", list(gdf.columns))
    print("จำนวนอาคารทั้งหมดในไฟล์ต้นฉบับ:", len(gdf))

    if gdf.crs is None:
        print("ไม่พบไฟล์ .prj -> กำหนดค่าเริ่มต้นเป็น EPSG:32647 (UTM Zone 47N)")
        gdf = gdf.set_crs(epsg=32647)
    gdf = gdf.to_crs(epsg=4326)

    if args.bbox:
        minx, miny, maxx, maxy = args.bbox
        gdf = gdf.cx[minx:maxx, miny:maxy]
    elif args.aoi in PRESET_AOI:
        minx, miny, maxx, maxy = PRESET_AOI[args.aoi]
        gdf = gdf.cx[minx:maxx, miny:maxy]
    elif args.aoi == "auto":
        for name, (minx, miny, maxx, maxy) in PRESET_AOI.items():
            sub = gdf.cx[minx:maxx, miny:maxy]
            if len(sub) > 0:
                print(f"ตรวจพบข้อมูลตรงกับพื้นที่ '{name}' ({len(sub)} อาคาร)")
                gdf = sub
                break

    cols_lower = {c.lower(): c for c in gdf.columns}
    floor_col = next((cols_lower[k] for k in ["floors", "floor", "num_floors", "fl_num", "story", "stories", "bldg_fl", "ชั้น"] if k in cols_lower), None)
    height_col = next((cols_lower[k] for k in ["height", "height_m", "bldg_h", "bldg_ht", "z", "elev", "ความสูง"] if k in cols_lower), None)
    type_col = next((cols_lower[k] for k in ["type", "bldg_type", "bldg_use", "use", "landuse", "lu_name", "ประเภท"] if k in cols_lower), None)
    print(f"จับคู่คอลัมน์ -> ชั้น: {floor_col} | ความสูง: {height_col} | ประเภท: {type_col}")

    gdf_utm = gdf.to_crs(epsg=32647)
    areas = gdf_utm.geometry.area.round(1).tolist()

    features = []
    for idx, (_, row) in enumerate(gdf.iterrows()):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            floors = float(row[floor_col]) if floor_col and row[floor_col] is not None else 2.0
        except (ValueError, TypeError):
            floors = 2.0
        floors = max(1.0, floors)

        try:
            height_m = float(row[height_col]) if height_col and row[height_col] is not None else round(floors * 3.2, 1)
        except (ValueError, TypeError):
            height_m = round(floors * 3.2, 1)
        if height_m <= 0:
            height_m = round(floors * 3.2, 1)

        btype = str(row[type_col]).strip() if type_col and row[type_col] is not None else "อาคารในพื้นที่ศึกษา"
        simp = round_coords(geom.simplify(0.000005, preserve_topology=True), 6)
        features.append({
            "type": "Feature",
            "geometry": json.loads(gpd.GeoSeries([simp]).to_json())["features"][0]["geometry"],
            "properties": {
                "bldg_id": f"BLDG-{idx+1:05d}",
                "floors": round(floors, 1),
                "height_m": round(height_m, 1),
                "area_sqm": areas[idx],
                "bldg_type": btype,
                "bldg_source": "building.shp (จริง)",
            },
        })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = out_path.stat().st_size / 1024
    print(f"แปลงสำเร็จ! บันทึก {len(features)} อาคาร ({size_kb:.1f} KB) -> {out_path.resolve()}")

if __name__ == "__main__":
    main()