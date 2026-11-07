"""A/B test analysis: does showing an ad instead of a public service announcement increase conversion?

Usage: python src/analyze.py
Reads data/marketing_AB.csv.gz, writes report/tables/*.csv, report/*.png and report/findings.json.
"""
import json
from pathlib import Path

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

ROOT = Path(__file__).resolve().parent.parent
DATA, REPORT = ROOT / "data", ROOT / "report"
TABLES = REPORT / "tables"
ALPHA, POWER = 0.05, 0.80
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def compare(g):
    """Conversion of ad vs psa inside a slice: rates, lift, 95% CI of the difference, p-value."""
    ad, psa = g[g["test_group"] == "ad"]["converted"], g[g["test_group"] == "psa"]["converted"]
    n1, n0, x1, x0 = len(ad), len(psa), int(ad.sum()), int(psa.sum())
    p1, p0 = x1 / n1, x0 / n0
    if x0 == 0 and x1 == 0:
        return pd.Series({"users_ad": n1, "users_psa": n0, "cr_ad": p1, "cr_psa": p0, "abs_lift_pp": 0.0,
                          "rel_lift": np.nan, "ci_low_pp": np.nan, "ci_high_pp": np.nan, "p_value": 1.0})
    _, p = proportions_ztest([x1, x0], [n1, n0])
    lo, hi = confint_proportions_2indep(x1, n1, x0, n0, method="newcomb")
    return pd.Series({
        "users_ad": n1, "users_psa": n0, "cr_ad": p1, "cr_psa": p0,
        "abs_lift_pp": (p1 - p0) * 100, "rel_lift": (p1 / p0 - 1) if p0 > 0 else np.nan,
        "ci_low_pp": lo * 100, "ci_high_pp": hi * 100, "p_value": p,
    })


def main():
    REPORT.mkdir(exist_ok=True)
    TABLES.mkdir(exist_ok=True)
    raw = pd.read_csv(DATA / "marketing_AB.csv.gz")
    f = {}

    # ---------- 1. Data quality ----------
    dq = {
        "rows": len(raw),
        "unique_users": int(raw["user_id"].nunique()),
        "duplicate_user_ids": int(raw["user_id"].duplicated().sum()),
        "users_in_both_groups": int((raw.groupby("user_id")["test_group"].nunique() > 1).sum()),
        "missing_values": int(raw.isna().sum().sum()),
        "unexpected_groups": sorted(set(raw["test_group"]) - {"ad", "psa"}),
        "converted_values": sorted(raw["converted"].unique().tolist()),
        "total_ads_min": int(raw["total_ads"].min()),
        "total_ads_p99": float(raw["total_ads"].quantile(0.99)),
        "total_ads_max": int(raw["total_ads"].max()),
        "hours_range": [int(raw["most_ads_hour"].min()), int(raw["most_ads_hour"].max())],
        "days": sorted(raw["most_ads_day"].unique().tolist()),
    }
    f["data_quality"] = dq
    df = raw.drop_duplicates("user_id")

    # ---------- 2. Split and randomization checks ----------
    split = df["test_group"].value_counts()
    f["split"] = {g: int(n) for g, n in split.items()}
    f["split_share_ad"] = float(split["ad"] / split.sum())
    # Covariates should look the same in both groups if assignment was random
    ads_ad, ads_psa = df.loc[df.test_group == "ad", "total_ads"], df.loc[df.test_group == "psa", "total_ads"]
    ks = stats.ks_2samp(ads_ad, ads_psa)
    day_tab = pd.crosstab(df["most_ads_day"], df["test_group"])
    hour_tab = pd.crosstab(df["most_ads_hour"], df["test_group"])
    balance = pd.DataFrame([
        {"check": "total_ads distribution (KS test)", "statistic": ks.statistic, "p_value": ks.pvalue,
         "ad_value": float(ads_ad.median()), "psa_value": float(ads_psa.median()), "note": "median ads seen"},
        {"check": "most_ads_day mix (chi-square)", "statistic": stats.chi2_contingency(day_tab)[0],
         "p_value": stats.chi2_contingency(day_tab)[1], "ad_value": np.nan, "psa_value": np.nan, "note": "7 days"},
        {"check": "most_ads_hour mix (chi-square)", "statistic": stats.chi2_contingency(hour_tab)[0],
         "p_value": stats.chi2_contingency(hour_tab)[1], "ad_value": np.nan, "psa_value": np.nan, "note": "24 hours"},
    ])
    balance.to_csv(TABLES / "balance_checks.csv", index=False)
    f["balance"] = balance.round(4).to_dict("records")
    # With 588K users even tiny differences are "significant", so look at the size of the gap too
    day_share = (day_tab / day_tab.sum()).reindex(DAYS)
    day_share["diff_pp"] = (day_share["ad"] - day_share["psa"]) * 100
    day_share.to_csv(TABLES / "day_mix_by_group.csv")
    f["day_mix_max_gap_pp"] = float(day_share["diff_pp"].abs().max())
    f["day_mix"] = day_share.round(4).reset_index().to_dict("records")
    hour_share = hour_tab / hour_tab.sum()
    f["hour_mix_max_gap_pp"] = float(((hour_share["ad"] - hour_share["psa"]) * 100).abs().max())

    # ---------- 3. Main result ----------
    main_res = compare(df)
    f["main"] = main_res.to_dict()
    chi2 = stats.chi2_contingency(pd.crosstab(df["test_group"], df["converted"]))
    f["main_chi2_p"] = float(chi2[1])
    incremental = (main_res["cr_ad"] - main_res["cr_psa"]) * main_res["users_ad"]
    f["incremental_conversions"] = float(incremental)
    f["incremental_conversions_ci"] = [float(main_res["ci_low_pp"] / 100 * main_res["users_ad"]),
                                       float(main_res["ci_high_pp"] / 100 * main_res["users_ad"])]

    # Bootstrap check of the relative lift
    rng = np.random.default_rng(42)
    a = df.loc[df.test_group == "ad", "converted"].to_numpy()
    c = df.loc[df.test_group == "psa", "converted"].to_numpy()
    boots = [rng.choice(a, a.size).mean() / rng.choice(c, c.size).mean() - 1 for _ in range(2000)]
    f["rel_lift_bootstrap_ci"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]

    # Adjusted estimates: is the lift still there after controlling for the day/hour/exposure imbalance?
    df["hour_bucket"] = pd.cut(df["most_ads_hour"], [-1, 5, 11, 17, 23], labels=["00-05", "06-11", "12-17", "18-23"])
    df["ads_bucket"] = pd.cut(df["total_ads"], [0, 10, 25, 50, 100, 250, 10_000],
                              labels=["1-10", "11-25", "26-50", "51-100", "101-250", "250+"])
    df["treat"] = (df["test_group"] == "ad").astype(int)
    df["log_ads"] = np.log(df["total_ads"])
    logit = smf.logit("converted ~ treat + C(most_ads_day) + C(hour_bucket) + log_ads", data=df).fit(disp=False)
    or_ci = np.exp(logit.conf_int().loc["treat"])
    f["adjusted_odds_ratio"] = float(np.exp(logit.params["treat"]))
    f["adjusted_odds_ratio_ci"] = [float(or_ci[0]), float(or_ci[1])]
    f["adjusted_p_value"] = float(logit.pvalues["treat"])
    unadj = smf.logit("converted ~ treat", data=df).fit(disp=False)
    f["unadjusted_odds_ratio"] = float(np.exp(unadj.params["treat"]))
    # Post-stratification: compare groups inside day x exposure cells, weight by the ad group's mix
    cells = df.groupby(["most_ads_day", "ads_bucket", "test_group"], observed=True)["converted"].agg(["mean", "size"]).unstack()
    cells = cells.dropna()
    w = cells[("size", "ad")] / cells[("size", "ad")].sum()
    f["post_stratified_abs_lift_pp"] = float(((cells[("mean", "ad")] - cells[("mean", "psa")]) * w).sum() * 100)
    f["post_stratified_cr_psa"] = float((cells[("mean", "psa")] * w).sum())

    # ---------- 4. Power and sample size ----------
    power = NormalIndPower()
    n_ad, n_psa, p0 = int(main_res["users_ad"]), int(main_res["users_psa"]), float(main_res["cr_psa"])
    es_obs = proportion_effectsize(main_res["cr_ad"], p0)
    f["achieved_power"] = float(power.power(es_obs, nobs1=n_ad, ratio=n_psa / n_ad, alpha=ALPHA))
    # Smallest relative lift this design could reliably detect
    es_mde = power.solve_power(nobs1=n_ad, ratio=n_psa / n_ad, alpha=ALPHA, power=POWER)
    p1_mde = np.sin(np.arcsin(np.sqrt(p0)) + es_mde / 2) ** 2
    f["mde_relative"] = float(p1_mde / p0 - 1)
    f["mde_abs_pp"] = float((p1_mde - p0) * 100)
    # Users needed per group to detect the observed effect with a 50/50 split vs the actual 96/4 split
    n_equal = power.solve_power(effect_size=es_obs, ratio=1, alpha=ALPHA, power=POWER)
    ratio_actual = n_psa / n_ad
    n_ad_actual = power.solve_power(effect_size=es_obs, ratio=ratio_actual, alpha=ALPHA, power=POWER)
    f["sample_needed_equal_split_total"] = float(n_equal * 2)
    f["sample_needed_actual_split_total"] = float(n_ad_actual * (1 + ratio_actual))
    # Sample size table for planning future tests at this baseline
    plan = []
    for rel in [0.05, 0.10, 0.20, 0.30, 0.50]:
        es = proportion_effectsize(p0 * (1 + rel), p0)
        n = power.solve_power(effect_size=es, ratio=1, alpha=ALPHA, power=POWER)
        plan.append({"relative_lift": rel, "users_per_group_50_50": int(np.ceil(n))})
    pd.DataFrame(plan).to_csv(TABLES / "sample_size_plan.csv", index=False)
    f["sample_size_plan"] = plan

    # ---------- 5. Segments ----------
    seg_day = df.groupby("most_ads_day").apply(compare, include_groups=False).reindex(DAYS)
    seg_hour = df.groupby("hour_bucket", observed=True).apply(compare, include_groups=False)
    seg_ads = df.groupby("ads_bucket", observed=True).apply(compare, include_groups=False)
    segments = pd.concat([seg_day.assign(dimension="most_ads_day"), seg_hour.assign(dimension="hour_bucket"),
                          seg_ads.assign(dimension="ads_bucket")])
    segments.index.name = "segment"
    segments = segments.reset_index()
    # Many slices tested at once: correct p-values (Holm) to avoid false discoveries
    segments["p_value_holm"] = multipletests(segments["p_value"], method="holm")[1]
    segments["significant_raw"] = segments["p_value"] < ALPHA
    segments["significant_holm"] = segments["p_value_holm"] < ALPHA
    segments.to_csv(TABLES / "segments.csv", index=False)
    f["segments"] = segments.round(5).to_dict("records")
    f["segments_significant_raw"] = int(segments["significant_raw"].sum())
    f["segments_significant_holm"] = int(segments["significant_holm"].sum())

    # Conversion by exposure in the control group shows the confounder: heavy users convert more anyway
    expo = df.groupby(["ads_bucket", "test_group"], observed=True)["converted"].mean().unstack()
    expo.to_csv(TABLES / "conversion_by_exposure.csv")
    users_by_bucket = df.groupby("ads_bucket", observed=True).size()
    f["user_share_by_ads_bucket"] = (users_by_bucket / users_by_bucket.sum()).round(4).astype(float).to_dict()
    f["conversions_share_by_ads_bucket"] = (df.groupby("ads_bucket", observed=True)["converted"].sum() / df["converted"].sum()).round(4).astype(float).to_dict()
    f["conversion_by_exposure"] = expo.round(5).reset_index().astype({"ads_bucket": str}).to_dict("records")

    (REPORT / "findings.json").write_text(json.dumps(f, indent=2, default=str), encoding="utf-8")

    # ---------- charts ----------
    plt.rcParams.update({"figure.dpi": 130, "axes.spines.top": False, "axes.spines.right": False})
    blue, grey = "#2b6cb0", "#a0aec0"

    fig, ax = plt.subplots(figsize=(6, 4))
    rates = [main_res["cr_psa"] * 100, main_res["cr_ad"] * 100]
    ns = [main_res["users_psa"], main_res["users_ad"]]
    errs = [1.96 * np.sqrt(r / 100 * (1 - r / 100) / n) * 100 for r, n in zip(rates, ns)]
    bars = ax.bar(["PSA (control)", "Ad (treatment)"], rates, yerr=errs, capsize=6, color=[grey, blue])
    for b, r in zip(bars, rates):
        ax.text(b.get_x() + b.get_width() / 2, r / 2, f"{r:.2f}%", ha="center", color="white", fontweight="bold")
    ax.set_ylabel("Conversion rate, %")
    ax.set_title("Conversion by group (95% CI)")
    plt.tight_layout()
    plt.savefig(REPORT / "conversion_by_group.png")
    plt.close()

    fig, ax = plt.subplots(figsize=(8, 4.5))
    s = segments.copy()
    s["label"] = s["dimension"].map({"most_ads_day": "Day", "hour_bucket": "Hour", "ads_bucket": "Ads seen"}) + ": " + s["segment"].astype(str)
    y = np.arange(len(s))[::-1]
    colors = [blue if sig else grey for sig in s["significant_holm"]]
    ax.errorbar(s["abs_lift_pp"], y, xerr=[s["abs_lift_pp"] - s["ci_low_pp"], s["ci_high_pp"] - s["abs_lift_pp"]],
                fmt="none", ecolor="#cbd5e0", capsize=3)
    ax.scatter(s["abs_lift_pp"], y, c=colors, zorder=3)
    ax.axvline(0, color="#718096", lw=1)
    ax.axvline(main_res["abs_lift_pp"], color=blue, lw=1, ls="--", label="Overall lift")
    ax.set_yticks(y)
    ax.set_yticklabels(s["label"], fontsize=8)
    ax.set_xlabel("Absolute lift, percentage points (95% CI)")
    ax.set_title("Lift by segment (blue = significant after Holm correction)")
    ax.legend(loc="lower right", fontsize=8)
    plt.tight_layout()
    plt.savefig(REPORT / "lift_by_segment.png")
    plt.close()

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(expo))
    ax.bar(x - 0.2, expo["psa"] * 100, 0.4, label="PSA (control)", color=grey)
    ax.bar(x + 0.2, expo["ad"] * 100, 0.4, label="Ad (treatment)", color=blue)
    ax.set_xticks(x)
    ax.set_xticklabels(expo.index.astype(str))
    ax.set_xlabel("Total ads seen")
    ax.set_ylabel("Conversion rate, %")
    ax.set_title("Conversion grows with exposure in both groups")
    ax.legend()
    plt.tight_layout()
    plt.savefig(REPORT / "conversion_by_exposure.png")
    plt.close()

    print(json.dumps({k: f[k] for k in ["data_quality", "split", "main", "main_chi2_p", "incremental_conversions",
                                         "incremental_conversions_ci", "rel_lift_bootstrap_ci", "achieved_power",
                                         "mde_relative", "mde_abs_pp", "day_mix_max_gap_pp", "hour_mix_max_gap_pp", "adjusted_odds_ratio", "adjusted_odds_ratio_ci", "adjusted_p_value", "unadjusted_odds_ratio", "post_stratified_abs_lift_pp", "post_stratified_cr_psa", "user_share_by_ads_bucket", "conversions_share_by_ads_bucket", "sample_needed_equal_split_total",
                                         "sample_needed_actual_split_total", "sample_size_plan",
                                         "segments_significant_raw", "segments_significant_holm"]}, indent=1, default=str))
    print(balance.round(4).to_string(index=False))
    print(segments[["dimension", "segment", "users_psa", "cr_ad", "cr_psa", "abs_lift_pp", "ci_low_pp", "ci_high_pp", "p_value", "p_value_holm"]].round(4).to_string(index=False))
    print(expo.round(4))


if __name__ == "__main__":
    main()
