"""Optional LightGBM second-opinion model + SHAP explanations.

Trained on the scored metrics using a time-split: metrics at as_of - 3m predict
whether the customer will be Amber/Red at as_of. Shown alongside the deterministic
score for comparison — never blended into the warning score.

Class imbalance handled via:
  1. SMOTE oversampling of minority class
  2. scale_pos_weight in LightGBM
  3. Stratified K-fold cross-validation for robust estimation
  4. Threshold tuning to maximise F1
  5. Interaction features for richer signal
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

INTERACTION_PAIRS = [
    ("late_or_missed_instalments", "inflow_change_3m_vs_prior_3m_pct"),
    ("current_dpd", "revolving_utilisation_pct"),
    ("debt_burden_ratio_pct", "avg_balance_change_pct"),
    ("late_or_missed_instalments", "current_dpd"),
    ("consecutive_inflow_declines", "months_without_salary"),
    ("revolving_utilisation_pct", "debt_burden_ratio_pct"),
]


def _available():
    try:
        import lightgbm  # noqa: F401
        import shap  # noqa: F401
        return True
    except ImportError:
        return False


def _add_interactions(X: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Add interaction features (products and ratios) for richer signal."""
    X = X.copy()
    new_features = list(features)
    for a, b in INTERACTION_PAIRS:
        if a in X.columns and b in X.columns:
            name = f"{a}_x_{b}"
            X[name] = X[a] * X[b]
            new_features.append(name)
    # Aggregate risk indicators
    repay_cols = [c for c in ["late_or_missed_instalments", "partial_payments", "current_dpd",
                              "dpd_increase_month_ends"] if c in X.columns]
    if repay_cols:
        X["_repayment_risk_sum"] = X[repay_cols].clip(lower=0).sum(axis=1)
        new_features.append("_repayment_risk_sum")
    cf_cols = [c for c in ["consecutive_inflow_declines", "months_without_salary",
                           "returned_payments"] if c in X.columns]
    if cf_cols:
        X["_cashflow_stress_sum"] = X[cf_cols].clip(lower=0).sum(axis=1)
        new_features.append("_cashflow_stress_sum")
    exp_cols = [c for c in ["revolving_utilisation_pct", "debt_burden_ratio_pct",
                            "new_facilities_or_enquiries"] if c in X.columns]
    if exp_cols:
        X["_exposure_pressure_sum"] = X[exp_cols].clip(lower=0).sum(axis=1)
        new_features.append("_exposure_pressure_sum")
    # Total signals firing (non-zero positive features)
    base_pos = [c for c in features if c in X.columns]
    X["_n_signals_firing"] = (X[base_pos].fillna(0).abs() > 0.01).sum(axis=1)
    new_features.append("_n_signals_firing")
    return X, new_features


def _find_best_threshold(y_true, y_prob) -> float:
    """Find classification threshold that maximises F1 score."""
    from sklearn.metrics import f1_score
    best_f1, best_thr = 0, 0.5
    for thr in np.arange(0.10, 0.90, 0.02):
        preds = (y_prob >= thr).astype(int)
        if preds.sum() == 0:
            continue
        f1 = f1_score(y_true, preds)
        if f1 > best_f1:
            best_f1, best_thr = f1, thr
    return best_thr


def train_model(m_prev: pd.DataFrame, current_view: pd.DataFrame):
    """Train LightGBM on prior-period metrics → current Amber/Red label.

    Uses SMOTE + class weighting + stratified CV + threshold tuning.
    Returns (model_info, feature_names) or (None, None).
    model_info is a dict with 'model', 'threshold', and 'scores'.
    """
    if not _available():
        return None, None
    import lightgbm as lgb
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score, f1_score, precision_score, recall_score

    features = [c for c in FEATURE_COLS if c in m_prev.columns]
    X = m_prev[features].copy()
    y = current_view.reindex(X.index).tier.isin(["Amber", "Red"]).astype(int)
    mask = X.notna().sum(axis=1) >= 5
    X, y = X.loc[mask], y.loc[mask]
    if y.sum() < 5 or len(y) < 30:
        return None, None

    X = X.fillna(-999)
    X, all_features = _add_interactions(X, features)

    n_pos = y.sum()
    n_neg = len(y) - n_pos
    spw = n_neg / n_pos

    # SMOTE oversampling
    try:
        from imblearn.over_sampling import SMOTE
        smote = SMOTE(random_state=42, k_neighbors=min(5, n_pos - 1))
        X_res, y_res = smote.fit_resample(X[all_features], y)
    except ImportError:
        X_res, y_res = X[all_features], y

    params = dict(
        objective="binary", metric="auc", n_estimators=300, max_depth=5,
        learning_rate=0.05, num_leaves=20, min_child_samples=3,
        subsample=0.8, colsample_bytree=0.7, reg_alpha=0.5, reg_lambda=1.0,
        scale_pos_weight=spw, verbose=-1, random_state=42,
    )

    # Stratified 5-fold CV to estimate generalisation
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_auc, cv_f1, cv_prec, cv_rec = [], [], [], []
    cv_thresholds = []

    for train_idx, val_idx in skf.split(X[all_features], y):
        X_tr, X_val = X[all_features].iloc[train_idx], X[all_features].iloc[val_idx]
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]

        # Apply SMOTE on fold training data only
        try:
            from imblearn.over_sampling import SMOTE
            k = min(5, y_tr.sum() - 1)
            if k >= 1:
                sm = SMOTE(random_state=42, k_neighbors=k)
                X_tr_s, y_tr_s = sm.fit_resample(X_tr, y_tr)
            else:
                X_tr_s, y_tr_s = X_tr, y_tr
        except ImportError:
            X_tr_s, y_tr_s = X_tr, y_tr

        fold_model = lgb.LGBMClassifier(**params)
        fold_model.fit(X_tr_s, y_tr_s)
        prob_val = fold_model.predict_proba(X_val)[:, 1]

        thr = _find_best_threshold(y_val.values, prob_val)
        preds = (prob_val >= thr).astype(int)

        cv_auc.append(roc_auc_score(y_val, prob_val))
        cv_f1.append(f1_score(y_val, preds))
        cv_prec.append(precision_score(y_val, preds, zero_division=0))
        cv_rec.append(recall_score(y_val, preds))
        cv_thresholds.append(thr)

    # Train final model on SMOTE-augmented full data
    final_model = lgb.LGBMClassifier(**params)
    final_model.fit(X_res, y_res)

    # Find best threshold on full dataset predictions
    full_prob = final_model.predict_proba(X[all_features])[:, 1]
    best_threshold = _find_best_threshold(y.values, full_prob)

    # Training scores at best threshold
    train_preds = (full_prob >= best_threshold).astype(int)
    scores = dict(
        train_auc=round(roc_auc_score(y, full_prob), 4),
        train_f1=round(f1_score(y, train_preds), 4),
        train_precision=round(precision_score(y, train_preds, zero_division=0), 4),
        train_recall=round(recall_score(y, train_preds), 4),
        cv_auc=round(np.mean(cv_auc), 4),
        cv_auc_std=round(np.std(cv_auc), 4),
        cv_f1=round(np.mean(cv_f1), 4),
        cv_f1_std=round(np.std(cv_f1), 4),
        cv_precision=round(np.mean(cv_prec), 4),
        cv_recall=round(np.mean(cv_rec), 4),
        threshold=round(best_threshold, 2),
        n_train=len(y),
        n_positive=int(y.sum()),
        n_smote=len(y_res),
        class_ratio=round(spw, 2),
    )

    model_info = dict(model=final_model, threshold=best_threshold, scores=scores)
    return model_info, all_features


def predict_proba(model_info, metrics: pd.DataFrame, features: list[str]) -> pd.Series:
    """P(deterioration in 90d) for each customer."""
    if model_info is None:
        return pd.Series(dtype=float)
    model = model_info if not isinstance(model_info, dict) else model_info["model"]
    X = metrics.reindex(columns=features).fillna(-999)
    # Add interaction features if missing
    if any(c not in X.columns for c in features):
        base = [c for c in FEATURE_COLS if c in metrics.columns]
        X, _ = _add_interactions(metrics[base].fillna(-999), base)
        X = X.reindex(columns=features).fillna(-999)
    probs = model.predict_proba(X)[:, 1]
    return pd.Series(probs, index=metrics.index, name="ml_prob")


def predict_class(model_info, metrics: pd.DataFrame, features: list[str]) -> pd.Series:
    """Binary prediction using the tuned threshold."""
    probs = predict_proba(model_info, metrics, features)
    thr = model_info["threshold"] if isinstance(model_info, dict) else 0.5
    return (probs >= thr).astype(int)


def shap_values(model_info, metrics: pd.DataFrame, features: list[str],
                customer_id: str) -> dict | None:
    """SHAP explanation for one customer. Returns dict with values and feature names."""
    if model_info is None:
        return None
    model = model_info if not isinstance(model_info, dict) else model_info["model"]
    try:
        import shap
    except ImportError:
        return None
    base_features = [c for c in FEATURE_COLS if c in metrics.columns]
    X_full, all_feat = _add_interactions(metrics[base_features].fillna(-999), base_features)
    X = X_full.reindex(columns=features).fillna(-999)
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
    feat_names = list(X.columns)
    return dict(
        features=[feat_names[i] for i in top],
        values=[round(float(vals[i]), 4) for i in top],
        base_value=round(float(base), 4),
        raw_values=[round(float(X.at[customer_id, feat_names[i]]), 2)
                    if pd.notna(X.at[customer_id, feat_names[i]]) else None
                    for i in top],
    )


def get_scores(model_info) -> dict | None:
    """Return training and CV scores from the trained model."""
    if model_info is None or not isinstance(model_info, dict):
        return None
    return model_info.get("scores")
