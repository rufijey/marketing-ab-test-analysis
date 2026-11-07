-- Post-stratified lift: compare groups inside day x exposure cells, then average the cell lifts
-- weighted by the ad group's mix. This removes the effect of the groups' different day/exposure mix.
WITH cells AS (
  SELECT
    most_ads_day,
    CASE
      WHEN total_ads <= 10  THEN '1-10'
      WHEN total_ads <= 25  THEN '11-25'
      WHEN total_ads <= 50  THEN '26-50'
      WHEN total_ads <= 100 THEN '51-100'
      WHEN total_ads <= 250 THEN '101-250'
      ELSE '250+'
    END AS ads_bucket,
    COUNTIF(test_group = 'ad')  AS users_ad,
    COUNTIF(test_group = 'psa') AS users_psa,
    SAFE_DIVIDE(COUNTIF(test_group = 'ad'  AND converted = 1), COUNTIF(test_group = 'ad'))  AS cr_ad,
    SAFE_DIVIDE(COUNTIF(test_group = 'psa' AND converted = 1), COUNTIF(test_group = 'psa')) AS cr_psa
  FROM `{raw_table}`
  GROUP BY 1, 2
),
weighted AS (
  SELECT
    *,
    users_ad / SUM(users_ad) OVER () AS weight
  FROM cells
  WHERE users_ad > 0 AND users_psa > 0
)
SELECT
  COUNT(*)                              AS cells_used,
  SUM(weight * (cr_ad - cr_psa)) * 100  AS post_stratified_abs_lift_pp,
  SUM(weight * cr_psa)                  AS post_stratified_cr_psa,
  SUM(weight * cr_ad)                   AS post_stratified_cr_ad
FROM weighted
