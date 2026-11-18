-- 1 device x 1 hour of 1 s rows (ts_local = Thai time).
SELECT ts_local, session, record_id, db_spl, dba, lux, temp_c, humidity_pct, lat, lon
FROM readings_1s
WHERE device_id = 'IOTW-A001'
  AND date = '2026-11-16'
  AND ts_local >= TIMESTAMP '2026-11-16 08:00:00'
  AND ts_local <  TIMESTAMP '2026-11-16 09:00:00'
ORDER BY ts_local, session, record_id;
