"""Portfolio-level insights: roll-rate matrix (S5), emerging clusters (S8), ground-truth eval."""

from __future__ import annotations

import numpy as np
import pandas as pd

BUCKETS = ["Current", "1-30", "31-60", "61-90", "91-180", "180+"]


def bucket(dpd: pd.Series) -> pd.Series:
    return pd.cut(dpd, [-1, 0, 30, 60, 90, 180, 10**6], labels=BUCKETS).astype(str)


def roll_rates(t: dict, as_of) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Month-to-month bucket transition matrix per portfolio, from facility snapshots."""
    dl = t["delinquency"][t["delinquency"].snapshot_date <= pd.Timestamp(as_of)].copy()
    dl = dl.merge(t["facilities"][["facility_id", "portfolio"]], on="facility_id", how="left")
    dl = dl.sort_values(["facility_id", "snapshot_date"])
    dl["b"] = bucket(dl.days_past_due)
    dl["b_next"] = dl.groupby("facility_id").b.shift(-1)
    tr = dl.dropna(subset=["b_next"])
    matrix = pd.crosstab(tr.b, tr.b_next, normalize="index").reindex(
        index=BUCKETS, columns=BUCKETS).fillna(0)
    order = {b: i for i, b in enumerate(BUCKETS)}
    tr["worse"] = tr.b_next.map(order) > tr.b.map(order)
    p_worse = tr.groupby(["portfolio", "b"]).worse.mean().rename("p_roll").reset_index()
    return matrix, p_worse


def customer_roll_prob(t: dict, as_of, p_worse: pd.DataFrame) -> pd.Series:
    dl = t["delinquency"][t["delinquency"].snapshot_date <= pd.Timestamp(as_of)]
    last = dl.sort_values("snapshot_date").groupby("facility_id").tail(1)
    last = last.merge(t["facilities"][["facility_id", "portfolio"]], on="facility_id")
    last["b"] = bucket(last.days_past_due)
    last = last.merge(p_worse, on=["portfolio", "b"], how="left")
    return last.groupby("customer_id").p_roll.max()


def wilson_low(k, n, z=1.96):
    p = k / n
    den = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    spread = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return (centre - spread) / den


SEGMENTS = [("employment_or_business_category", "emirate"),
            ("primary_product", "emirate"),
            ("employment_or_business_category",),
            ("customer_segment", "portfolio")]


def clusters(view: pd.DataFrame, prev_at_risk: pd.Series, min_at_risk: int = 3):
    """Segments whose at-risk rate is significantly above the portfolio rate."""
    v = view.copy()
    v["at_risk"] = v.tier.isin(["Amber", "Red"])
    v["was_at_risk"] = prev_at_risk.reindex(v.index).fillna(False)
    base = v.at_risk.mean()
    rows = []
    for dims in SEGMENTS:
        g = v.groupby(list(dims)).agg(n=("at_risk", "size"), at_risk=("at_risk", "sum"),
                                      was=("was_at_risk", "sum"),
                                      exposure_at_risk=("outstanding", lambda s: s[
                                          v.loc[s.index, "at_risk"]].sum()))
        g = g[(g.n >= 5)]
        for key, r in g.iterrows():
            key = key if isinstance(key, tuple) else (key,)
            rate = r.at_risk / r.n
            lo = wilson_low(r.at_risk, r.n)
            rows.append(dict(segment=" · ".join(f"{k}" for k in key),
                             dimensions=" × ".join(dims), customers=int(r.n),
                             at_risk=int(r.at_risk), at_risk_rate=round(100 * rate, 1),
                             portfolio_rate=round(100 * base, 1),
                             lift=round(rate / base, 2) if base else np.nan,
                             wilson_low=round(100 * lo, 1),
                             momentum_pp=round(100 * (r.at_risk - r.was) / r.n, 1),
                             exposure_at_risk=round(r.exposure_at_risk, 0),
                             emerging=bool(r.at_risk >= min_at_risk and lo > base)))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["emerging", "lift"], ascending=False).reset_index(drop=True)


GT_MAP = {"Low": "Green", "Medium": "Watch", "High": "Amber", "Critical": "Red"}


def evaluate(view: pd.DataFrame, truth: pd.DataFrame) -> dict:
    truth = truth.set_index("customer_id")
    d = view.join(truth.drop(columns=[c for c in truth.columns if c in view.columns]),
                  how="inner")
    if d.empty:
        return {}
    d["expected"] = d.expected_risk_band.map(GT_MAP)
    tiers = ["Green", "Watch", "Amber", "Red"]
    cm = pd.crosstab(d.expected, d.tier).reindex(index=tiers, columns=tiers, fill_value=0)
    hi = d.expected.isin(["Amber", "Red"])
    rank = {t: i for i, t in enumerate(tiers)}
    diff = (d.tier.map(rank) - d.expected.map(rank)).abs()
    low_data = d.expected_data_sufficiency == "INSUFFICIENT"
    traps = d.archetype.isin(["ISOLATED_EXCEPTION", "SME_SEASONAL", "SALARY_ELSEWHERE",
                              "THIN_FILE", "SME_THIN_FILE", "CURED"])
    return dict(
        confusion=cm,
        catch_rate=round(100 * d[hi].tier.isin(["Amber", "Red"]).mean(), 1),
        false_alarm_rate=round(100 * d[d.expected == "Green"].tier.isin(
            ["Amber", "Red"]).mean(), 1),
        exact=round(100 * (diff == 0).mean(), 1),
        within_one=round(100 * (diff <= 1).mean(), 1),
        thin_file_flagged=int(d[low_data].tier.isin(["Amber", "Red"]).sum()),
        thin_file_total=int(low_data.sum()),
        trap_flagged=int(d[traps].tier.isin(["Amber", "Red"]).sum()),
        trap_total=int(traps.sum()),
        by_archetype=pd.crosstab(d.archetype, d.tier).reindex(columns=tiers, fill_value=0),
    )
