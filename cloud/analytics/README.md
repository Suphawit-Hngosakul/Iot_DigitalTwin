# cloud/analytics

รวมข้อมูล IoT + ดาวเทียมบนกริดกลาง 90×90 ม. (`EPSG:32647`) และคำนวณสถิติ **S1–S8** (#33, Spec 60) ลง `curated/analytics/`

## โครงสร้างโมดูล

- `curated_merge/` (#32): Step Functions `iot-dt-curated-merge` รวม `satellite_grid` + `buildings_grid` + `readings_1min` ลงตาราง Iceberg `iot_walking.cell_period`
- `stats/` (#33): โมดูลคำนวณสถิติกลาง (Pure NumPy/Pandas, `seed=42`) ใช้ร่วมกันทั้ง Lambda และ Verification Notebook:
  - **S1 (`summary`)**: สถิติพรรณนา (`n`, `mean`, `sd`, `min`, `p25`, `median`, `p75`, `max`, `p90`)
  - **S2 (`correlation`)**: Pearson $r$ และ Spearman rank $\rho$ พร้อมการเทียบอันดับ IoT กับ LST และข้อจำกัด
  - **S3–S4 (`regression`)**: Standardized OLS, VIF (แจ้งเตือนเมื่อ `VIF > 5`), Bootstrap 95% CI, Spatial Block CV (Roberts et al., 2017) และตัวชี้วัดเชิงพรรณนา P25→P75 ที่โครงการกำหนดขึ้นเอง
  - **S5 (`importance`)**: Random Forest (`trees=300`, `seed=42`) + OOB Permutation Importance
  - **S6 (`hotspot`)**: Getis-Ord $G_i^*$ รัศมี 300 ม.
  - **S7 (`spatial_autocorr`)**: Global Moran's I ของ `lst_mean` และ `s3_residual` (แจ้งเตือนหากค่าคลาดเคลื่อนยังกระจุกตัวเชิงพื้นที่)
  - **S8 (`groups`)**: เปรียบเทียบ 3 กลุ่ม (`green` / `medium` / `dense`) และทดสอบ Kruskal-Wallis
  - **`cell_attribution`**: ผลวิเคราะห์ระดับเซลล์ 90×90 ม. สำหรับคลิกดูสาเหตุความร้อนรายช่องบนแผนที่
- `functions/` & `statemachine/analytics.asl.json`: Step Functions `iot-dt-analytics` ทำงานอัตโนมัติเมื่อ `curated/_success/merge/<period>.json` ถูกสร้าง
- `notebooks/verify_heat_analytics.ipynb`: สมุดบันทึกยืนยันความถูกต้องของสูตรและสร้างกราฟวินิจฉัย
