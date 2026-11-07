-- Data quality checks on the raw table: one row of counts that should all be clean.
SELECT
  COUNT(*)                                              AS row_count,
  COUNT(DISTINCT user_id)                               AS unique_users,
  COUNT(*) - COUNT(DISTINCT user_id)                    AS duplicate_rows,
  (SELECT COUNT(*) FROM (
     SELECT user_id FROM `{raw_table}`
     GROUP BY user_id HAVING COUNT(DISTINCT test_group) > 1
  ))                                                    AS users_in_both_groups,
  COUNTIF(test_group IS NULL OR converted IS NULL
          OR total_ads IS NULL OR most_ads_day IS NULL
          OR most_ads_hour IS NULL)                     AS rows_with_nulls,
  COUNTIF(test_group NOT IN ('ad', 'psa'))              AS invalid_group,
  COUNTIF(converted NOT IN (0, 1))                      AS invalid_converted,
  COUNTIF(most_ads_hour NOT BETWEEN 0 AND 23)           AS invalid_hour,
  COUNTIF(most_ads_day NOT IN ('Monday', 'Tuesday', 'Wednesday', 'Thursday',
                               'Friday', 'Saturday', 'Sunday')) AS invalid_day,
  MIN(total_ads)                                        AS total_ads_min,
  APPROX_QUANTILES(total_ads, 100)[OFFSET(99)]          AS total_ads_p99,
  MAX(total_ads)                                        AS total_ads_max
FROM `{raw_table}`
