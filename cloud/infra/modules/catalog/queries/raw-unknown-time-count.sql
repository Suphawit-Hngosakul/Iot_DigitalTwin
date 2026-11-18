-- Rows without a trusted time (date=unknown/hour=unknown), per device and session.
-- Many rows in one session = the device booted without RTC (DateTime = seconds since boot).
SELECT device_id, session, count(*) AS rows,
       min(record_id) AS first_record, max(record_id) AS last_record,
       min(datetime) AS sample_datetime
FROM raw_csv_unknown
GROUP BY device_id, session
ORDER BY device_id, session;
