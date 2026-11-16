# cloud/satellite

- `ingest/` — Lambda ดึง Landsat + ข้อมูลอาคาร (building.shp ของ กทม. + Open Buildings) จาก Google Earth Engine ลง S3 `raw/satellite/`
- `pipeline/` — Step Functions ดาวเทียม → grid 90 m + PNG layers

