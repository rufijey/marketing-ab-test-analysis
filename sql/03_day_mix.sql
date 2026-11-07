-- Balance check: share of each group's users by the day they saw the most ads.
-- With random assignment and identical delivery, the shares would be nearly equal.
WITH counts AS (
  SELECT most_ads_day, test_group, COUNT(*) AS users
  FROM `{raw_table}`
  GROUP BY most_ads_day, test_group
),
shares AS (
  SELECT
    most_ads_day,
    test_group,
    users,
    users / SUM(users) OVER (PARTITION BY test_group) AS share
  FROM counts
)
SELECT
  most_ads_day,
  MAX(IF(test_group = 'ad',  users, NULL)) AS users_ad,
  MAX(IF(test_group = 'psa', users, NULL)) AS users_psa,
  MAX(IF(test_group = 'ad',  share, NULL)) AS share_ad,
  MAX(IF(test_group = 'psa', share, NULL)) AS share_psa,
  (MAX(IF(test_group = 'ad', share, NULL)) - MAX(IF(test_group = 'psa', share, NULL))) * 100 AS diff_pp
FROM shares
GROUP BY most_ads_day
ORDER BY CASE most_ads_day
  WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4
  WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END
