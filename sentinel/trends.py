"""Historical risk trend analysis: multi-period scoring, extended trajectory, indicator timelines."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel import metrics as met, scoring


def compute_history(t: dict, rules: dict, as_of, months_back: int = 12,
                    step_months: int = 3, params: dict | None = None) -> pd.DataFrame:
    """Score the portfolio at each step from (as_of - months_back) to as_of.

    Returns DataFrame indexed by (customer_id, period_date) with:
    risk_score, tier, tier_rank, RBDI, CFSI, LPI, BUREAU, signals_fired, reasons.
    """
    # Month-end anchoring: metrics.compute aligns windows on month-end periods, so a
    # mid-month as_of drops the current month and misaligns the year-on-year lag window.
    as_of = pd.Timestamp(as_of) + pd.offsets.MonthEnd(0)
    params = params or rules.get("parameters", {})
    customers = t["customers"]
    dates = pd.date_range(end=as_of, periods=months_back // step_months + 1,
                          freq=f"{step_months}ME")
    frames = []
    for dt in dates:
        m = met.compute(t, dt, params)
        v = scoring.score(m, rules, customers)
        v["period_date"] = dt
        keep = ["risk_score", "tier", "tier_rank", "signals_fired", "period_date"]
        for idx in scoring.MODULES.values():
            if idx in v.columns:
                keep.append(idx)
        frames.append(v[keep])
    history = pd.concat(frames)
    history.index.name = "customer_id"
    return history.reset_index().set_index(["customer_id", "period_date"]).sort_index()


def score_deltas(history: pd.DataFrame, customer_id: str) -> pd.DataFrame:
    """Per-period score and module index changes for one customer."""
    if customer_id not in history.index.get_level_values(0):
        return pd.DataFrame()
    h = history.loc[customer_id].sort_index()
    out = pd.DataFrame(index=h.index)
    out["risk_score"] = h.risk_score
    out["score_delta"] = h.risk_score.diff()
    out["tier"] = h.tier
    out["tier_change"] = h.tier_rank.diff().fillna(0).astype(int)
    for idx in scoring.MODULES.values():
        if idx in h.columns:
            out[idx] = h[idx]
            out[f"{idx}_delta"] = h[idx].diff()
    return out


def extended_trajectory(history: pd.DataFrame) -> pd.Series:
    """Multi-period trajectory using OLS slope + volatility on score series.

    Labels: Sustained-Deteriorating, Deteriorating, Volatile,
    Stable, Improving, Sustained-Improving, Recovering, New.
    """
    results = {}
    for cid in history.index.get_level_values(0).unique():
        h = history.loc[cid].sort_index()
        scores = h.risk_score.values
        n = len(scores)
        if n < 2:
            results[cid] = "New"
            continue
        x = np.arange(n, dtype=float)
        slope = np.polyfit(x, scores, 1)[0]
        std = scores.std()
        last_delta = scores[-1] - scores[-2] if n >= 2 else 0

        if std > 15 and abs(slope) < 3:
            results[cid] = "Volatile"
        elif slope >= 5:
            results[cid] = "Sustained-Deteriorating" if n >= 3 and all(
                np.diff(scores) > 0) else "Deteriorating"
        elif slope <= -5:
            if h.tier_rank.iloc[0] >= 2 and h.tier_rank.iloc[-1] < 2:
                results[cid] = "Recovering"
            else:
                results[cid] = "Sustained-Improving" if n >= 3 and all(
                    np.diff(scores) < 0) else "Improving"
        else:
            results[cid] = "Stable"
    return pd.Series(results, name="trajectory")


def indicator_timeline(metrics_row: pd.Series) -> pd.DataFrame:
    """Extract private time-series columns from a metrics row into a monthly DataFrame.

    Columns: dpd, inflow, balance, utilisation (12 months, oldest first).
    """
    out = {}
    for col, label in [("_dpd_history", "dpd"), ("_inflow_series", "inflow"),
                        ("_balance_series", "balance"), ("_util_series", "utilisation")]:
        val = metrics_row.get(col)
        if val is not None and isinstance(val, list) and len(val) > 0:
            out[label] = val
    if not out:
        return pd.DataFrame()
    max_len = max(len(v) for v in out.values())
    for k, v in out.items():
        if len(v) < max_len:
            out[k] = [np.nan] * (max_len - len(v)) + v
    return pd.DataFrame(out)
