"""Risk prioritisation: composite score combining severity, exposure, probability, trajectory."""

from __future__ import annotations

import numpy as np
import pandas as pd

TRAJECTORY_WEIGHTS = {
    "Sustained-Deteriorating": 1.0,
    "Deteriorating": 0.85,
    "Volatile": 0.65,
    "New": 0.50,
    "Stable": 0.40,
    "Recovering": 0.30,
    "Improving": 0.20,
    "Sustained-Improving": 0.10,
}

COMPONENT_WEIGHTS = {
    "severity": 0.30,
    "exposure": 0.25,
    "probability": 0.25,
    "trajectory": 0.15,
    "correlation": 0.05,
}


def composite_priority(
    view: pd.DataFrame,
    correlation_factors: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute composite priority score (0-100) for each customer.

    Returns DataFrame indexed like view with:
    priority_score, priority_rank, and per-component scores.
    """
    out = pd.DataFrame(index=view.index)

    out["severity_component"] = (view.risk_score / 100).clip(0, 1)

    q95 = view.outstanding.quantile(0.95)
    out["exposure_component"] = (view.outstanding / q95).clip(0, 1) if q95 > 0 else 0

    # Prefer the purpose-built delinquency forecast, then the ML second opinion, then roll rates.
    prob = None
    for col in ("p_3m", "ml_prob", "roll_prob"):
        cand = view.get(col)
        if cand is not None and pd.to_numeric(cand, errors="coerce").notna().any():
            prob = pd.to_numeric(cand, errors="coerce")
            break
    if prob is None:
        prob = pd.Series(0.0, index=view.index)
    out["probability_component"] = prob.fillna(0).clip(0, 1)

    out["trajectory_component"] = view.trajectory.map(TRAJECTORY_WEIGHTS).fillna(0.5)

    if correlation_factors is not None:
        out["correlation_component"] = correlation_factors.reindex(view.index).fillna(0).clip(0, 1)
    else:
        out["correlation_component"] = 0.0

    w = COMPONENT_WEIGHTS
    raw = (w["severity"] * out.severity_component
           + w["exposure"] * out.exposure_component
           + w["probability"] * out.probability_component
           + w["trajectory"] * out.trajectory_component
           + w["correlation"] * out.correlation_component)
    out["priority_score"] = (raw * 100).clip(0, 100).round(1)
    out["priority_rank"] = out.priority_score.rank(ascending=False, method="min").astype(int)
    return out


def priority_quadrants(view: pd.DataFrame, priority: pd.DataFrame) -> pd.Series:
    """Assign each customer to a 2x2 quadrant: impact (exposure) × probability.

    Returns Series with values: 'High-Impact High-Prob', 'High-Impact Low-Prob',
    'Low-Impact High-Prob', 'Low-Impact Low-Prob'.
    """
    exp_med = view.outstanding.median()
    prob_col = priority.probability_component
    prob_med = prob_col.median()
    hi_exp = view.outstanding >= exp_med
    hi_prob = prob_col >= prob_med
    labels = np.select(
        [hi_exp & hi_prob, hi_exp & ~hi_prob, ~hi_exp & hi_prob],
        ["High-Impact High-Prob", "High-Impact Low-Prob", "Low-Impact High-Prob"],
        "Low-Impact Low-Prob",
    )
    return pd.Series(labels, index=view.index, name="priority_quadrant")
