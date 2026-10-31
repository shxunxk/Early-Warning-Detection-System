"""Rules → signals → score → mitigation → triggers → confidence → tier (the cheap stage)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

TIERS = ["Green", "Watch", "Amber", "Red"]
MODULES = {"REPAYMENT": "RBDI", "CASH_FLOW": "CFSI", "EXPOSURE": "LPI", "BUREAU": "BUREAU"}
UNITS = {"pct": "%", "pp": " pp", "dpd": " days"}


def load_rules(path) -> dict:
    with open(path) as f:
        return json.load(f)


def strength(x: pd.Series, op: str, thr: float, sev: float) -> pd.Series:
    """0 below threshold, 0.5 at threshold, 1.0 at severe; NaN = not evaluated."""
    x = pd.to_numeric(x, errors="coerce")
    if op in (">=", ">"):
        hit = x >= thr if op == ">=" else x > thr
        span = (x - thr) / (sev - thr) if sev != thr else 1.0
    elif op in ("<=", "<"):
        hit = x <= thr if op == "<=" else x < thr
        span = (thr - x) / (thr - sev) if sev != thr else 1.0
    else:  # ==
        return (x == thr).astype(float).where(x.notna())
    s = np.where(hit, 0.5 + 0.5 * np.clip(span, 0, 1), 0.0)
    return pd.Series(s, index=x.index).where(x.notna())


def _fmt(metric: str, v) -> str:
    if pd.isna(v):
        return "n/a"
    unit = " months" if metric.endswith("month_ends") or metric.startswith("months_") else \
        next((u for k, u in UNITS.items() if k in metric), "")
    return f"{v:,.0f}{unit}" if abs(v) >= 10 or float(v).is_integer() else f"{v:,.1f}{unit}"


def score(metrics: pd.DataFrame, rules: dict, customers: pd.DataFrame) -> pd.DataFrame:
    sigs = [s for s in rules["signals"] if s.get("enabled", True)]
    out = pd.DataFrame(index=metrics.index)
    S = {}
    for s in sigs:
        col = metrics.get(s["metric"], pd.Series(np.nan, index=metrics.index))
        st = strength(col, s["operator"], float(s["threshold"]),
                      float(s.get("severe", s["threshold"])))
        if s["signal_id"] == "RS03":  # current DPD scaled by bucket
            d = pd.to_numeric(col, errors="coerce")
            st = pd.Series(np.select([d >= 61, d >= 31, d >= 1], [1.0, 0.75, 0.5], 0.0),
                           index=d.index).where(d.notna())
        S[s["signal_id"]] = st
    strength_df = pd.DataFrame(S)
    weights = pd.Series({s["signal_id"]: float(s["weight"]) for s in sigs})
    points = strength_df.fillna(0) * weights

    pos = [s["signal_id"] for s in sigs if s["weight"] > 0]
    mitig_ids = [s["signal_id"] for s in sigs if s["weight"] < 0]
    out["raw_score"] = points[pos].sum(axis=1)

    for cat, name in MODULES.items():
        ids = [s["signal_id"] for s in sigs if s["category"] == cat and s["weight"] > 0]
        if not ids:
            continue
        avail = (strength_df[ids].notna() * weights[ids]).sum(axis=1)
        out[name] = (points[ids].sum(axis=1) / avail.replace(0, np.nan) * 100).round(0)

    # S4 mitigation, blocked when underlying stress exists.
    mitig = points[mitig_ids].sum(axis=1) if mitig_ids else 0
    guard = (out.get("CFSI", 0).fillna(0) > 50) | (out.get("LPI", 0).fillna(0) > 50)
    verified = metrics.get("_evidence_verified", pd.Series(False, index=metrics.index))
    verified = verified.reindex(out.index).fillna(False).astype(bool)
    mitig = mitig * np.where(verified, 1.5, 1.0)
    out["mitigation"] = np.where(guard, 0.0, mitig)
    out["mitigation_blocked"] = guard & (pd.Series(mitig, index=out.index) < 0)
    out["risk_score"] = (out.raw_score + out.mitigation).clip(0, 100).round(0)

    bands = sorted(rules["score_bands"], key=lambda b: b["min_score"])
    tier_idx = np.zeros(len(out), dtype=int)
    for i, b in enumerate(bands):
        tier_idx[out.risk_score.values >= b["min_score"]] = i
    names = [b["band"] for b in bands]
    dpd = metrics.current_dpd.reindex(out.index).fillna(0)
    triggers = pd.Series("", index=out.index)
    red, amber = names.index("Red"), names.index("Amber")
    t_red = dpd >= 90
    t_amb = (dpd >= 30) & (tier_idx < amber)
    tier_idx = np.where(t_red, red, np.where(t_amb, amber, tier_idx))
    triggers[t_red] = "DPD >= 90"
    triggers[t_amb & ~t_red] = "DPD >= 30"

    dcs = metrics.data_completeness_score.reindex(out.index)
    out["dcs"] = dcs
    out["confidence"] = np.select([dcs >= 75, dcs >= 40], ["High", "Medium"], "Low")
    hard = (dpd > 0) | (strength_df.get("RS01", 0).fillna(0) > 0)
    cap = (out.confidence == "Low") & ~hard & (tier_idx > names.index("Watch"))
    tier_idx = np.where(cap, names.index("Watch"), tier_idx)
    out["insufficient_evidence"] = (out.confidence == "Low") & ~hard
    out["tier"] = [names[i] for i in tier_idx]
    out["tier_rank"] = tier_idx
    out["trigger"] = triggers
    band_info = {b["band"]: b for b in bands}
    out["monitoring_status"] = out.tier.map(lambda b: band_info[b]["monitoring_status"])
    out["recommended_action"] = out.tier.map(lambda b: band_info[b]["recommended_action"])

    # Reasons + evidence.
    meta = {s["signal_id"]: s for s in sigs}
    reasons, evidence, n_fired = [], [], []
    for cid in out.index:
        row_pts = points.loc[cid]
        fired = row_pts[row_pts != 0].sort_values(key=lambda v: -v.abs())
        rs = []
        for sid, pts in fired.items():
            s = meta[sid]
            v = metrics.at[cid, s["metric"]]
            rs.append(f"{s['name']}: {_fmt(s['metric'], v)} "
                      f"(threshold {s['operator']} {_fmt(s['metric'], s['threshold'])})")
        reasons.append(rs)
        n_fired.append(int((row_pts > 0).sum()))
        evidence.append(dict(
            signals=[dict(id=sid, name=meta[sid]["name"], category=meta[sid]["category"],
                          metric=meta[sid]["metric"],
                          value=None if pd.isna(metrics.at[cid, meta[sid]["metric"]]) else
                          float(metrics.at[cid, meta[sid]["metric"]]),
                          operator=meta[sid]["operator"], threshold=meta[sid]["threshold"],
                          strength=None if pd.isna(strength_df.at[cid, sid]) else
                          round(float(strength_df.at[cid, sid]), 2),
                          points=round(float(points.at[cid, sid]), 1))
                     for sid in strength_df.columns],
            raw_score=round(float(out.at[cid, "raw_score"]), 1),
            mitigation=round(float(out.at[cid, "mitigation"]), 1),
            mitigation_blocked=bool(out.at[cid, "mitigation_blocked"]),
            trigger=out.at[cid, "trigger"], risk_score=float(out.at[cid, "risk_score"]),
            tier=out.at[cid, "tier"], confidence=out.at[cid, "confidence"],
            data_completeness_score=None if pd.isna(dcs.at[cid]) else float(dcs.at[cid]),
            current_dpd=float(dpd.at[cid]),
            indices={k: (None if pd.isna(out.at[cid, k]) else float(out.at[cid, k]))
                     for k in MODULES.values() if k in out},
        ))
    out["reasons"] = reasons
    out["signals_fired"] = n_fired
    out["evidence"] = evidence
    return out


def trajectory(now: pd.DataFrame, before: pd.DataFrame) -> pd.Series:
    prev = before.risk_score.reindex(now.index)
    prev_rank = before.tier_rank.reindex(now.index)
    d = now.risk_score - prev
    t = np.select([prev.isna(), (prev_rank >= 2) & (now.tier_rank < 2), d >= 10, d <= -10],
                  ["New", "Recovering", "Deteriorating", "Improving"], "Stable")
    return pd.Series(t, index=now.index)
