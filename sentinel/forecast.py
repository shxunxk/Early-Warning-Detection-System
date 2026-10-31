"""Forward-looking delinquency forecast: P(customer reaches 30+ DPD within 1 / 3 months).

Unlike `ml.py` (which learns to reproduce the rule tier), this model predicts a real future
outcome. Training data is point-in-time: metrics are recomputed as they stood at each past
month-end and labelled with what actually happened in the following months. Validation is
out-of-time: the most recent labelled months are held out.

Only customers below 30 DPD at the snapshot are eligible ("will a performing / early-arrears
customer become seriously delinquent?"). Customers already 30+ are covered by roll rates.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel import metrics as metrics_mod
from sentinel.ml import FEATURE_COLS

HORIZONS = {"1m": 1, "3m": 3}
EVENT_DPD = 30
FEATURES = FEATURE_COLS + ["is_sme"]


def _customer_dpd(t: dict) -> pd.DataFrame:
    """Customer x month-end max DPD (wide: rows customers, columns month-end dates)."""
    dl = t["delinquency"]
    per = dl.groupby(["customer_id", "snapshot_date"]).days_past_due.max()
    return per.unstack("snapshot_date").sort_index(axis=1)


def _features(m: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    X = m.reindex(columns=FEATURE_COLS).astype(float)
    X["is_sme"] = (customers.set_index("customer_id").customer_type == "SME").reindex(
        X.index).astype(float)
    return X


def build_panel(t: dict, params: dict, as_of, min_month: int = 6) -> pd.DataFrame:
    """Point-in-time training rows for every past month-end with at least 1 month of future."""
    as_of = pd.Timestamp(as_of)
    dpd = _customer_dpd(t)
    dates = [d for d in dpd.columns if d < as_of]
    dates = dates[min_month:]  # need some history before the first snapshot
    rows = []
    for d in dates:
        m = metrics_mod.compute(t, d, params)
        X = _features(m, t["customers"])
        now = dpd[d]
        future = dpd.loc[:, (dpd.columns > d)]
        X["snapshot"] = d
        X["dpd_now"] = now.reindex(X.index)
        for name, h in HORIZONS.items():
            win = future.loc[:, future.columns <= d + pd.offsets.MonthEnd(h)]
            complete = win.shape[1] >= h
            lab = (win.max(axis=1) >= EVENT_DPD).astype(float) if complete else np.nan
            X[f"y_{name}"] = lab if not complete else lab.reindex(X.index)
        rows.append(X[X.dpd_now.notna() & (X.dpd_now < EVENT_DPD)])
    return pd.concat(rows)


def _fit(X: pd.DataFrame, y: pd.Series):
    import lightgbm as lgb
    model = lgb.LGBMClassifier(objective="binary", n_estimators=300, learning_rate=0.03,
                               num_leaves=15, max_depth=4, min_child_samples=40,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                               reg_lambda=1.0, verbose=-1, random_state=42)
    model.fit(X, y)
    return model


def train(panel: pd.DataFrame, test_months: int = 3) -> dict:
    """Out-of-time validation per horizon, then refit on all labelled rows."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    out = {}
    for name in HORIZONS:
        lab = panel[panel[f"y_{name}"].notna()]
        snaps = sorted(lab.snapshot.unique())
        test_snaps = snaps[-test_months:]
        tr, te = lab[~lab.snapshot.isin(test_snaps)], lab[lab.snapshot.isin(test_snaps)]
        y_col = f"y_{name}"
        model = _fit(tr[FEATURES], tr[y_col])
        p = model.predict_proba(te[FEATURES])[:, 1]
        y = te[y_col].values
        k = max(1, int(round(0.1 * len(p))))
        top = np.argsort(-p)[:k]
        val = dict(
            train_rows=len(tr), test_rows=len(te), test_events=int(y.sum()),
            train_period=f"{pd.Timestamp(tr.snapshot.min()).date()} → "
                         f"{pd.Timestamp(tr.snapshot.max()).date()}",
            test_period=f"{pd.Timestamp(min(test_snaps)).date()} → "
                        f"{pd.Timestamp(max(test_snaps)).date()}",
            base_rate=round(100 * float(y.mean()), 2),
            auc=round(float(roc_auc_score(y, p)), 3) if 0 < y.sum() < len(y) else None,
            pr_auc=round(float(average_precision_score(y, p)), 3) if y.sum() else None,
            top_decile_capture=round(100 * float(y[top].sum() / max(y.sum(), 1)), 1),
            predicted_events=round(float(p.sum()), 1), actual_events=int(y.sum()),
        )
        out[name] = dict(model=_fit(lab[FEATURES], lab[y_col]), validation=val)
    return out


def predict(models: dict, m_now: pd.DataFrame, customers: pd.DataFrame,
            current_dpd: pd.Series) -> pd.DataFrame:
    """Probabilities at the as-of date for customers below 30 DPD; NaN for those already 30+."""
    X = _features(m_now, customers)
    eligible = current_dpd.reindex(X.index).fillna(0) < EVENT_DPD
    res = pd.DataFrame(index=X.index)
    for name, md in models.items():
        p = pd.Series(md["model"].predict_proba(X[FEATURES])[:, 1], index=X.index)
        res[f"p_{name}"] = p.where(eligible)
    return res


def drivers(models: dict, m_now: pd.DataFrame, customers: pd.DataFrame, horizon="3m",
            top=3) -> pd.Series:
    """Top SHAP drivers pushing each customer's forecast up (plain-language labels)."""
    import shap
    X = _features(m_now, customers)[FEATURES]
    sv = shap.TreeExplainer(models[horizon]["model"]).shap_values(X)
    sv = sv[1] if isinstance(sv, list) else sv
    sv = pd.DataFrame(sv, index=X.index, columns=FEATURES)
    return sv.apply(lambda r: [LABELS.get(c, c) for c in r[r > 0].sort_values(
        ascending=False).index[:top]], axis=1)


def history(t: dict, as_of) -> pd.DataFrame:
    """Customers newly reaching 30+ DPD in each month (the quantity being forecast)."""
    dpd = _customer_dpd(t)
    dpd = dpd.loc[:, dpd.columns <= pd.Timestamp(as_of)]
    hit = dpd >= EVENT_DPD
    new = hit & ~hit.shift(1, axis=1, fill_value=False)
    s = new.sum()
    return pd.DataFrame({"month_end": s.index, "new_30plus": s.values}).iloc[1:]


LABELS = {
    "late_or_missed_instalments": "late/missed payments", "partial_payments": "partial payments",
    "current_dpd": "current arrears", "dpd_increase_month_ends": "rising DPD",
    "months_since_cure": "recent cure", "inflow_change_3m_vs_prior_3m_pct": "falling inflows",
    "consecutive_inflow_declines": "consecutive inflow drops",
    "months_without_salary": "missing salary", "avg_balance_change_pct": "balance erosion",
    "returned_payments": "bounced payments", "revolving_utilisation_pct": "high utilisation",
    "utilisation_change_pp": "utilisation spike", "debt_burden_ratio_pct": "debt burden",
    "new_facilities_or_enquiries": "new credit", "exposure_growth_minus_income_growth_pp":
    "exposure outpacing income", "bureau_external_max_dpd": "arrears at other lenders",
    "bureau_score_change": "bureau score drop", "months_of_history": "short history",
    "missing_financial_months": "data gaps", "data_completeness_score": "data completeness",
    "is_sme": "business customer",
}
