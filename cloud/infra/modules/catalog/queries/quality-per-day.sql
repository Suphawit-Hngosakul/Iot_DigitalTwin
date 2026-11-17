-- Loss rate per device per day (sum over batches/sessions).
SELECT device_id, date,
       sum(expected_rows)  AS expected_rows,
       sum(received_rows)  AS received_rows,
       sum(missing_rows)   AS missing_rows,
       sum(duplicate_rows) AS duplicate_rows,
       sum(invalid_rows)   AS invalid_rows,
       round(1.0 * sum(missing_rows) / nullif(sum(expected_rows), 0), 4) AS loss_rate
FROM quality
WHERE date BETWEEN '2026-11-01' AND '2026-11-30'
GROUP BY device_id, date
ORDER BY device_id, date;
