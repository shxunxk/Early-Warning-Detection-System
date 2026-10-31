"""Intelligent intervention recommendations: map signal patterns to specific actions."""

from __future__ import annotations

import pandas as pd

URGENCY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

SCENARIOS = [
    {
        "id": "S_TEMPORARY_MISS",
        "name": "Temporary payment miss",
        "urgency": "LOW",
        "actions": [
            "Monitor recovery over next 30 days",
            "Verify supporting evidence (salary credit, dispute resolution)",
            "No immediate action required — track for recurrence",
        ],
    },
    {
        "id": "S_REPEATED_DELAYS",
        "name": "Repeated payment delays",
        "urgency": "MEDIUM",
        "actions": [
            "RM review within 5 business days",
            "Assess current repayment capacity",
            "Consider restructuring if income remains stable",
            "Request updated financial documents",
        ],
    },
    {
        "id": "S_DECLINING_INCOME",
        "name": "Declining income / cash-flow",
        "urgency": "HIGH",
        "actions": [
            "Request updated financials within 10 days",
            "Review employment / business status",
            "Assess adequacy of existing limits",
            "Consider proactive restructuring before delinquency",
        ],
    },
    {
        "id": "S_INCREASING_EXPOSURE",
        "name": "Increasing exposure",
        "urgency": "MEDIUM",
        "actions": [
            "Review all facility limits",
            "Assess total leverage across lenders",
            "Consider limit reduction or moratorium on new drawdowns",
        ],
    },
    {
        "id": "S_SUSTAINED_HIGH_RISK",
        "name": "Sustained high risk",
        "urgency": "CRITICAL",
        "actions": [
            "Escalate to credit committee within 48 hours",
            "Prepare full risk assessment memo",
            "Evaluate collateral adequacy",
            "Initiate recovery / restructuring dialogue",
        ],
    },
    {
        "id": "S_INSUFFICIENT_INFO",
        "name": "Insufficient information",
        "urgency": "MEDIUM",
        "actions": [
            "Request updated financial documents within 15 days",
            "Request fresh bureau report",
            "Flag for priority data collection",
        ],
    },
]


def _signal_fired(evidence: dict, signal_id: str) -> bool:
    for s in evidence.get("signals", []):
        if s["id"] == signal_id and s.get("strength") and s["strength"] > 0:
            return True
    return False


def recommend(view_row: pd.Series, evidence: dict) -> list[dict]:
    """Return matching scenario recommendations for a single customer."""
    matched = []
    tier = view_row.get("tier", "Green")
    trajectory = view_row.get("trajectory", "Stable")
    confidence = view_row.get("confidence", "High")
    dpd = view_row.get("current_dpd", 0) or 0
    late = view_row.get("late_or_missed_instalments", 0) or 0

    if dpd == 0 and late <= 1 and view_row.get("CFSI", 0) < 50:
        isolated = view_row.get("isolated_delay_recovered", 0)
        if isolated or (late == 1 and dpd == 0):
            matched.append(_scenario("S_TEMPORARY_MISS",
                                     ["isolated_delay_recovered", "dpd=0"]))

    if late >= 2:
        matched.append(_scenario("S_REPEATED_DELAYS",
                                 [f"late_missed={late}"]))

    income_signals = ["RS07", "RS08", "RS09"]
    if any(_signal_fired(evidence, s) for s in income_signals):
        fired = [s for s in income_signals if _signal_fired(evidence, s)]
        matched.append(_scenario("S_DECLINING_INCOME", fired))

    exposure_signals = ["RS12", "RS13", "RS14", "RS15", "RS16"]
    n_exp = sum(1 for s in exposure_signals if _signal_fired(evidence, s))
    if n_exp >= 2:
        fired = [s for s in exposure_signals if _signal_fired(evidence, s)]
        matched.append(_scenario("S_INCREASING_EXPOSURE", fired))

    if tier in ("Amber", "Red") and trajectory in ("Deteriorating", "Sustained-Deteriorating"):
        matched.append(_scenario("S_SUSTAINED_HIGH_RISK",
                                 [f"tier={tier}", f"trajectory={trajectory}"]))

    if confidence == "Low":
        matched.append(_scenario("S_INSUFFICIENT_INFO", ["confidence=Low"]))

    matched.sort(key=lambda s: URGENCY_ORDER.get(s["urgency"], 0), reverse=True)
    return matched


def _scenario(scenario_id: str, indicators: list[str]) -> dict:
    tpl = next(s for s in SCENARIOS if s["id"] == scenario_id)
    return {**tpl, "supporting_indicators": indicators}


def recommend_bulk(view: pd.DataFrame) -> pd.Series:
    """Apply recommend() to all customers. Returns Series of recommendation lists."""
    results = {}
    for cid in view.index:
        row = view.loc[cid]
        evidence = row.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        results[cid] = recommend(row, evidence)
    return pd.Series(results, name="recommendations")


def primary_recommendation(recommendations: list[dict]) -> dict:
    """Select the highest-urgency recommendation."""
    if not recommendations:
        return {}
    return recommendations[0]
