"""Optional LightGBM second-opinion model + SHAP explanations.

Trained on the scored metrics using a time-split: metrics at as_of - 3m predict
whether the customer will be Amber/Red at as_of. Shown alongside the deterministic
score for comparison — never blended into the warning score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLS = [
    "late_or_missed_instalments", "partial_payments", "current_dpd",
    "dpd_increase_month_ends", "months_since_cure",
    "inflow_change_3m_vs_prior_3m_pct", "consecutive_inflow_declines",
    "months_without_salary", "avg_balance_change_pct", "returned_payments",
    "revolving_utilisation_pct", "utilisation_change_pp", "debt_burden_ratio_pct",
    "new_facilities_or_enquiries", "exposure_growth_minus_income_growth_pp",
    "bureau_external_max_dpd", "bureau_score_change",
    "months_of_history", "missing_financial_months", "data_completeness_score",
]


def _available():
    try:
        import lightgbm  # noqa: F401
        import shap  # noqa: F401
        return True
    except ImportError:
        return False


def train_model(m_prev: pd.DataFrame, current_view: pd.DataFrame):
    """Train LightGBM on prior-period metrics → current Amber/Red label.

    Returns (model, feature_names) or (None, None) if libraries are unavailable
    or there's insufficient data.
    """
    if not _available():
        return None, None
    import lightgbm as lgb

    features = [c for c in FEATURE_COLS if c in m_prev.columns]
    X = m_prev[features].copy()
    y = current_view.reindex(X.index).tier.isin(["Amber", "Red"]).astype(int)
    mask = X.notna().sum(axis=1) >= 5
    X, y = X.loc[mask], y.loc[mask]
    if y.sum() < 5 or len(y) < 30:
        return None, None

    X = X.fillna(-999)
    params = dict(
        objective="binary", metric="auc", n_estimators=100, max_depth=4,
        learning_rate=0.1, num_leaves=15, min_child_samples=5,
        subsample=0.8, colsample_bytree=0.8, verbose=-1,
    )
    model = lgb.LGBMClassifier(**params)
    model.fit(X, y)
    return model, features


def predict_proba(model, metrics: pd.DataFrame, features: list[str]) -> pd.Series:
    """P(deterioration in 90d) for each customer."""
    if model is None:
        return pd.Series(dtype=float)
    X = metrics[features].fillna(-999)
    probs = model.predict_proba(X)[:, 1]
    return pd.Series(probs, index=metrics.index, name="ml_prob")


def shap_values(model, metrics: pd.DataFrame, features: list[str],
                customer_id: str) -> dict | None:
    """SHAP explanation for one customer. Returns dict with values and feature names."""
    if model is None:
        return None
    try:
        import shap
    except ImportError:
        return None
    X = metrics[features].fillna(-999)
    if customer_id not in X.index:
        return None
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X.loc[[customer_id]])
    if isinstance(sv, list):
        sv = sv[1]
    vals = sv[0]
    base = explainer.expected_value
    if isinstance(base, (list, np.ndarray)):
        base = base[1]
    sorted_idx = np.argsort(-np.abs(vals))
    top = sorted_idx[:10]
    return dict(
        features=[features[i] for i in top],
        values=[round(float(vals[i]), 4) for i in top],
        base_value=round(float(base), 4),
        raw_values=[round(float(metrics.at[customer_id, features[i]]), 2)
                    if pd.notna(metrics.at[customer_id, features[i]]) else None
                    for i in top],
    )
