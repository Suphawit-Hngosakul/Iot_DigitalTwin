-- cell_period (Iceberg, written by curated-merge #32): one row per cell x period x match_type.
-- Every period / match_type should have the same n_cells as its satellite batch (~6,829).
SELECT period, match_type, sat_period,
       count(*)                       AS n_cells,
       count(lst_mean)                AS n_with_lst,
       count(builtup_pct)             AS n_with_buildings,
       count(air_temp_mean)           AS n_with_iot,
       sum(n_iot_minutes)             AS iot_minutes,
       round(avg(lst_mean), 2)        AS lst_mean,
       round(avg(air_temp_mean), 2)   AS air_temp_mean
FROM cell_period
GROUP BY period, match_type, sat_period
ORDER BY period, match_type;
