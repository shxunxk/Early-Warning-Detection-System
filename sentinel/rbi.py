"""RBI Credit Risk EWS: SMA classification, migration matrix, distribution, and reporting.

SMA (Special Mention Account) categories per RBI norms:
  Standard: DPD = 0
  SMA-0:    DPD 1-30
  SMA-1:    DPD 31-60
  SMA-2:    DPD 61-90
  NPA:      DPD 91+

Thresholds are configurable via the rbi_ews_config section in the rules file.
Amounts are INR, matching the dataset.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

import numpy as np
import pandas as pd

SMA_CATEGORIES = ["Standard", "SMA-0", "SMA-1", "SMA-2", "NPA"]

DEFAULT_CONFIG = {
    "alert_examination_tat_days": 30,
    "escalation_deadline_days": 7,
    "resolution_deadline_days": 180,
    "high_exposure_threshold_inr": 25_000_000,
    "sma_buckets": [[0, 0, "Standard"], [1, 30, "SMA-0"], [31, 60, "SMA-1"],
                     [61, 90, "SMA-2"], [91, 999_999, "NPA"]],
}


def config(rules: dict | None) -> dict:
    if rules and "rbi_ews_config" in rules:
        cfg = {**DEFAULT_CONFIG, **rules["rbi_ews_config"]}
    else:
        cfg = dict(DEFAULT_CONFIG)
    return cfg


def sma_classify(dpd: pd.Series, cfg_in: dict | None = None) -> pd.Series:
    """Map DPD values to SMA categories."""
    cfg = config(cfg_in)
    buckets = cfg["sma_buckets"]
    cuts = [-1] + [b[1] for b in buckets]
    labels = [b[2] for b in buckets]
    return pd.cut(dpd.fillna(0).clip(lower=0), bins=cuts, labels=labels, right=True).astype(str)


def sma_distribution(t: dict, as_of, cfg_in: dict | None = None) -> pd.DataFrame:
    """Portfolio SMA distribution: facility count, customer count, outstanding by SMA category."""
    dl = t["delinquency"]
    dl = dl[dl.snapshot_date <= pd.Timestamp(as_of)]
    last = dl.sort_values("snapshot_date").groupby("facility_id").tail(1)
    fa = t["facilities"][["facility_id", "customer_id", "outstanding_balance", "portfolio"]].copy()
    fa["outstanding"] = pd.to_numeric(fa["outstanding_balance"], errors="coerce").fillna(0)
    merged = last[["facility_id", "days_past_due"]].merge(fa, on="facility_id", how="left")
    merged["sma"] = sma_classify(merged.days_past_due, cfg_in)
    agg = merged.groupby("sma").agg(
        facility_count=("facility_id", "nunique"),
        customer_count=("customer_id", "nunique"),
        outstanding_amount=("outstanding", "sum"),
    ).reindex(SMA_CATEGORIES).fillna(0)
    total = agg.outstanding_amount.sum()
    agg["pct_of_portfolio"] = (agg.outstanding_amount / total * 100).round(1) if total else 0
    return agg.reset_index().rename(columns={"sma": "sma_category"})


def sma_distribution_by_portfolio(t: dict, as_of, cfg_in: dict | None = None) -> pd.DataFrame:
    """SMA distribution broken down by portfolio."""
    dl = t["delinquency"][t["delinquency"].snapshot_date <= pd.Timestamp(as_of)]
    last = dl.sort_values("snapshot_date").groupby("facility_id").tail(1)
    fa = t["facilities"][["facility_id", "customer_id", "outstanding_balance", "portfolio"]].copy()
    fa["outstanding"] = pd.to_numeric(fa["outstanding_balance"], errors="coerce").fillna(0)
    merged = last[["facility_id", "days_past_due"]].merge(fa, on="facility_id", how="left")
    merged["sma"] = sma_classify(merged.days_past_due, cfg_in)
    return merged.groupby(["portfolio", "sma"]).agg(
        facility_count=("facility_id", "nunique"),
        outstanding=("outstanding", "sum"),
    ).reset_index().rename(columns={"sma": "sma_category"})


def sma_migration_matrix(t: dict, as_of, period_months: int = 1,
                         cfg_in: dict | None = None) -> pd.DataFrame:
    """Month-to-month SMA category transition matrix.

    Rows = SMA this period, Columns = SMA next period.
    Values = fraction of facilities that transitioned.
    """
    dl = t["delinquency"][t["delinquency"].snapshot_date <= pd.Timestamp(as_of)].copy()
    dl = dl.sort_values(["facility_id", "snapshot_date"])
    dl["sma"] = sma_classify(dl.days_past_due, cfg_in)
    dl["sma_next"] = dl.groupby("facility_id").sma.shift(-1)
    tr = dl.dropna(subset=["sma_next"])
    matrix = pd.crosstab(tr.sma, tr.sma_next, normalize="index").reindex(
        index=SMA_CATEGORIES, columns=SMA_CATEGORIES).fillna(0)
    return matrix


def sma_trend(t: dict, as_of, months_back: int = 12,
              cfg_in: dict | None = None) -> pd.DataFrame:
    """Monthly SMA distribution over time for trend charts."""
    as_of = pd.Timestamp(as_of)
    dl = t["delinquency"].copy()
    fa = t["facilities"][["facility_id", "outstanding_balance"]].copy()
    fa["outstanding"] = pd.to_numeric(fa["outstanding_balance"], errors="coerce").fillna(0)
    dl = dl[dl.snapshot_date <= as_of]
    dl = dl[dl.snapshot_date > as_of - pd.DateOffset(months=months_back)]
    dl["sma"] = sma_classify(dl.days_past_due, cfg_in)
    dl = dl[["facility_id", "snapshot_date", "sma"]].merge(fa, on="facility_id", how="left")
    dl["month"] = dl.snapshot_date.dt.to_period("M").dt.to_timestamp()
    agg = dl.groupby(["month", "sma"]).agg(
        count=("facility_id", "nunique"),
        outstanding=("outstanding", "sum"),
    ).reset_index().rename(columns={"sma": "sma_category"})
    return agg


def ews_compliance_summary(view: pd.DataFrame, as_of, cfg_in: dict | None = None) -> dict:
    """EWS compliance metrics: TAT status, red-flag candidates, overdue alerts."""
    cfg = config(cfg_in)
    as_of = pd.Timestamp(as_of)
    tat_days = cfg["alert_examination_tat_days"]
    threshold = cfg["high_exposure_threshold_inr"]

    at_risk = view[view.tier.isin(["Amber", "Red"])]
    high_exposure = at_risk[at_risk.outstanding >= threshold]

    pre_delinquent = view[(view.current_dpd == 0) & view.tier.isin(["Watch", "Amber", "Red"])]
    already_delinquent = view[view.current_dpd > 0]

    return dict(
        total_customers=len(view),
        at_risk_count=len(at_risk),
        at_risk_exposure=float(at_risk.outstanding.sum()),
        high_exposure_at_risk=len(high_exposure),
        high_exposure_amount=float(high_exposure.outstanding.sum()),
        pre_delinquent_count=len(pre_delinquent),
        pre_delinquent_exposure=float(pre_delinquent.outstanding.sum()),
        already_delinquent_count=len(already_delinquent),
        already_delinquent_exposure=float(already_delinquent.outstanding.sum()),
        tat_days=tat_days,
        exposure_threshold=threshold,
    )


def generate_report(t: dict, view: pd.DataFrame, as_of,
                    rules: dict | None = None) -> dict:
    """Compile full RBI EWS report data for rendering or export."""
    cfg = config(rules)
    as_of_ts = pd.Timestamp(as_of)
    return dict(
        distribution=sma_distribution(t, as_of_ts, cfg),
        distribution_by_portfolio=sma_distribution_by_portfolio(t, as_of_ts, cfg),
        migration_matrix=sma_migration_matrix(t, as_of_ts, cfg_in=cfg),
        trend=sma_trend(t, as_of_ts, cfg_in=cfg),
        compliance=ews_compliance_summary(view, as_of_ts, cfg),
        metadata=dict(
            report_date=datetime.now().isoformat(),
            as_of=str(as_of_ts.date()),
            generated_by="Sentinel EWS",
        ),
    )


def report_to_excel(report: dict) -> bytes:
    """Convert report dict to downloadable Excel workbook."""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        report["distribution"].to_excel(writer, sheet_name="SMA Distribution", index=False)
        if "distribution_by_portfolio" in report:
            report["distribution_by_portfolio"].to_excel(
                writer, sheet_name="SMA by Portfolio", index=False)
        mig = report["migration_matrix"]
        (mig * 100).round(1).to_excel(writer, sheet_name="Migration Matrix")
        report["trend"].to_excel(writer, sheet_name="SMA Trend", index=False)
        comp = pd.DataFrame([report["compliance"]])
        comp.to_excel(writer, sheet_name="Compliance Summary", index=False)
    return buf.getvalue()
