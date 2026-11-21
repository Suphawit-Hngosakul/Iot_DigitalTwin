-- satellite_grid: one row per batch (scene / season). Always filter period_type
-- (+ year) so partition projection only lists the folders it needs.
-- Every batch of the Lak Si AOI should have the same n_cells (~6,829).
SELECT period_type, year, period,
       count(*)                         AS n_cells,
       count(lst_mean)                  AS n_cells_with_lst,
       round(avg(lst_mean), 2)          AS lst_mean,
       round(approx_percentile(lst_mean, 0.9), 2) AS lst_p90_of_cells,
       round(avg(ndvi_mean), 3)         AS ndvi_mean,
       round(avg(ndbi_mean), 3)         AS ndbi_mean,
       round(avg(valid_pixel_ratio), 3) AS valid_pixel_ratio
FROM satellite_grid
WHERE period_type IN ('scene', 'season')
  AND year BETWEEN 2022 AND 2025
GROUP BY period_type, year, period
ORDER BY period_type, period;
