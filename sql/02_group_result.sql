-- Main result: conversion by group, absolute and relative lift, z statistic and a 95% Wald CI.
-- The p-value and the Newcombe CI are calculated in Python from the same counts.
WITH by_group AS (
  SELECT
    test_group,
    COUNT(*)        AS users,
    SUM(converted)  AS conversions,
    AVG(converted)  AS conversion_rate
  FROM `{raw_table}`
  GROUP BY test_group
),
pivoted AS (
  SELECT
    MAX(IF(test_group = 'ad',  users, NULL))           AS users_ad,
    MAX(IF(test_group = 'psa', users, NULL))           AS users_psa,
    MAX(IF(test_group = 'ad',  conversions, NULL))     AS conv_ad,
    MAX(IF(test_group = 'psa', conversions, NULL))     AS conv_psa,
    MAX(IF(test_group = 'ad',  conversion_rate, NULL)) AS cr_ad,
    MAX(IF(test_group = 'psa', conversion_rate, NULL)) AS cr_psa
  FROM by_group
),
stats AS (
  SELECT
    *,
    (conv_ad + conv_psa) / (users_ad + users_psa)                               AS pooled_rate,
    SQRT(cr_ad * (1 - cr_ad) / users_ad + cr_psa * (1 - cr_psa) / users_psa)   AS se_unpooled
  FROM pivoted
)
SELECT
  users_ad, users_psa, conv_ad, conv_psa, cr_ad, cr_psa,
  users_ad / (users_ad + users_psa)                                             AS share_ad,
  (cr_ad - cr_psa) * 100                                                        AS abs_lift_pp,
  SAFE_DIVIDE(cr_ad, cr_psa) - 1                                                AS rel_lift,
  (cr_ad - cr_psa) / SQRT(pooled_rate * (1 - pooled_rate) * (1 / users_ad + 1 / users_psa)) AS z_stat,
  (cr_ad - cr_psa - 1.96 * se_unpooled) * 100                                   AS wald_ci_low_pp,
  (cr_ad - cr_psa + 1.96 * se_unpooled) * 100                                   AS wald_ci_high_pp,
  (cr_ad - cr_psa) * users_ad                                                   AS incremental_conversions
FROM stats
