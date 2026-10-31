"""Multi-signal risk correlation: detect combined deteriorating indicators across modules."""

from __future__ import annotations

import pandas as pd

CORRELATION_PATTERNS = [
    {"id": "CORR01", "name": "Cash-flow driven delinquency",
     "modules": ["CFSI", "RBDI"],
     "conditions": {"CFSI": 50, "RBDI": 50},
     "explanation": "Declining income coincides with payment difficulties — "
                    "repayment stress is likely income-driven, not behavioural."},
    {"id": "CORR02", "name": "Leveraging up under stress",
     "modules": ["LPI", "CFSI"],
     "conditions": {"LPI": 50, "CFSI": 30},
     "explanation": "Rising exposure while income declines — customer may be "
                    "borrowing to cover shortfalls."},
    {"id": "CORR03", "name": "Cross-lender distress",
     "modules": ["RBDI", "BUREAU"],
     "conditions": {"RBDI": 30, "BUREAU": 30},
     "explanation": "On-us and off-us delinquency together signal systemic "
                    "repayment difficulty, not an isolated event."},
    {"id": "CORR04", "name": "Full deterioration",
     "modules": ["CFSI", "LPI", "RBDI"],
     "conditions": {"CFSI": 40, "LPI": 40, "RBDI": 40},
     "explanation": "Income, exposure, and repayment all deteriorating simultaneously — "
                    "highest combined risk."},
]

OVERLAP_GROUPS = [
    {"signals": ["RS01", "RS03", "RS04"], "note": "Same underlying payment failure"},
    {"signals": ["RS07", "RS08", "RS09"], "note": "Related income signals"},
    {"signals": ["RS12", "RS13"], "note": "Same revolving facility"},
]


def detect_correlations(view: pd.DataFrame) -> pd.DataFrame:
    """Per customer: matching correlation patterns and combined risk factor.

    Returns DataFrame with columns: correlation_ids, correlation_names,
    narrative, combined_risk_factor, n_modules_stressed.
    """
    out = pd.DataFrame(index=view.index)
    ids_list, names_list, narr_list = [], [], []
    factor_list = []

    for _, row in view.iterrows():
        matched_ids, matched_names, explanations = [], [], []
        for pat in CORRELATION_PATTERNS:
            match = True
            for mod, threshold in pat["conditions"].items():
                val = row.get(mod)
                if val is None or pd.isna(val) or val < threshold:
                    match = False
                    break
            if match:
                matched_ids.append(pat["id"])
                matched_names.append(pat["name"])
                explanations.append(pat["explanation"])
        ids_list.append(matched_ids)
        names_list.append(matched_names)
        narr_list.append(" | ".join(explanations) if explanations else "")

        n_stressed = sum(1 for mod in ["RBDI", "CFSI", "LPI", "BUREAU"]
                         if not pd.isna(row.get(mod, float("nan"))) and row.get(mod, 0) > 30)
        base = min(n_stressed / 4.0, 1.0)
        pattern_boost = min(len(matched_ids) * 0.15, 0.4)
        factor_list.append(min(base + pattern_boost, 1.0))

    out["correlation_ids"] = ids_list
    out["correlation_names"] = names_list
    out["narrative"] = narr_list
    out["combined_risk_factor"] = factor_list
    mods_available = [m for m in ["RBDI", "CFSI", "LPI", "BUREAU"] if m in view.columns]
    out["n_modules_stressed"] = [
        sum(1 for mod in mods_available
            if not pd.isna(view.at[cid, mod]) and view.at[cid, mod] > 30)
        for cid in view.index
    ]
    return out


def overlap_warning(evidence: dict) -> str | None:
    """Advisory text if signals from the same overlap group are double-counting risk."""
    fired = {s["id"] for s in evidence.get("signals", [])
             if s.get("strength") and s["strength"] > 0}
    warnings = []
    for group in OVERLAP_GROUPS:
        overlap = fired & set(group["signals"])
        if len(overlap) >= 2:
            warnings.append(f"{group['note']} ({', '.join(sorted(overlap))})")
    return "; ".join(warnings) if warnings else None


def correlation_summary(view: pd.DataFrame) -> pd.DataFrame:
    """Portfolio-level: how many customers have each correlation pattern."""
    corr = detect_correlations(view)
    counts = {}
    for pat in CORRELATION_PATTERNS:
        counts[pat["id"]] = {
            "name": pat["name"],
            "customer_count": sum(1 for ids in corr.correlation_ids if pat["id"] in ids),
        }
    return pd.DataFrame.from_dict(counts, orient="index")
