-- User-level extract for the statistics that need row-level data in Python:
-- KS and chi-square balance tests, logistic regression and bootstrap.
SELECT user_id, test_group, converted, total_ads, most_ads_day, most_ads_hour
FROM `{raw_table}`
