-- Raw rows per hour next to trusted rows: raw = trusted + dropped (quarantine rows + duplicates).
WITH raw AS (
  SELECT device_id, date, hour, count(*) AS raw_rows
  FROM raw_csv
  WHERE device_id = 'IOTW-A001' AND date = '2026-11-16'
  GROUP BY device_id, date, hour
), trusted AS (
  SELECT device_id, date, format('%02d', hour(ts_local)) AS hour, count(*) AS trusted_rows
  FROM readings_1s
  WHERE device_id = 'IOTW-A001' AND date = '2026-11-16'
  GROUP BY device_id, date, format('%02d', hour(ts_local))
)
SELECT r.device_id, r.date, r.hour, r.raw_rows, coalesce(p.trusted_rows, 0) AS trusted_rows,
       r.raw_rows - coalesce(p.trusted_rows, 0) AS dropped_rows
FROM raw r
LEFT JOIN trusted p ON p.device_id = r.device_id AND p.date = r.date AND p.hour = r.hour
ORDER BY r.hour;
