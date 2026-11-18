-- 1 device x 1 day (Thai date). Always filter device_id + date (partition pruning).
SELECT minute_local, n_rows, db_spl_mean, db_spl_max, db_spl_min, dba_mean,
       temp_c_mean, humidity_pct_mean, lux_mean, lat, lon
FROM readings_1min
WHERE device_id = 'IOTW-A001'
  AND date = '2026-11-16'
ORDER BY minute_local;
