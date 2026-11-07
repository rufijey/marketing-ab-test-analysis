"""A/B test analysis: does showing an ad instead of a public service announcement increase conversion?

Usage: python src/analyze.py
Aggregations run as SQL in BigQuery (sql/*.sql); statistics that SQL is not built for
(p-values, Newcombe CIs, power, logistic regression, bootstrap, Holm correction) run in Python.
Writes report/tables/*.csv, report/*.png and report/findings.json.
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import confint_proportions_2indep, proportion_effectsize, proportions_ztest

from bq import RAW_TABLE, ROOT, client, run_sql

REPORT = ROOT / "report"
TABLES = REPORT / "tables"
ALPHA, POWER = 0.05, 0.80


def test_counts(x1, n1, x0, n0):
    """Two-proportion z-test p-value and Newcombe 95% CI (in percentage points) from counts."""
    x1, n1, x0, n0 = int(x1), int(n1), int(x0), int(n0)
    _, p = proportions_ztest([x1, x0], [n1, n0])
    lo, hi = confint_proportions_2indep(x1, n1, x0, n0, method="newcomb")
    return float(p), float(lo) * 100, float(hi) * 100


def main():
    REPORT.mkdir(exist_ok=True)
    TABLES.mkdir(exist_ok=True)
    bq = client()
    f = {"bigquery_table": RAW_TABLE}

    # ---------- 1. Data quality (SQL) ----------
    dq = run_sql("01_data_quality", bq)
    dq.to_csv(TABLES / "data_quality.csv", index=False)
    f["data_quality"] = {k: int(v) for k, v in dq.iloc[0].items()}

    # ---------- 2. Main result (SQL aggregates, Python test) ----------
    g = run_sql("02_group_result", bq).iloc[0]
    p_value, ci_lo, ci_hi = test_counts(g.conv_ad, g.users_ad, g.conv_psa, g.users_psa)
    group = g.to_dict()
    group.update({"p_value": p_value, "ci_low_pp": ci_lo, "ci_high_pp": ci_hi})
    pd.DataFrame([group]).to_csv(TABLES / "group_result.csv", index=False)
    f["split"] = {"ad": int(g.users_ad), "psa": int(g.users_psa)}
    f["main"] = {k: float(v) for k, v in group.items()}
    f["incremental_conversions_ci"] = [ci_lo / 100 * g.users_ad, ci_hi / 100 * g.users_ad]

    # ---------- 3. Balance checks ----------
    day_mix = run_sql("03_day_mix", bq)
    day_mix.to_csv(TABLES / "day_mix_by_group.csv", index=False)
    f["day_mix"] = day_mix.round(4).to_dict("records")
    f["day_mix_max_gap_pp"] = float(day_mix["diff_pp"].abs().max())

    # Row-level extract for tests that need individual users
    users = run_sql("06_users", bq)
    assert len(users) == g.users_ad + g.users_psa, "row-level extract does not match SQL totals"
    ads_ad = users.loc[users.test_group == "ad", "total_ads"]
    ads_psa = users.loc[users.test_group == "psa", "total_ads"]
    ks = stats.ks_2samp(ads_ad, ads_psa)
    day_chi = stats.chi2_contingency(day_mix[["users_ad", "users_psa"]])
    hour_tab = pd.crosstab(users["most_ads_hour"], users["test_group"])
    hour_chi = stats.chi2_contingency(hour_tab)
    hour_share = hour_tab / hour_tab.sum()
    f["hour_mix_max_gap_pp"] = float(((hour_share["ad"] - hour_share["psa"]) * 100).abs().max())
    balance = pd.DataFrame([
        {"check": "total_ads distribution (KS test)", "statistic": ks.statistic, "p_value": ks.pvalue,
         "ad_value": float(ads_ad.median()), "psa_value": float(ads_psa.median()), "note": "median ads seen"},
        {"check": "most_ads_day mix (chi-square)", "statistic": day_chi[0], "p_value": day_chi[1],
         "ad_value": np.nan, "psa_value": np.nan, "note": f"max gap {f['day_mix_max_gap_pp']:.2f} pp"},
        {"check": "most_ads_hour mix (chi-square)", "statistic": hour_chi[0], "p_value": hour_chi[1],
         "ad_value": np.nan, "psa_value": np.nan, "note": f"max gap {f['hour_mix_max_gap_pp']:.2f} pp"},
    ])
    balance.to_csv(TABLES / "balance_checks.csv", index=False)
    f["balance"] = balance.round(4).to_dict("records")

    # ---------- 4. Does the lift hold up? ----------
    rng = np.random.default_rng(42)
    a = users.loc[users.test_group == "ad", "converted"].to_numpy()
    c = users.loc[users.test_group == "psa", "converted"].to_numpy()
    boots = [rng.choice(a, a.size).mean() / rng.choice(c, c.size).mean() - 1 for _ in range(2000)]
    f["rel_lift_bootstrap_ci"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]

    users["treat"] = (users["test_group"] == "ad").astype(int)
    users["log_ads"] = np.log(users["total_ads"])
    users["hour_bucket"] = pd.cut(users["most_ads_hour"], [-1, 5, 11, 17, 23], labels=["00-05", "06-11", "12-17", "18-23"])
    logit = smf.logit("converted ~ treat + C(most_ads_day) + C(hour_bucket) + log_ads", data=users).fit(disp=False)
    or_ci = np.exp(logit.conf_int().loc["treat"])
    f["adjusted_odds_ratio"] = float(np.exp(logit.params["treat"]))
    f["adjusted_odds_ratio_ci"] = [float(or_ci.iloc[0]), float(or_ci.iloc[1])]
    f["adjusted_p_value"] = float(logit.pvalues["treat"])
    f["unadjusted_odds_ratio"] = float((g.cr_ad / (1 - g.cr_ad)) / (g.cr_psa / (1 - g.cr_psa)))

    ps = run_sql("05_post_stratified_lift", bq)
    ps.to_csv(TABLES / "post_stratified_lift.csv", index=False)
    f["post_stratified"] = {k: float(v) for k, v in ps.iloc[0].items()}

    # ---------- 5. Power and sample size ----------
    power = NormalIndPower()
    n_ad, n_psa, p0 = int(g.users_ad), int(g.users_psa), float(g.cr_psa)
    ratio = n_psa / n_ad
    es_obs = proportion_effectsize(g.cr_ad, p0)
    f["achieved_power"] = float(power.power(es_obs, nobs1=n_ad, ratio=ratio, alpha=ALPHA))
    es_mde = power.solve_power(nobs1=n_ad, ratio=ratio, alpha=ALPHA, power=POWER)
    p1_mde = np.sin(np.arcsin(np.sqrt(p0)) + es_mde / 2) ** 2
    f["mde_relative"] = float(p1_mde / p0 - 1)
    f["mde_abs_pp"] = float((p1_mde - p0) * 100)
    n_equal = power.solve_power(effect_size=es_obs, ratio=1, alpha=ALPHA, power=POWER)
    n_ad_actual = power.solve_power(effect_size=es_obs, ratio=ratio, alpha=ALPHA, power=POWER)
    f["sample_needed_equal_split_total"] = float(n_equal * 2)
    f["sample_needed_actual_split_total"] = float(n_ad_actual * (1 + ratio))
    plan = []
    for rel in [0.05, 0.10, 0.20, 0.30, 0.50]:
        es = proportion_effectsize(p0 * (1 + rel), p0)
        plan.append({"relative_lift": rel,
                     "users_per_group_50_50": int(np.ceil(power.solve_power(effect_size=es, ratio=1, alpha=ALPHA, power=POWER)))})
    pd.DataFrame(plan).to_csv(TABLES / "sample_size_plan.csv", index=False)
    f["sample_size_plan"] = plan

    # ---------- 6. Segments (SQL aggregates, Python tests + Holm) ----------
    seg = run_sql("04_segments", bq)
    tested = [test_counts(r.conv_ad, r.users_ad, r.conv_psa, r.users_psa) for r in seg.itertuples()]
    seg["p_value"], seg["ci_low_pp"], seg["ci_high_pp"] = zip(*tested)
    seg["p_value_holm"] = multipletests(seg["p_value"], method="holm")[1]
    seg["significant_raw"] = seg["p_value"] < ALPHA
    seg["significant_holm"] = seg["p_value_holm"] < ALPHA
    seg.to_csv(TABLES / "segments.csv", index=False)
    f["segments"] = seg.round(5).to_dict("records")
    f["segments_significant_raw"] = int(seg["significant_raw"].sum())
    f["segments_significant_holm"] = int(seg["significant_holm"].sum())

    (REPORT / "findings.json").write_text(json.dumps(f, indent=2, default=str), encoding="utf-8")

    # ---------- charts ----------
    plt.rcParams.update({"figure.dpi": 130, "axes.spines.top": False, "axes.spines.right": False})
    blue, grey = "#2b6cb0", "#a0aec0"

    fig, ax = plt.subplots(figsize=(6, 4))
    rates = [g.cr_psa * 100, g.cr_ad * 100]
    errs = [1.96 * np.sqrt(r / 100 * (1 - r / 100) / n) * 100 for r, n in zip(rates, [g.users_psa, g.users_ad])]
    bars = ax.bar(["PSA (control)", "Ad (treatment)"], rates, yerr=errs, capsize=6, color=[grey, blue])
    for b, r in zip(bars, rates):
        ax.text(b.get_x() + b.get_width() / 2, r / 2, f"{r:.2f}%", ha="center", color="white", fontweight="bold")
    ax.set_ylabel("Conversion rate, %")
    ax.set_title("Conversion by group (95% CI)")
    plt.tight_layout()
    plt.savefig(REPORT / "conversion_by_group.png")
    plt.close()

    fig, ax = plt.subplots(figsize=(8, 4.5))
    s = seg.copy()
    s["label"] = s["dimension"].map({"most_ads_day": "Day", "hour_bucket": "Hour", "ads_bucket": "Ads seen"}) + ": " + s["segment"]
    y = np.arange(len(s))[::-1]
    ax.errorbar(s["abs_lift_pp"], y, xerr=[s["abs_lift_pp"] - s["ci_low_pp"], s["ci_high_pp"] - s["abs_lift_pp"]],
                fmt="none", ecolor="#cbd5e0", capsize=3)
    ax.scatter(s["abs_lift_pp"], y, c=[blue if x else grey for x in s["significant_holm"]], zorder=3)
    ax.axvline(0, color="#718096", lw=1)
    ax.axvline(g.abs_lift_pp, color=blue, lw=1, ls="--", label="Overall lift")
    ax.set_yticks(y)
    ax.set_yticklabels(s["label"], fontsize=8)
    ax.set_xlabel("Absolute lift, percentage points (95% CI)")
    ax.set_title("Lift by segment (blue = significant after Holm correction)")
    ax.legend(loc="lower right", fontsize=8)
    plt.tight_layout()
    plt.savefig(REPORT / "lift_by_segment.png")
    plt.close()

    expo = seg[seg["dimension"] == "ads_bucket"]
    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(expo))
    ax.bar(x - 0.2, expo["cr_psa"] * 100, 0.4, label="PSA (control)", color=grey)
    ax.bar(x + 0.2, expo["cr_ad"] * 100, 0.4, label="Ad (treatment)", color=blue)
    ax.set_xticks(x)
    ax.set_xticklabels(expo["segment"])
    ax.set_xlabel("Total ads seen")
    ax.set_ylabel("Conversion rate, %")
    ax.set_title("Conversion grows with exposure in both groups")
    ax.legend()
    plt.tight_layout()
    plt.savefig(REPORT / "conversion_by_exposure.png")
    plt.close()

    summary = {k: f[k] for k in ["data_quality", "main", "rel_lift_bootstrap_ci", "adjusted_odds_ratio",
                                 "adjusted_odds_ratio_ci", "unadjusted_odds_ratio", "post_stratified", "achieved_power",
                                 "mde_relative", "sample_needed_equal_split_total", "sample_needed_actual_split_total",
                                 "day_mix_max_gap_pp", "hour_mix_max_gap_pp", "segments_significant_raw",
                                 "segments_significant_holm"]}
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
