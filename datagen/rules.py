"""Default risk signal definitions (the 7th input dataset in the challenge).

These are a starting configuration the application loads, not code. Thresholds, weights
and bands are meant to be edited by the risk team; the app must not hard-code them.
"""

from datetime import date

import config as C

PARAMETERS = dict(
    as_of_date=C.AS_OF_DATE.isoformat(),
    currency=C.CURRENCY,
    score_range=[0, 100],
    score_direction="HIGHER_IS_RISKIER",
    grace_days=3,
    excuse_lunar_holiday_delays=True,
    trajectory_lookback_months=3,
    min_history_months=6,
    min_repayment_records=4,
    stale_data_months=2,
)

# RBI credit-risk EWS reporting. SMA buckets follow the IRAC norms (SMA-0 at 1-30 DPD,
# SMA-1 at 31-60, SMA-2 at 61-90, NPA beyond 90); the turnaround times mirror the RBI EWS
# framework. Configuration, not code, so a risk team can retune without a release.
RBI_EWS_CONFIG = dict(
    alert_examination_tat_days=30,
    escalation_deadline_days=7,
    resolution_deadline_days=180,
    high_exposure_threshold_inr=25_000_000,
    sma_buckets=[[0, 0, "Standard"], [1, 30, "SMA-0"], [31, 60, "SMA-1"],
                 [61, 90, "SMA-2"], [91, 999_999, "NPA"]],
)

SCORE_BANDS = [
    dict(band="Green", min_score=0, max_score=29, monitoring_status="NORMAL",
         recommended_action="No action; routine monitoring"),
    dict(band="Watch", min_score=30, max_score=59, monitoring_status="WATCHLIST",
         recommended_action="RM review; soft-touch customer contact"),
    dict(band="Amber", min_score=60, max_score=79, monitoring_status="ACTION_REQUIRED",
         recommended_action="Contact customer; assess restructuring or limit freeze"),
    dict(band="Red", min_score=80, max_score=100, monitoring_status="ESCALATE",
         recommended_action="Escalate to credit committee / collections; review provisioning"),
]

OVERRIDES = [
    dict(override_id="OV01", condition="current_dpd >= 90", minimum_band="Red",
         description="90+ DPD is non-performing regardless of other signals"),
    dict(override_id="OV02", condition="current_dpd >= 30", minimum_band="Amber",
         description="30+ DPD is at least Amber"),
    dict(override_id="OV03", condition="confidence == 'Low' and no hard evidence",
         maximum_band="Watch",
         description="Missing data alone never escalates beyond Watch (insufficient evidence)"),
]

# Value at which a signal reaches full strength (threshold = half strength).
SEVERE = dict(RS01=3, RS02=3, RS03=60, RS04=4, RS05=1, RS06=1, RS07=-45, RS08=5, RS09=3,
              RS10=-70, RS11=5, RS12=100, RS13=50, RS14=70, RS15=6, RS16=60, RS17=90,
              RS18=-120, DQ01=0, DQ02=3, DQ03=1)

# metric names describe what the engine must compute; lookback is in months.
SIGNALS = [
    # ---------------------------------------------------------------- repayment (S1, S4, S5)
    dict(signal_id="RS01", name="Repeated late or missed instalments", category="REPAYMENT",
         scenario="S1", metric="late_or_missed_instalments", lookback_months=6,
         operator=">=", threshold=2, weight=30, severity="HIGH",
         description="Count of instalments paid after due date + grace, partial or unpaid."),
    dict(signal_id="RS02", name="Worsening days past due", category="REPAYMENT",
         scenario="S5", metric="dpd_increase_month_ends", lookback_months=3,
         operator=">=", threshold=2, weight=15, severity="HIGH",
         description="Number of consecutive month-ends where max DPD rose."),
    dict(signal_id="RS03", name="Currently in arrears", category="REPAYMENT",
         scenario="S5", metric="current_dpd", lookback_months=0,
         operator=">=", threshold=1, weight=15, severity="MEDIUM",
         description="Scaled by DPD bucket: 1-30 = 50%, 31-60 = 75%, 61+ = 100% of weight."),
    dict(signal_id="RS04", name="Partial payments", category="REPAYMENT",
         scenario="S1", metric="partial_payments", lookback_months=6,
         operator=">=", threshold=2, weight=10, severity="MEDIUM",
         description="Instalments where less than 95% of the amount due was paid."),
    dict(signal_id="RS05", name="Isolated delay with recovery", category="MITIGANT",
         scenario="S4", metric="isolated_delay_recovered", lookback_months=12,
         operator="==", threshold=1, weight=-10, severity="INFO",
         description="Exactly one irregular instalment, preceded by 6+ on-time and fully "
                     "regularised; stronger mitigation if verified evidence exists."),
    dict(signal_id="RS06", name="Recent cure", category="REPAYMENT",
         scenario="S5", metric="months_since_cure", lookback_months=12,
         operator="<=", threshold=3, weight=8, severity="LOW",
         description="Arrears cleared within the last 3 months; watch for relapse."),
    # ---------------------------------------------------------------- cash flow (S2, S6)
    dict(signal_id="RS07", name="Sustained inflow decline", category="CASH_FLOW",
         scenario="S2", metric="inflow_change_3m_vs_prior_3m_pct", lookback_months=6,
         operator="<=", threshold=-20, weight=20, severity="HIGH",
         description="Average salary/business receipts of the last 3 months vs the 3 before."),
    dict(signal_id="RS08", name="Consecutive monthly inflow declines", category="CASH_FLOW",
         scenario="S2", metric="consecutive_inflow_declines", lookback_months=4,
         operator=">=", threshold=3, weight=8, severity="MEDIUM",
         description="Months in a row with lower receipts. For SMEs compare year-on-year to "
                     "avoid flagging normal seasonality."),
    dict(signal_id="RS09", name="Salary stopped", category="CASH_FLOW",
         scenario="S2", metric="months_without_salary", lookback_months=3,
         operator=">=", threshold=2, weight=15, severity="HIGH",
         description="Salary-transfer customers only; ignore if salary_transfer_flag = N."),
    dict(signal_id="RS10", name="Balance erosion", category="CASH_FLOW",
         scenario="S6", metric="avg_balance_change_pct", lookback_months=6,
         operator="<=", threshold=-40, weight=10, severity="MEDIUM",
         description="Latest 3-month average balance vs the 3 months before."),
    dict(signal_id="RS11", name="Returned payments / bounced cheques", category="CASH_FLOW",
         scenario="S6", metric="returned_payments", lookback_months=3,
         operator=">=", threshold=2, weight=10, severity="HIGH",
         description="Failed direct debits and returned cheques."),
    # ---------------------------------------------------------------- exposure (S3)
    dict(signal_id="RS12", name="High revolving utilisation", category="EXPOSURE",
         scenario="S3", metric="revolving_utilisation_pct", lookback_months=0,
         operator=">=", threshold=85, weight=12, severity="MEDIUM",
         description="On-us card / working-capital balance as % of limit."),
    dict(signal_id="RS13", name="Utilisation spike", category="EXPOSURE",
         scenario="S3", metric="utilisation_change_pp", lookback_months=6,
         operator=">=", threshold=25, weight=10, severity="MEDIUM",
         description="Percentage-point rise in revolving utilisation."),
    dict(signal_id="RS14", name="Debt burden above limit", category="EXPOSURE",
         scenario="S3", metric="debt_burden_ratio_pct", lookback_months=0,
         operator=">=", threshold=50, weight=15, severity="HIGH",
         description="(On-us + bureau monthly obligations) / monthly income. UAE cap is 50%."),
    dict(signal_id="RS15", name="New credit accumulation", category="EXPOSURE",
         scenario="S3", metric="new_facilities_or_enquiries", lookback_months=6,
         operator=">=", threshold=3, weight=12, severity="MEDIUM",
         description="New on-us facilities + new bureau facilities + bureau enquiries."),
    dict(signal_id="RS16", name="Exposure growing faster than income", category="EXPOSURE",
         scenario="S3", metric="exposure_growth_minus_income_growth_pp", lookback_months=6,
         operator=">=", threshold=25, weight=10, severity="MEDIUM",
         description="Total outstanding growth minus income growth, in percentage points."),
    # ---------------------------------------------------------------- bureau
    dict(signal_id="RS17", name="Delinquency at other lenders", category="BUREAU",
         scenario="S1", metric="bureau_external_max_dpd", lookback_months=0,
         operator=">=", threshold=30, weight=12, severity="HIGH",
         description="Off-us arrears often precede on-us arrears."),
    dict(signal_id="RS18", name="Bureau score drop", category="BUREAU",
         scenario="S1", metric="bureau_score_change", lookback_months=6,
         operator="<=", threshold=-50, weight=6, severity="MEDIUM",
         description="Points lost over the lookback."),
    # ---------------------------------------------------------------- data quality (S7)
    dict(signal_id="DQ01", name="Insufficient history", category="DATA_QUALITY",
         scenario="S7", metric="months_of_history", lookback_months=0,
         operator="<", threshold=6, weight=0, severity="INFO",
         description="Confidence flag only. Never adds risk points."),
    dict(signal_id="DQ02", name="Missing financial months", category="DATA_QUALITY",
         scenario="S7", metric="missing_financial_months", lookback_months=12,
         operator=">=", threshold=1, weight=0, severity="INFO",
         description="Gaps in the account feed. Confidence flag only."),
    dict(signal_id="DQ03", name="Bureau no-hit", category="DATA_QUALITY",
         scenario="S7", metric="bureau_no_hit", lookback_months=0,
         operator="==", threshold=1, weight=0, severity="INFO",
         description="No bureau record. Confidence flag only."),
]


def definitions() -> dict:
    return dict(
        version="1.0",
        generated_on=date.today().isoformat(),
        notes="Weights are points on a 0-100 scale (higher = riskier). A signal earns half its "
              "weight at `threshold` and full weight at `severe`; the total is capped at 100 "
              "and overrides apply after scoring.",
        parameters=PARAMETERS,
        signals=[{**sig, "severe": SEVERE[sig["signal_id"]], "enabled": True}
                 for sig in SIGNALS],
        score_bands=SCORE_BANDS,
        overrides=OVERRIDES,
        rbi_ews_config=RBI_EWS_CONFIG,
    )
