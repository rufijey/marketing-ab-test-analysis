-- Lift by segment: day of week, hour bucket and exposure bucket, stacked in one table.
-- Counts feed the per-segment z-test and the Holm correction in Python.
WITH users AS (
  SELECT
    test_group,
    converted,
    most_ads_day,
    CASE
      WHEN most_ads_hour BETWEEN 0 AND 5   THEN '00-05'
      WHEN most_ads_hour BETWEEN 6 AND 11  THEN '06-11'
      WHEN most_ads_hour BETWEEN 12 AND 17 THEN '12-17'
      ELSE '18-23'
    END AS hour_bucket,
    CASE
      WHEN total_ads <= 10  THEN '1-10'
      WHEN total_ads <= 25  THEN '11-25'
      WHEN total_ads <= 50  THEN '26-50'
      WHEN total_ads <= 100 THEN '51-100'
      WHEN total_ads <= 250 THEN '101-250'
      ELSE '250+'
    END AS ads_bucket
  FROM `{raw_table}`
),
long AS (
  SELECT 'most_ads_day' AS dimension, most_ads_day AS segment, test_group, converted FROM users
  UNION ALL
  SELECT 'hour_bucket', hour_bucket, test_group, converted FROM users
  UNION ALL
  SELECT 'ads_bucket', ads_bucket, test_group, converted FROM users
),
agg AS (
  SELECT
    dimension,
    segment,
    COUNTIF(test_group = 'ad')                     AS users_ad,
    COUNTIF(test_group = 'psa')                    AS users_psa,
    COUNTIF(test_group = 'ad'  AND converted = 1)  AS conv_ad,
    COUNTIF(test_group = 'psa' AND converted = 1)  AS conv_psa
  FROM long
  GROUP BY dimension, segment
)
SELECT
  dimension,
  segment,
  users_ad,
  users_psa,
  conv_ad,
  conv_psa,
  conv_ad / users_ad                                         AS cr_ad,
  conv_psa / users_psa                                       AS cr_psa,
  (conv_ad / users_ad - conv_psa / users_psa) * 100          AS abs_lift_pp,
  SAFE_DIVIDE(conv_ad / users_ad, conv_psa / users_psa) - 1  AS rel_lift,
  (users_ad + users_psa) / SUM(users_ad + users_psa) OVER (PARTITION BY dimension) AS share_of_users,
  (conv_ad + conv_psa) / SUM(conv_ad + conv_psa) OVER (PARTITION BY dimension)     AS share_of_conversions
FROM agg
ORDER BY
  CASE dimension WHEN 'most_ads_day' THEN 1 WHEN 'hour_bucket' THEN 2 ELSE 3 END,
  CASE segment
    WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4
    WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 WHEN 'Sunday' THEN 7
    WHEN '1-10' THEN 1 WHEN '11-25' THEN 2 WHEN '26-50' THEN 3 WHEN '51-100' THEN 4
    WHEN '101-250' THEN 5 WHEN '250+' THEN 6
    ELSE 0 END,
  segment
