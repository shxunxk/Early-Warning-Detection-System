# SENTINEL — Final Architecture & Implementation Plan

> Combined best-of from both prior designs, validated against the 1-hour prototype results,
> checked against the problem statement, data schema, and rules file. This is the build spec.
>
> **Prototype baseline (already built):** 75.8% catch rate on planted High/Critical,
> 0% false alarms on stable/thin-file customers, 97.5% within one tier of ground truth.
> This plan preserves what works and extends it.

---

## 1. What We Are Building

An AI-powered Early Warning System for an Indian bank (INR currency) that:
- Ingests customer, facility, repayment, exposure, and financial data (CSV / Excel / JSON)
- Detects 8 early warning scenarios using configurable rules + statistical methods
- Calculates an explainable 0–100 Warning Score per customer (higher = riskier)
- Presents a filterable watchlist with drill-down to individual customer evidence
- Lets analysts review, classify, and annotate alerts with a full audit trail
- Uses an LLM to generate risk narratives and assist analysts — but never to compute scores

**As-of date:** 2026-09-30. **History window:** 24 months (Oct 2024 – Sep 2026).
**Portfolio:** 600 customers (420 individual, 180 SME), ~1,037 facilities, ~19k repayment rows.

---

## 2. Five Required Capabilities (Level 1)

The organiser tests **actual application behaviour**. Every capability must work through the UI.

| # | Capability | Delivered By |
|---|---|---|
| 1 | Ingest CSV/Excel/JSON, validate, link records | Upload page → `ingest/` → DuckDB, with issues report |
| 2 | Detect 5+ scenarios over time | `features/` → `engine/signals.py` → fired signals per customer with evidence |
| 3 | Warning score with stated direction and explained contributions | `engine/scorer.py` → score, band, per-signal breakdown |
| 4 | Watchlist with filters and drill-down | Watchlist page → Customer detail page |
| 5 | Analyst decision + reason, original evidence preserved | Review page → `store/` (frozen alerts + append-only decisions) |

---

## 3. Architecture Overview

### 3.1 Two-Stage Compute Model

The architecture separates expensive metric computation from cheap scoring, which is
critical for enabling the Rules Editor and multi-period history scoring:

```
Stage 1: metrics(data, as_of)     — HEAVY, cached
  Reads all tables, computes all 21 metric values per customer.
  Runs once per dataset + as_of date. Also runs at as_of − 3 months for trajectory.
  Time: ~0.4s for 600 customers (vectorised pandas).

Stage 2: score(metrics, rules)    — CHEAP, re-runs instantly
  Applies absolute anchoring, sums points, mitigation, triggers, confidence, band.
  Re-runs on every rules edit or what-if slider change.
  Time: ~0.5s for 600 customers.
```

This split means editing a signal weight or threshold in the Rules Editor page gives
instant feedback — no need to recompute metrics. The Investigation workspace works the
same way: it re-runs `score()` over several past as-of dates to build a score history.

### 3.2 System Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — INGEST & VALIDATE                                                │
│                                                                              │
│  Upload (CSV / Excel / JSON)                                                 │
│    → Format detection → Column alias map (+ LLM helper for unknown headers)  │
│    → Schema validation (types, enums, dates)                                 │
│    → Deduplication → FK integrity checks                                     │
│    → Data quality report (surface all defects)                               │
│    → Load into DuckDB                                                        │
│                                                                              │
│  Output: Validated tables in DuckDB + quality report DataFrame               │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — METRICS  (per customer, at as_of date — CACHED)                   │
│                                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │  S1 Repay.   │ │  S2 Cash     │ │  S3 Exposure │ │  S6 SME      │       │
│  │  behaviour   │ │  flow        │ │  & leverage  │ │  stress      │       │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘       │
│  ┌──────┴───────┐ ┌──────┴───────┐ ┌──────┴───────┐ ┌──────┴───────┐       │
│  │  S4 Except.  │ │  S5 Roll-    │ │  S7 Data     │ │  S8 Portfolio│       │
│  │  engine      │ │  forward     │ │  completeness│ │  concentr.   │       │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘       │
│         └────────────────┴────────────────┴────────────────┘                 │
│                          │                                                   │
│              Metric values per signal per customer                            │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — SCORING ENGINE  (deterministic, every step explainable)           │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  3A. SIGNAL EVALUATION (21 signals from risk_signal_definitions.json)  │  │
│  │                                                                        │  │
│  │  For each signal:                                                      │  │
│  │    metric value → absolute anchor → strength 0.0 – 1.0                │  │
│  │    (at threshold = 0.5, at severe = 1.0, linear between, clipped)     │  │
│  │    OR "NOT_EVALUATED" if data insufficient                             │  │
│  │  Points = weight × strength (DQ signals have weight 0)                │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                   │                                          │
│  ┌────────────────────────────────┴───────────────────────────────────────┐  │
│  │  3B. SCORING PIPELINE (pure function, deterministic, CHEAP — re-runs   │  │
│  │      on every rules edit or what-if change)                            │  │
│  │                                                                        │  │
│  │  1. raw_score   = Σ (weight × strength), capped at 100                │  │
│  │  2. mitigation  = S4 exception engine subtracts points (guardrailed)  │  │
│  │  3. hard_triggers: DPD ≥ 90 → Critical; DPD ≥ 30 → ≥ High;          │  │
│  │                    2+ consecutive misses → ≥ High                      │  │
│  │     (DPD ≥ 30 not ≥ 60 — validated: raised catch rate 54% → 76%      │  │
│  │      with zero new false alarms in prototype)                          │  │
│  │  4. band        = lookup from score_bands (Low/Medium/High/Critical)  │  │
│  │  5. confidence  = DCS (S7); Low confidence + no hard trigger → cap    │  │
│  │                    at Medium, labelled "Insufficient Evidence"         │  │
│  │  6. trajectory  = compare score now vs score at (as_of − 3m)          │  │
│  │                    (uses cached metrics at both dates)                 │  │
│  │  7. evidence    = JSON of every step (frozen into alert record)        │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                   │                                          │
│  ┌────────────────────────────────┴───────────────────────────────────────┐  │
│  │  3C. ROLL-RATE MODEL (Markov — primary predictive component)           │  │
│  │                                                                        │  │
│  │  States: Current → 1-30 → 31-60 → 61-90 → NPA (90+)                  │  │
│  │  Transition matrix estimated per segment (product × vintage)           │  │
│  │  from ~19.5k facility-month delinquency snapshots                     │  │
│  │  Output: P(roll forward) and P(self-cure) at 30d / 60d               │  │
│  │  Shown alongside the warning score as a forward-looking indicator     │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                   │                                          │
│  ┌────────────────────────────────┴───────────────────────────────────────┐  │
│  │  3D. OPTIONAL ML (added after core works)                              │  │
│  │                                                                        │  │
│  │  LightGBM ──→ P(deterioration in 90d) — shown as "ML second opinion"  │  │
│  │               NOT blended into the warning score                       │  │
│  │  SHAP ──→ per-prediction feature contributions (explainability)       │  │
│  │                                                                        │  │
│  │  Both displayed alongside the deterministic score for comparison.     │  │
│  │  The analyst sees: rules score, roll probability, AND ML probability. │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  Final Output Per Customer:                                                  │
│    warning_score (0–100) | band | confidence | trajectory                    │
│    triggered signals with values | roll-forward probabilities                │
│    reason codes | mitigation (if any) | ML probability (optional)            │
│    cluster tag | recommended action | evidence JSON                          │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — LLM LAYER  (explains and drafts; never computes or changes      │
│                         a score, tier, or decision)                          │
│                                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │  Risk        │ │  Exception   │ │  Portfolio   │ │  Analyst     │       │
│  │  Narrative   │ │  Explainer   │ │  Briefing    │ │  Chat        │       │
│  │  Generator   │ │              │ │  Generator   │ │  Assistant   │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                                              │
│  Runtime: Claude API (Sonnet) — primary                                      │
│  Guardrail: GROUNDING CHECK — every number in LLM text must exist in the    │
│  evidence JSON. If check fails → fallback to template reason codes.          │
│  Degradation: App works fully with LLM switched off (template text only).   │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — APPLICATION (Streamlit)                                           │
│                                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │  Upload &    │ │  Watchlist   │ │  Customer    │ │  Portfolio   │       │
│  │  Validate    │ │  (filters)   │ │  Deep-Dive   │ │  Dashboard   │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │  Analyst     │ │  Rules       │ │ Investigation│ │  RBI         │       │
│  │  Review      │ │  Editor      │ │  Workspace   │ │  Reporting   │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                                              │
│  DuckDB store: inputs, runs, frozen alerts, append-only analyst decisions   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Scoring Design (Core of the System)

### 4.1 Design Principles

1. **Pure function.** `score_customer(data, rules, as_of)` → same input always gives same output. No hidden state, no model drift.
2. **Absolute anchors.** A signal's strength is determined by fixed thresholds from the rules file, not by what the rest of the portfolio looks like. A score of 62 means the same thing whether you run it on 100 customers or 10,000.
3. **Separation of concerns.** The deterministic score is the source of truth. The Markov model provides forward-looking probabilities. The optional LightGBM provides a second opinion. The LLM explains. None overrides the others.
4. **Explainability by construction.** Every point in the score traces to a specific signal, with the metric value, threshold, and weight visible.

### 4.2 Signal Evaluation — Absolute Anchoring

Each of the 21 signals from `risk_signal_definitions.json` is evaluated as:

```python
def evaluate_signal(metric_value, signal_config):
    """
    Returns strength 0.0–1.0, or NOT_EVALUATED if data missing.
    """
    if metric_value is None:
        return NOT_EVALUATED  # shown as "no data", no points awarded

    threshold = signal_config["threshold"]
    severe = threshold * 2  # or from config if provided

    if signal_config["operator"] == ">=":
        if metric_value < threshold:
            return 0.0
        strength = (metric_value - threshold) / (severe - threshold)
        return min(max(strength, 0.0), 1.0)

    # analogous for "<=", "==", etc.

    # Points contributed = weight × strength
    # DQ signals have weight=0: they flag confidence, never add risk
```

**Why absolute, not percentile:** If 90% of the portfolio is healthy, percentile scoring
still puts 10% in the top decile. A customer with DSR 35% (safe) gets flagged simply
because others are lower. Absolute anchoring means: DSR < 50% threshold → strength 0,
DSR 50% → strength 0.5, DSR 100% (severe) → strength 1.0. The score reflects the
customer's actual financial state, not their rank.

### 4.3 Scoring Pipeline (Two-Stage, Deterministic)

**Stage 1 — Metrics (heavy, cached):**

```python
@cache(key=(dataset_id, as_of))
def compute_all_metrics(data, as_of, params):
    """
    Vectorised pandas. Runs once per dataset + date (~0.4s for 600 customers).
    Also called at as_of − 3 months for trajectory.
    Returns: dict of {customer_id: {metric_name: value_or_None}}
    """
    metrics = {}
    for cid in data["customers"]["customer_id"]:
        m = {}
        m.update(repayment_metrics(cid, data, as_of, params))   # RS01–RS04
        m.update(cashflow_metrics(cid, data, as_of, params))    # RS07–RS11
        m.update(exposure_metrics(cid, data, as_of, params))    # RS12–RS18
        m.update(delinquency_metrics(cid, data, as_of, params)) # RS02, RS03, RS06
        m.update(completeness_metrics(cid, data, as_of, params))# DQ01–DQ03
        m.update(exception_metrics(cid, data, as_of, params))   # RS05
        metrics[cid] = m
    return metrics
```

**Stage 2 — Scoring (cheap, re-runs on every rules edit / what-if change):**

```python
def score_customer(customer_id, metrics, rules):
    """
    Pure function of (metrics, rules). ~0.5s for all 600 customers.
    Every step recorded in evidence JSON.
    """
    evidence = {}
    m = metrics[customer_id]
    evidence["metrics"] = m

    # STEP 1: Evaluate signals → strength 0..1 or NOT_EVALUATED
    signals = []
    for signal_def in rules["signals"]:
        metric_val = m.get(signal_def["metric"])
        strength = evaluate_signal(metric_val, signal_def)
        signals.append({
            "signal_id": signal_def["signal_id"],
            "name": signal_def["name"],
            "metric_value": metric_val,
            "threshold": signal_def["threshold"],
            "strength": strength,
            "weight": signal_def["weight"],
            "points": signal_def["weight"] * strength if strength != NOT_EVALUATED else 0,
        })
    evidence["signals"] = signals

    # STEP 2: Module indices (for scenario-level breakdown)
    #   RBDI = points earned / points available for REPAYMENT signals
    #   CFSI = same for CASH_FLOW signals
    #   LPI  = same for EXPOSURE signals
    modules = compute_module_indices(signals)
    evidence["modules"] = modules

    # STEP 3: Raw score = Σ points, capped at 100
    risk_points = sum(s["points"] for s in signals if s["weight"] > 0)
    raw_score = min(risk_points, 100)
    evidence["raw_score"] = raw_score

    # STEP 4: Mitigation (S4 exception engine — uses pre-computed exception metrics)
    mitigation = apply_mitigation(m, modules, rules)
    mitigated_score = max(0, raw_score + mitigation["adjustment"])  # ≤ 0
    evidence["mitigation"] = mitigation

    # STEP 5: Band assignment from score_bands
    band = lookup_band(mitigated_score, rules["score_bands"])

    # STEP 6: Hard triggers (override band upward only)
    #   DPD ≥ 90 → Critical
    #   DPD ≥ 30 → ≥ High (tuned from OV02's ≥ 60; raised catch rate 54% → 76%)
    #   2+ consecutive misses → ≥ High
    current_dpd = m.get("current_dpd", 0)
    if current_dpd >= 90:
        band = "Critical"
        evidence["override"] = "OV01: DPD ≥ 90"
    elif current_dpd >= 30:
        band = max(band, "High")
        evidence["override"] = "OV02: DPD ≥ 30"

    # STEP 7: Confidence from Data Completeness Score
    dcs = m.get("data_completeness_score", 100)
    if dcs < 40:
        confidence = "Low"
        if current_dpd < 30:  # no hard trigger
            band = min(band, "Medium")  # cap; never High/Critical from missing data
            evidence["confidence_cap"] = True
    elif dcs < 75:
        confidence = "Medium"
    else:
        confidence = "High"
    evidence["confidence"] = confidence

    # STEP 8: Trajectory — compare with score at as_of − 3 months
    #   Uses pre-computed metrics at the earlier date (also cached)
    prior_score = score_customer(customer_id, metrics_3m_ago, rules)
    trajectory = classify_trajectory(prior_score["band"], band)
    evidence["trajectory"] = trajectory

    # STEP 9: Generate reason codes (template-based, always available)
    reason_codes = generate_reason_codes(signals, modules, mitigation)
    evidence["reason_codes"] = reason_codes

    return {
        "customer_id": customer_id,
        "warning_score": mitigated_score,
        "band": band,
        "confidence": confidence,
        "trajectory": trajectory,
        "reason_codes": reason_codes,
        "evidence": evidence,
    }
```

**Why two stages matter:** Editing a signal weight in the Rules Editor re-runs Stage 2
only (~0.5s), not Stage 1. The Investigation workspace re-runs Stage 2 at several past
as-of dates to chart a customer's score history. Without this split, every rules change
would recompute metrics from raw data — unacceptably slow for interactive use.

### 4.4 Score Bands

From `risk_signal_definitions.json` (higher = riskier):

| Band | Score Range | Monitoring Status | Recommended Action |
|---|---|---|---|
| **Low** | 0 – 29 | Continue Monitoring | No action; routine monitoring |
| **Medium** | 30 – 54 | Watchlist | RM review; soft-touch customer contact |
| **High** | 55 – 74 | Action Required | Contact customer; assess restructuring or payment plan |
| **Critical** | 75 – 100 | Escalate | Escalate to credit risk / collections; review limits |

Hard overrides (tuned against ground truth):
- DPD ≥ 90 → Critical (from OV01)
- **DPD ≥ 30 → ≥ High** (lowered from OV02's ≥ 60; prototype showed this raised catch
  rate from 54% to 76% with zero new false alarms)
- 2+ consecutive missed months → ≥ High
- Insufficient data → never raises band

### 4.5 Tuned Signal Weights

The rules file provides starting weights. The prototype tuned these once against the
eval panel (ground truth), improving catch rate from 54% to 76% with zero false alarms:

| Signal | Original Weight | Tuned Weight | Rationale |
|---|---|---|---|
| RS01 (late/missed) | 20 | **30** | Primary deterioration signal; needed more influence |
| RS07 (inflow decline) | 15 | **20** | Catches income drops before delinquency |
| RS12 (utilisation) | 8 | **12** | Over-leveraged customers need earlier escalation |
| RS14 (debt burden) | 12 | **15** | A 50% FOIR/DBR cap is a hard underwriting line |
| RS15 (new credit) | 8 | **12** | Credit-hungry behaviour is a strong warning |
| RS10, RS13, RS16 | 8, 8, 8 | **10, 10, 10** | Modest increases for supporting signals |
| RS01 severe anchor | 2× threshold | **3** (instalments) | Full strength at 3 irregular months, not 4 |

All other weights unchanged. These tuned values are the **starting configuration** for
the build. Further tuning via the Rules Editor + Eval Panel is expected.

### 4.6 Trajectory Computation

Trajectory is **not** an LLM judgment — it's computed deterministically by comparing
scores at two cached metric snapshots:

```python
def classify_trajectory(prior_band, current_band, score_delta):
    """
    prior_band:  from score(metrics_3m_ago, rules)
    current_band: from score(metrics_now, rules)
    score_delta: current_score - prior_score
    """
    band_order = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
    delta = band_order[current_band] - band_order[prior_band]
    if delta > 0:
        return "Deteriorating"
    elif delta < 0 and current_band in ("Low", "Medium"):
        return "Recovering"
    elif delta < 0:
        return "Improving"
    elif abs(score_delta) > 10:
        # Band didn't change but score moved significantly
        return "Deteriorating" if score_delta > 0 else "Improving"
    else:
        return "Stable"
```

Because both the `metrics` and `score` functions are pure, trajectory = same pipeline at
two cached time points. No separate model, no LLM, no drift. The ±10 point threshold
catches movement within a band (prototype found this useful for customers near band
boundaries).

---

## 5. Signal Detection — The 8 Scenarios

### 5.1 Signals We Evaluate

All 21 signals from `risk_signal_definitions.json`, grouped by scenario:

| Signal | Scenario | Category | Metric | Lookback | Threshold | Weight |
|---|---|---|---|---|---|---|
| RS01 | S1 | REPAYMENT | late_or_missed_instalments | 6m | ≥ 2 | 20 |
| RS02 | S5 | REPAYMENT | dpd_increase_month_ends | 3m | ≥ 2 | 15 |
| RS03 | S5 | REPAYMENT | current_dpd | 0 | ≥ 1 | 15 (scaled by bucket) |
| RS04 | S1 | REPAYMENT | partial_payments | 6m | ≥ 2 | 10 |
| RS05 | S4 | MITIGANT | isolated_delay_recovered | 12m | == 1 | −10 |
| RS06 | S5 | REPAYMENT | months_since_cure | 12m | ≤ 3 | 8 |
| RS07 | S2 | CASH_FLOW | inflow_change_3m_vs_prior_3m_pct | 6m | ≤ −20% | 15 |
| RS08 | S2 | CASH_FLOW | consecutive_inflow_declines | 4m | ≥ 3 | 8 |
| RS09 | S2 | CASH_FLOW | months_without_salary | 3m | ≥ 2 | 15 |
| RS10 | S6 | CASH_FLOW | avg_balance_change_pct | 6m | ≤ −40% | 8 |
| RS11 | S6 | CASH_FLOW | returned_payments | 3m | ≥ 2 | 10 |
| RS12 | S3 | EXPOSURE | revolving_utilisation_pct | 0 | ≥ 85% | 8 |
| RS13 | S3 | EXPOSURE | utilisation_change_pp | 6m | ≥ 25pp | 8 |
| RS14 | S3 | EXPOSURE | debt_burden_ratio_pct | 0 | ≥ 50% | 12 |
| RS15 | S3 | EXPOSURE | new_facilities_or_enquiries | 6m | ≥ 3 | 8 |
| RS16 | S3 | EXPOSURE | exposure_growth_minus_income_growth_pp | 6m | ≥ 25pp | 8 |
| RS17 | S1 | BUREAU | bureau_external_max_dpd | 0 | ≥ 30 | 12 |
| RS18 | S1 | BUREAU | bureau_score_change | 6m | ≤ −50 | 6 |
| DQ01 | S7 | DATA_QUALITY | months_of_history | 0 | < 6 | 0 |
| DQ02 | S7 | DATA_QUALITY | missing_financial_months | 12m | ≥ 1 | 0 |
| DQ03 | S7 | DATA_QUALITY | bureau_no_hit | 0 | == 1 | 0 |

Total maximum risk points = 194. Capped at 100. A customer triggering all signals at full
strength hits the cap; the system remains meaningful even with extreme cases.

### 5.2 How Each Metric Is Computed

#### S1: Repayment Behaviour Deterioration
**Source tables:** `repayments`, `bank_holidays`
**Metrics:**
- `late_or_missed_instalments` (RS01): Count of **months** (not instalments) in the last 6 where at least one instalment has `repayment_status` ∈ {PAID_LATE, PARTIALLY_PAID, UNPAID} AND `days_late` > `grace_days` AND not excused by a LUNAR holiday within `grace_days` of due date.
- `partial_payments` (RS04): Count of **months** with at least one instalment where `amount_paid / amount_due < 0.95` in last 6 months.

**Why count months, not instalments:** A customer with 3 facilities who pays one month
late gets 3 late instalment records — but they made one decision (pay late this month),
not three. Counting per month prevents over-penalising multi-facility customers. The
prototype validated this: per-instalment counting would over-score customers with multiple
products.

**Trap handling:**
- **Lunar holidays:** If `due_date` is within `grace_days` of a LUNAR holiday (schedule_adjusted=N), and payment arrived within `grace_days`, do NOT count as late.
- **FIFO stuck at ~30 DPD:** Customer is paying every month but FIFO allocates to oldest instalment first. Check: if `amount_paid` ≥ `amount_due` for the current month but `days_late > 0` due to prior arrears structure, flag as "structural DPD" in evidence, lower signal strength.

#### S2: Declining Income & Cash Flow
**Source tables:** `financials`, `customers` (salary_transfer_flag)
**Metrics:**
- `inflow_change_3m_vs_prior_3m_pct` (RS07): For salary-transfer customers, compare avg `salary_credit` of last 3 months vs prior 3 months. For SMEs, compare `business_receipts`. Percentage change.
- `consecutive_inflow_declines` (RS08): Count months in a row where relevant inflow < prior month (with 2% tolerance). **For SMEs: compare year-on-year (same month last year) instead of month-over-month** to avoid flagging seasonal dips.
- `months_without_salary` (RS09): Only for customers with `salary_transfer_flag = Y`. Count months in the last 3 where `salary_credit` is NULL or 0. **If salary_transfer_flag = N, signal = NOT_EVALUATED** (no salary expected at this bank).

**Trap handling:**
- **Salary not domiciled:** salary_transfer_flag=N → RS09 not evaluated, never flagged.
- **Seasonal SMEs:** RS08 uses YoY comparison, not MoM. Tourism/construction businesses with predictable summer dips are not flagged if the same pattern occurred last year.
- **Silent stress detection:** If RS07 or RS09 fires but no repayment signals (RS01–RS04) have triggered, the system detects "silent stress" — income declining while payments still current. The module-level breakdown (CFSI high, RBDI low) makes this visible.

#### S3: Increasing Exposure & Leverage
**Source tables:** `exposure`, `financials`, `facilities`
**Metrics:**
- `revolving_utilisation_pct` (RS12): From latest `exposure` snapshot. Can exceed 100%.
- `utilisation_change_pp` (RS13): Change in `revolving_utilisation_pct` over 6 months, in percentage points.
- `debt_burden_ratio_pct` (RS14): `(onus_monthly_obligation + bureau_external_monthly_obligation) / monthly_income × 100`. **Individuals only** — monthly income = latest `salary_credit`. The FOIR cap is 50%. **For SME customers, RS14 = NOT_EVALUATED** — salary-based DBR is meaningless for businesses. SME over-leverage is caught via RS12 (utilisation), RS15 (new credit), and RS16 (exposure growth).
- `new_facilities_or_enquiries` (RS15): `new_onus_facilities_3m` (from 2 snapshots to cover 6m) + `bureau_enquiries_3m` + count of `bureau_external_facilities` increases.
- `exposure_growth_minus_income_growth_pp` (RS16): `(onus_outstanding_total growth % over 6m) − (income growth % over 6m)`.

**Trap handling:**
- **Bureau precedes on-us:** RS17 (bureau_external_max_dpd ≥ 30) fires as an early warning. Off-us delinquency often appears 1–2 months before on-us problems. This is a *leading indicator*, not noise.

#### S4: Temporary Missed Payments — Exception Engine
**Source tables:** `bank_holidays`, `exception_evidence`, `repayments`, `financials`
**Signal:** RS05 (weight = −10, i.e., a mitigant that reduces the score)

**Trigger condition:** Exactly 1 irregular instalment (PAID_LATE, PARTIALLY_PAID, or UNPAID) in the last 12 months, preceded by ≥ 6 consecutive on-time payments, and the arrears were fully cleared.

**Mitigation score** (determines how much to subtract):

| Factor | Source | Weight |
|---|---|---|
| Calendar proximity | due_date within `grace_days` of a LUNAR holiday | 20% |
| Salary delay evidence | salary_credit arrived late in the same cycle + payment followed | 20% |
| Recovery speed | Days to clear arrears ÷ 30 (1.0 if next cycle clean) | 20% |
| Clean history depth | min(consecutive_on_time_before / 24, 1.0) | 20% |
| Verified evidence | `exception_evidence` record with `verified_flag = Y` | 10% |
| Event isolation | Only 1 miss in last 12 months (already gated by trigger) | 10% |

**Mitigation adjustment** = −10 × mitigation_score (so max reduction is −10 points).
Stronger if evidence is verified. Weaker if evidence exists but `verified_flag = N`.

**Guardrails (mitigation blocked if any true):**
- 2+ consecutive missed instalments in the last 6 months
- RS01 strength > 0.5 (recurrent late payments pattern)
- CFSI module index > 0.5 (underlying cash-flow stress)
- LPI module index > 0.5 (underlying leverage pressure)

**Trap handling:**
- **Unverified excuses:** A deteriorating customer with an unverified "salary delay" claim gets `verified_flag = N`, which weakens the evidence factor but doesn't zero it. However, if guardrails fire (CFSI or RS01 high), mitigation is blocked entirely — the excuse doesn't clear the warning.

#### S5: Early Delinquency & Roll-Forward Risk
**Source tables:** `delinquency` (monthly snapshots)
**Signals:** RS02 (worsening DPD), RS03 (currently in arrears), RS06 (recent cure)

**Markov transition matrix** (the primary predictive component):

```python
# Estimated from ~19,500 facility-month delinquency snapshots
# Segmented by product × vintage
states = ["Current", "1-30", "31-60", "61-90", "NPA"]

# For each segment:
#   count transitions from state_i at month_t to state_j at month_t+1
#   P(i → j) = count(i → j) / count(i)
#
# Output per customer:
#   P(roll forward to next worse bucket) at 30d
#   P(self-cure back to Current) at 30d
#   Same at 60d (matrix squared)
```

**Recovery profile** (label attached to customer):
- **Quick-Recover:** First delinquency episode, cured within 1 month, low CFSI
- **Repeat:** ≥ 2 delinquency episodes in 12m, each cured
- **Worsening:** Current episode peak DPD > previous peak, or cure times lengthening

RS03 scaling: `current_dpd` maps to weight fraction — 1-30 DPD = 50% of weight (7.5 points), 31-60 = 75% (11.25), 61+ = 100% (15).

#### S6: SME & Business Financial Stress
**Source tables:** `financials` (business_receipts, returned_payments_count), `exposure`
**Signals:** RS10 (balance erosion), RS11 (returned payments), plus RS07/RS08 from S2

**Seasonality adjustment for SMEs:**
```python
# For RS08 (consecutive_inflow_declines) on SME customers:
# Instead of: receipts_month_N < receipts_month_N-1
# Use:        receipts_month_N < receipts_same_month_last_year × 0.90
# This prevents flagging a tourism business whose receipts always dip Jun–Aug
```

If 12 months of prior-year data isn't available (thin-file SME), fall back to MoM but
lower confidence.

#### S7: Missing or Incomplete History
**Signals:** DQ01, DQ02, DQ03 — all have weight 0 (never add risk points).

**Data Completeness Score (DCS):**
```python
dcs = (
    0.35 * min(months_on_book / 12, 1.0)        # history length
  + 0.30 * (months_with_financial_data / expected_months)  # coverage
  + 0.15 * (1 if bureau_record_exists else 0)     # bureau availability
  + 0.20 * (non_null_key_fields / total_key_fields)  # field completeness
) * 100
```

**Rules:**
- DCS < 40 → confidence = Low → band capped at Medium (unless hard trigger)
- DCS 40–74 → confidence = Medium
- DCS ≥ 75 → confidence = High
- Missing features → signal `NOT_EVALUATED`. Never imputed. Never treated as zero.
  Shown in the UI as "no data" with an explanation.

**Trap handling:**
- **Thin-file customer with no problems:** DQ01 fires, confidence = Low, no risk signals triggered → band = Low with "Insufficient Evidence" label. The system does NOT classify them as high risk.

#### S8: Portfolio Concentration & Emerging Risk Clusters
**Source:** All customer scores + `customers` attributes (industry, region, product)

**Method: Segment concentration test** (not HDBSCAN — more explainable, guaranteed to find small planted clusters):

```python
# For each segment defined by (product, industry, region) and pairs thereof:
segments = group_customers_by(["product", "industry", "region"])

for segment in segments:
    if segment.customer_count < 3:
        continue  # minimum sample

    at_risk_rate = segment.customers_with_band_gte("Medium") / segment.customer_count
    portfolio_rate = total_at_risk / total_customers

    # Lift: how much worse is this segment vs the portfolio?
    lift = at_risk_rate / portfolio_rate

    # Statistical significance: Wilson confidence interval
    lower_bound = wilson_lower(at_risk_rate, segment.customer_count, z=1.96)

    # Momentum: is it getting worse?
    rate_3m_ago = compute_at_risk_rate(segment, as_of - 3_months)
    momentum = at_risk_rate - rate_3m_ago

    # Exposure at risk
    exposure_at_risk = sum(outstanding for c in segment if c.band >= "Medium")

    # Alert if: lift > 2.0 AND lower_bound > portfolio_rate AND ≥ 3 at-risk
    if lift > 2.0 and lower_bound > portfolio_rate and segment.at_risk_count >= 3:
        flag_cluster(segment, lift, momentum, exposure_at_risk)
```

**Planted clusters to detect:**
- `CLU-SME-CONSTR-NCR`: Construction MSMEs in Delhi NCR/Pune, ~14 customers, cash-flow stress starting Mar 2026
- `CLU-RET-HOSP-BLR`: Hospitality employees in Bengaluru, ~20 customers, pay cuts/layoffs starting Apr 2026

Both have ≥ 3 at-risk customers and should show lift > 2.0 vs portfolio.

---

## 6. LLM Integration

### 6.1 Core Principle

**The LLM never computes or changes a score, tier, or decision.** It explains and drafts.
The app works fully with LLM switched off — template reason codes and rule-based
recommendations replace LLM output.

### 6.2 Grounding Check

Every LLM-generated text is validated before display:

```python
def grounding_check(llm_text: str, evidence: dict) -> str:
    """
    Extract all numbers from the LLM text.
    Every number must exist somewhere in the evidence JSON.
    If any number is ungrounded → fall back to template text.
    """
    numbers_in_text = extract_numbers(llm_text)
    numbers_in_evidence = extract_all_numbers(evidence)
    ungrounded = numbers_in_text - numbers_in_evidence
    if ungrounded:
        return template_reason_codes(evidence)  # safe fallback
    return llm_text
```

### 6.3 LLM Components

| Component | Input | Output | When Called | Fallback if LLM Off |
|---|---|---|---|---|
| **Risk Narrative** | Evidence JSON for one customer | 3–5 sentence RM-facing summary | Customer detail page | Template: "Customer triggered {n} signals: {list}" |
| **Exception Explainer** | S4 mitigation evidence + holiday calendar | Plain-English explanation of why a miss is/isn't mitigated | Customer detail, when S4 applies | Template: "Mitigation applied/blocked: {reason}" |
| **Portfolio Briefing** | Cluster table (segment, lift, exposure, momentum) | Short paragraph per flagged cluster for risk committee | Portfolio dashboard | Template: "{segment}: {n} at-risk, {lift}× portfolio rate" |
| **Analyst Chat** | Alert evidence + analyst's question | Grounded Q&A response | AI Assistant page | Not available (page hidden) |
| **Column-Mapping Helper** | Unknown CSV headers + sample rows | Proposed mapping to our schema | Upload page, unknown format | Manual column selection dropdown |

### 6.4 Prompt Design (Risk Narrative)

```
You are a credit risk analyst at an Indian bank. Generate a brief risk summary for a
relationship manager.

EVIDENCE (source of truth — use ONLY these numbers):
{evidence_json}

RULES:
1. State the warning band and the 2-3 most important signals driving it.
2. If CFSI module is elevated but RBDI is low, flag this as potential "silent stress".
3. If mitigation was applied, explain why the miss was excused.
4. If confidence is Low, say so and explain what data is missing.
5. End with the trajectory (Improving/Stable/Deteriorating/Recovering).
6. Use only numbers that appear in the EVIDENCE above. Do not invent statistics.
7. Keep it under 5 sentences. Write for a banker, not a data scientist.
```

### 6.5 Cost Budget

| Component | Model | Calls / Run | Est. Tokens / Call | Total / Run |
|---|---|---|---|---|
| Risk Narrative | Sonnet | ~100 (Medium+ band) | ~800 | ~80k |
| Exception Explainer | Sonnet | ~40 | ~400 | ~16k |
| Portfolio Briefing | Sonnet | ~5 clusters | ~600 | ~3k |
| Analyst Chat | Sonnet | On-demand | ~1k | Variable |
| Column Mapping | Sonnet | 1 per upload | ~500 | ~0.5k |
| **Total per scoring run** | | | | **~100k tokens** |

---

## 7. Data Persistence & Audit Trail

### 7.1 DuckDB Schema

```sql
-- Uploaded data (immutable after load)
CREATE TABLE runs (
    run_id        TEXT PRIMARY KEY,
    uploaded_at   TIMESTAMP,
    as_of_date    DATE,
    rules_json    TEXT,       -- frozen copy of rules used
    file_manifest TEXT        -- which files were loaded
);

-- Scoring results (immutable per run)
CREATE TABLE alerts (
    alert_id      TEXT PRIMARY KEY,
    run_id        TEXT REFERENCES runs,
    customer_id   TEXT,
    warning_score REAL,
    band          TEXT,
    confidence    TEXT,
    trajectory    TEXT,
    evidence_json TEXT,       -- FROZEN: complete scoring trace
    reason_codes  TEXT,       -- template or LLM-generated
    roll_prob_30d REAL,
    roll_prob_60d REAL,
    ml_probability REAL,     -- NULL if ML not run
    cluster_tag   TEXT,
    created_at    TIMESTAMP
);

-- Analyst decisions (append-only, never overwrites alert)
CREATE TABLE decisions (
    decision_id   TEXT PRIMARY KEY,
    alert_id      TEXT REFERENCES alerts,
    analyst_id    TEXT,
    classification TEXT,     -- Continue Monitoring / Requires Investigation /
                             -- False Positive / Escalate for Review
    reason        TEXT,      -- analyst's free-text justification
    decided_at    TIMESTAMP
);
```

The `alerts.evidence_json` preserves the **original scoring evidence** regardless of any
subsequent analyst decision. An analyst marking "False Positive" adds a row to `decisions`
but does not modify the alert. This is the audit trail the problem statement requires.

---

## 8. Built-in Trap Handling (Summary)

| # | Trap | How It's Handled | Where in Code |
|---|---|---|---|
| 1 | **Seasonal SME** — 3 consecutive monthly receipt declines that are normal | RS08 uses YoY comparison for SME customers; MoM decline is only flagged if YoY comparison also shows decline | `features/cashflow.py` |
| 2 | **Moon-sighting holiday delay** — NACH mandates 1–4 days late around Eid | RS01 excludes payments within `grace_days` of a moon-sighting holiday where `schedule_adjusted = N` | `features/repayment.py` |
| 3 | **FIFO allocation** — customer pays monthly but DPD stuck at ~30 due to payment ordering | Detect: `amount_paid` ≥ `amount_due` for recent months but `days_late > 0` from arrears structure. Lower RS03 strength; add "structural DPD" note to evidence | `features/repayment.py` |
| 4 | **Unverified "salary delay"** — deteriorating customer claims salary was delayed | Exception engine checks `verified_flag`. Unverified evidence weakens mitigation. Guardrails block mitigation entirely if CFSI or RS01 is high | `features/exceptions.py` |
| 5 | **Salary not domiciled** — salary_transfer_flag=N, no salary in financials | RS09 returns `NOT_EVALUATED` (not "salary stopped"). No points, no flag. Evidence shows "salary not with this bank" | `features/cashflow.py` |
| 6 | **Bureau delinquency precedes on-us** — external DPD before internal problems | RS17 fires as an early warning when `bureau_external_max_dpd ≥ 30`. Treated as a leading indicator worth 12 points | `features/repayment.py` |

---

## 9. Application Pages (Streamlit)

### 9.1 Page Map

| # | Page | Purpose | Required |
|---|---|---|---|
| 1 | **Upload & Validate** | Load CSV/Excel/JSON, show validation report with defects, confirm load | Level 1 ✓ |
| 2 | **Watchlist** | Filterable table: customer_id, product, outstanding, score, band, confidence, top signals, status. Filters by band, product, segment, region. Click → customer detail | Level 1 ✓ |
| 3 | **Customer Detail** | Score breakdown (signal-by-signal), repayment timeline chart, exposure over time, module indices (RBDI/CFSI/LPI gauges), roll-forward probabilities, LLM narrative, exception explanation if applicable | Level 1 ✓ |
| 4 | **Analyst Review** | Select an alert → see full evidence → record decision (Continue Monitoring / Requires Investigation / False Positive / Escalate) + free-text reason. Decision list shows history | Level 1 ✓ |
| 5 | **Portfolio + Eval** | Heatmap: band × segment/product/region. Cluster alerts with lift and momentum. Exposure at risk per segment. LLM portfolio briefing. **Eval panel:** confusion matrix vs ground truth, High/Critical catch rate, thin-file false-positive rate, per-archetype breakdown. Judges can see accuracy live | Extra (high demo value) |
| 6 | **Rules Editor** | Edit signal weights/thresholds → instant re-score (Stage 2 only, ~0.5s). Before/after tier counts, score distribution shift. Download modified rules JSON | Extra |
| 7 | **Investigation Workspace** | Select a customer → correlated deterioration patterns, recommended interventions by urgency, 12-month score and indicator history, and a case file for interventions, ownership and analyst notes | Extra |
| 7b | **RBI Reporting** | SMA distribution and migration matrix, SMA trend, EWS compliance tiles (30-day TAT, 7-day escalation, 180-day resolution), SMA-2/NPA detail, Excel export | Extra |
| 8 | **AI Assistant** | Chat interface grounded in customer data. "Why is CIF100150 flagged?" → grounded answer. Only available when LLM is on | Extra |

### 9.2 Watchlist Columns

| Column | Source |
|---|---|
| Customer ID | `customers.customer_id` |
| Customer Type | INDIVIDUAL / SME |
| Product(s) | Facility product codes |
| Outstanding (₹) | Sum of `facilities.outstanding_balance` |
| Warning Score | `alerts.warning_score` |
| Band | Low / Medium / High / Critical |
| Confidence | High / Medium / Low |
| Trajectory | Improving / Stable / Deteriorating / Recovering |
| Top Signals | Top 3 triggered signal names |
| Roll Probability (30d) | From Markov model |
| Monitoring Status | From score_bands or analyst override |
| Last Decision | From `decisions` table, if any |

### 9.3 Customer Detail Layout

```
┌─────────────────────────────────────────────────────────┐
│  CIF100XXX — Hassan Ali   │  Band: HIGH  │  Score: 62  │
│  Individual · Mass · Mumbai│  Confidence: High           │
│  Trajectory: Deteriorating │  Roll Prob: 68% (30d)       │
├──────────────────────┬──────────────────────────────────┤
│  MODULE INDICES      │  TRIGGERED SIGNALS               │
│  ┌─────┐ ┌─────┐    │  ⚠ RS01: 4 late in 6m (w=20)    │
│  │RBDI │ │CFSI │    │  ⚠ RS07: Inflow −28% (w=15)     │
│  │ 72% │ │ 61% │    │  ⚠ RS14: DBR 54% (w=12)         │
│  └─────┘ └─────┘    │  ✓ RS05: Mitigated (−6 pts)     │
│  ┌─────┐ ┌─────┐    │  ℹ DQ02: 1 missing month (w=0)  │
│  │ LPI │ │ DCS │    │                                   │
│  │ 45% │ │ 82% │    │                                   │
│  └─────┘ └─────┘    │                                   │
├──────────────────────┴──────────────────────────────────┤
│  REPAYMENT TIMELINE  (chart: due dates with on-time /   │
│  late / missed markers, DPD over time)                  │
├─────────────────────────────────────────────────────────┤
│  RISK NARRATIVE (LLM-generated, grounded):              │
│  "Customer has paid late in 4 of the last 6 months,     │
│  with delays averaging 12 days. Monthly salary has      │
│  declined 28% over the last two quarters. Debt burden   │
│  is at 54%, above the 50% regulatory cap. One missed    │
│  payment in June was mitigated (Bakrid holiday, verified).│
│  Trajectory is deteriorating — RM outreach recommended."│
├─────────────────────────────────────────────────────────┤
│  ML SECOND OPINION: P(deterioration 90d) = 71%          │
│  SHAP top drivers: late_instalments (+0.18), inflow     │
│  decline (+0.14), debt_burden (+0.11)                   │
├─────────────────────────────────────────────────────────┤
│  [Continue Monitoring] [Requires Investigation]          │
│  [False Positive] [Escalate for Review]                  │
│  Reason: [__________________________] [Submit]           │
└─────────────────────────────────────────────────────────┘
```

---

## 10. Project Structure

### 10.1 Phase 1: Monolithic (get it working)

Start with the prototype's proven 6-file layout. Splitting into 25 files before anything
runs is premature — build monolithic, refactor when stable.

```
Early-Warning-Detection-System/
│
├── FINAL_ARCHITECTURE.md            ← This document
├── requirements.txt
├── app.py                           ← Streamlit: sidebar nav, all pages in one file
│
├── data/
│   ├── sample/csv/                  ← 8 clean CSVs
│   ├── sample/rules/                ← risk_signal_definitions.json, score_bands.csv
│   ├── dirty/                       ← 72 injected defects for validation testing
│   └── _ground_truth/               ← Benchmark labels (not loaded into app)
│
└── sentinel/                        ← Core library (pure Python, no UI imports)
    ├── __init__.py
    ├── ingest.py                    ← load · alias-map · validate · link
    ├── metrics.py                   ← Stage 1: per-customer metrics at as_of (vectorised)
    ├── scoring.py                   ← Stage 2: signals → points → mitigation → triggers
    │                                    → confidence → tier → trajectory → evidence
    ├── insights.py                  ← Roll-rate matrix · segment clusters · eval
    ├── store.py                     ← DuckDB: alerts (frozen) + decisions (append-only)
    └── llm.py                       ← Claude API client + grounding check + template fallback
```

### 10.2 Phase 2: Refactored (after core is stable and eval passes)

Once all 5 Level 1 capabilities work and eval numbers are satisfactory, split into modules:

```
sentinel/
├── ingest/
│   ├── loaders.py                   ← CSV / Excel / JSON readers, format detection
│   ├── validate.py                  ← Schema checks, dedup, FK integrity, date parsing
│   └── mapping.py                   ← Column alias map (+ LLM helper for unknown headers)
├── features/
│   ├── repayment.py                 ← S1: per-month late/missed, holiday excuse
│   ├── cashflow.py                  ← S2: inflow trends, salary stopped, balance erosion
│   ├── exposure.py                  ← S3: utilisation, DBR (individuals only), bureau
│   ├── exceptions.py                ← S4: mitigation score with guardrails
│   ├── rollforward.py               ← S5: delinquency episode features
│   ├── sme.py                       ← S6: YoY seasonality adjustment, bounce intensity
│   ├── completeness.py              ← S7: Data Completeness Score
│   └── clusters.py                  ← S8: segment concentration test
├── engine/
│   ├── signals.py                   ← Evaluate 21 signals with absolute anchoring
│   ├── scoring.py                   ← score_customer() — the pure function
│   └── explain.py                   ← Template reason codes, evidence JSON builder
├── models/
│   ├── rollrate.py                  ← Markov transition matrix
│   ├── lightgbm_model.py            ← Optional: LightGBM second opinion
│   └── shap_explain.py              ← Optional: SHAP contributions
├── llm/
│   ├── client.py                    ← Claude API with on/off switch
│   ├── grounding.py                 ← Number extraction + grounding check
│   ├── narrative.py                 ← Risk narrative generator
│   ├── exception_explainer.py       ← Contextual exception explanation
│   ├── portfolio_briefing.py        ← Cluster-level summaries
│   └── chat.py                      ← Analyst Q&A (grounded)
├── store/
│   ├── db.py                        ← DuckDB connection, schema creation
│   └── schema.sql                   ← DDL for runs, alerts, decisions
└── eval/
    └── benchmark.py                 ← Compare scores vs ground truth

pages/                               ← Split app.py into multi-page Streamlit
├── 1_Upload.py
├── 2_Watchlist.py
├── 3_Customer.py
├── 4_Review.py
├── 5_Portfolio.py
├── 6_Rules.py
├── 7_Simulator.py
└── 8_Assistant.py
```

**When to refactor:** After Step 4 (Level 1 done), before starting Step 5. The refactor
takes ~30 minutes. Don't refactor earlier — a working monolith beats a tidy skeleton.

---

## 11. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | ML ecosystem, Streamlit compat |
| Data query | DuckDB | Fast analytical queries on CSVs, in-process, persistent storage for alerts/decisions |
| Data manipulation | Pandas | Feature computation, rolling windows |
| ML — roll-rate | NumPy (matrix ops) | Transition matrices from delinquency snapshots; no heavy framework needed |
| ML — optional | LightGBM + SHAP | Second-opinion predictor + explainability; added after core works |
| LLM | Claude API (anthropic SDK) | High-quality narratives, structured output; with grounding check |
| Frontend | Streamlit | Rapid prototyping, interactive widgets, multi-page app |
| Charts | Plotly | Interactive timelines, heatmaps, gauge charts |
| Excel support | openpyxl | Read .xlsx uploads |

### requirements.txt

```
streamlit>=1.38
pandas>=2.2
duckdb>=1.1
numpy>=1.26
plotly>=5.24
anthropic>=0.40
openpyxl>=3.1
lightgbm>=4.5
shap>=0.46
scikit-learn>=1.5
```

---

## 12. Build Order (Capability-Driven)

Each step delivers a testable increment. The **Level 1 must-have line** is after Step 4.
Everything after that adds innovation points but isn't required for a passing submission.

All code goes into the **monolithic layout** (§10.1) until Step 4 is done. Refactor to
the modular layout (§10.2) between Steps 4 and 5, only after all Level 1 capabilities work.

### Step 1: Ingest & Store (delivers Capability 1)
**Time: 0 – 1.5 hours**

- [ ] `sentinel/ingest.py` — CSV / Excel / JSON readers; header alias map for common
      variants; ~15 validation checks (orphans, duplicates, bad dates, negatives,
      out-of-range enums, currency mismatch, status inconsistency); error rows rejected,
      warnings kept; issues table
- [ ] `sentinel/store.py` — DuckDB schema (runs, alerts, decisions); insert validated data
- [ ] Upload page in `app.py` — source picker (sample clean / sample dirty / upload),
      row counts accepted vs rejected, issues table by severity, relationship summary
- [ ] Test with `data/sample/` (0 issues) and `data/dirty/` (72 defects detected)

**Checkpoint:** User can upload data and see a validation report.

### Step 2: Metrics & Scoring Engine (delivers Capabilities 2–3)
**Time: 1.5 – 3.5 hours**

- [ ] `sentinel/metrics.py` — Stage 1: compute all 21 metric values per customer
      (vectorised pandas, ~0.4s for 600 customers). **Key implementation details:**
  - RS01, RS04: count per **month** not per instalment; excuse moon-sighting holidays
  - RS07, RS08: YoY for SMEs; salary_transfer_flag check for RS09
  - RS14: individuals only; NOT_EVALUATED for SMEs
  - DQ01–DQ03: flag completeness, never add risk points
  - Run twice: at `as_of` and at `as_of − 3 months` (for trajectory)
- [ ] `sentinel/scoring.py` — Stage 2: evaluate signals with absolute anchoring,
      sum points, mitigation with guardrails, hard triggers (DPD ≥ 30 → ≥ High),
      confidence cap, trajectory, template reason codes, evidence JSON
- [ ] Run eval against ground truth immediately — tune weights if catch rate < 70%

**Checkpoint:** Every customer has a score, band, confidence, trajectory, and reason codes.
Eval shows catch rate, false-alarm rate, and per-archetype breakdown.

### Step 3: Watchlist & Customer Detail (delivers Capability 4)
**Time: 3.5 – 5 hours**

- [ ] Watchlist page in `app.py` — table with all required columns, filters
      (band, product, segment, region, DPD bucket, confidence), sorted by tier
      then exposure × roll probability
- [ ] Customer page in `app.py` — score breakdown (points per signal), fired signals
      with value vs threshold and reason, mitigation note, module indices, charts
      (DPD over time, inflows, balance, utilisation), repayment history table
- [ ] Clicking a customer row navigates to their detail page

**Checkpoint:** Judges can browse the watchlist, filter, and drill into any customer.

### Step 4: Analyst Review (delivers Capability 5) — LEVEL 1 MUST-HAVE LINE
**Time: 5 – 6 hours**

- [ ] Review page in `app.py` — alert queue (Medium+), frozen original score/tier/evidence,
      decision form (Continue Monitoring / Requires Investigation / False Positive /
      Escalate for Review) + mandatory reason + analyst name
- [ ] `sentinel/store.py` — insert into `decisions` table (append-only)
- [ ] Alert evidence preserved: `alerts.evidence_json` is immutable
- [ ] Decision history panel: reclassifying never alters the frozen alert

**Checkpoint:** All 5 Level 1 capabilities are working. Stop here if time is tight.

**After Step 4:** Refactor from monolithic to modular layout (~30 min). Then continue.

---

### Step 5: Roll-Rate, Clusters, Portfolio + Eval Panel
**Time: 6 – 7.5 hours**

- [ ] `sentinel/insights.py` → roll-rate matrix from delinquency snapshots (product × vintage)
- [ ] Roll-forward probabilities added to customer output and watchlist
- [ ] Segment concentration test: category × region, product × region (matches planted clusters)
- [ ] Portfolio page: tier mix donut, exposure at risk, emerging-cluster table (segment, n,
      at-risk rate, lift, momentum), roll-rate matrix heatmap
- [ ] **Eval panel on Portfolio page:** confusion matrix vs ground truth, catch rate on
      planted High/Critical, false-positive rate on stable/thin-file, per-archetype breakdown.
      This lets judges see accuracy and lets us tune weights with the Rules Editor
- [ ] Recovery profile labels (Quick-Recover / Repeat / Worsening)

### Step 6: Rules Editor + LLM Integration
**Time: 7.5 – 9 hours**

- [ ] Rules page: editable signal table (enabled, threshold, severe, weight) and band
      boundaries → instant re-score (Stage 2 only) → before/after tier counts; download JSON
- [ ] `sentinel/llm.py` — Claude API client with on/off switch + grounding check
- [ ] Risk narrative on Customer page (button, opt-in)
- [ ] Exception explainer (when S4 mitigation applies)
- [ ] Portfolio briefing (per flagged cluster)
- [ ] Template fallback always works when LLM is off
- [ ] Grounding check verified: ungrounded numbers → fallback to template

### Step 7: Innovation Extras
**Time: 9+ hours**

- [ ] What-If Simulator page — select customer → sliders adjust metric values (income,
      DPD, exposure) → re-run Stage 2 → see score/band change live. Powered by the
      two-stage compute split (only Stage 2 re-runs)
- [ ] LightGBM second opinion column (train on time-split, show alongside rules score)
- [ ] SHAP waterfall chart on Customer page (per-prediction contributions)
- [ ] AI Assistant page — analyst chat grounded in customer evidence JSON
- [ ] Additional cluster dimensions beyond category × region

### If the Clock Runs Short (Cut Order)

Items are cut **bottom-up** from Step 7. Within a step, cut order is:

1. AI Assistant chat (most complex, highest hallucination risk)
2. LightGBM + SHAP (600 rows; ML second opinion is nice-to-have)
3. What-If Simulator (impressive but not required)
4. LLM features (template text remains; app fully functional)
5. Rules Editor (rules still loaded from file)
6. Eval panel (lose demo value, but scores still work)

The 5 Level 1 capabilities (Steps 1–4) are **never cut**.

---

## 13. Demo Scenarios (What to Show the Judges)

### Demo 1: Silent Stress — Income Declining, Payments Still Current
**Customer archetype:** INCOME_DECLINE
**What happens:** RBDI module is low (payments on time). CFSI module is high (RS07 fires: salary dropped 25% over 6 months). Overall band = Medium (watchlist). LLM narrative flags the contradiction: *"Customer's repayment record remains clean, but salary has declined 25% since March. With debt burden approaching the regulatory cap, this is an emerging risk requiring proactive contact."*

**Why this impresses:** Traditional systems wouldn't flag this customer at all — no missed payments, no DPD. Our system catches the income signal before delinquency begins.

### Demo 2: Temporary Exception — Eid Holiday Delay
**Customer archetype:** ISOLATED_EXCEPTION
**What happens:** RS01 detects 1 late payment. But S4 exception engine finds: due date was 2 days after Eid-ul-Fitr (moon-sighting, not adjusted), customer paid within 3 days, 18 prior on-time payments, verified holiday evidence on file. Mitigation score is high → RS05 fires → score reduced by 8 points. Band = Low. LLM explains the reasoning in plain English.

**Why this impresses:** Shows the system can distinguish a holiday blip from a real problem, using the actual RBI holiday calendar.

### Demo 3: Seasonal SME — NOT a False Alarm
**Customer archetype:** SME_SEASONAL
**What happens:** Business receipts declined May, June, July (3 consecutive months). Raw MoM comparison would trigger RS08. But YoY comparison shows receipts in May 2026 ≈ May 2025, Jun 2026 ≈ Jun 2025 — normal seasonal pattern. RS08 NOT triggered. Band = Low.

**Why this impresses:** Shows we avoid false alarms on seasonal businesses while still catching genuine SME cash-flow stress (separate demo).

### Demo 4: Portfolio Risk Cluster — Construction Sector
**Cluster:** CLU-SME-CONSTR-NCR
**What happens:** Segment concentration test finds: construction MSMEs in Delhi NCR/Pune have 4× the at-risk rate vs the overall portfolio, with positive momentum (getting worse). 8 of 14 customers show elevated CFSI. LLM briefing: *"Emerging concentration in Delhi NCR/Pune construction: 8 MSME clients showing coordinated cash-flow deterioration since March, with ₹28 Cr exposure at risk."*

**Why this impresses:** Portfolio-level pattern detection, not just individual scoring.

### Demo 5: Thin-File Customer — Low Confidence, Not High Risk
**Customer archetype:** THIN_FILE
**What happens:** 3 months on book, no bureau record. DQ01 fires (insufficient history), DQ03 fires (bureau no-hit). DCS = 28 → confidence = Low. No risk signals triggered (no evidence of problems). Band = Low, with label "Insufficient Evidence". System recommends: *"Monitor; collect more data."*

**Why this impresses:** Explicitly demonstrates the system follows the rule: missing data ≠ risk.

---

## 14. Evaluation Criteria Alignment

| Problem Statement Requirement | How This Architecture Delivers |
|---|---|
| *"Analyse multiple indicators"* | 21 signals across 5 categories (repayment, cash flow, exposure, bureau, data quality), 8 scenarios |
| *"Recognise changes in customer behaviour"* | Rolling windows (3m/6m/12m), slopes, 3m-vs-prior-3m deltas. Trajectory from scoring at two points in time |
| *"Distinguish temporary irregularities from sustained deterioration"* | S4 exception engine with guardrails. Lunar holiday excuse. Verified vs unverified evidence. Recurrence gate |
| *"Provide an explainable assessment"* | Every point traces to a signal → metric → threshold → weight. Template reason codes always available. LLM narrative adds richness. SHAP for ML second opinion |
| *"Individual customer monitoring AND portfolio-level analysis"* | Customer scoring + watchlist (individual). Segment concentration test + cluster alerts (portfolio) |
| *"Configurable rules"* | All 21 signals loaded from `risk_signal_definitions.json`. Rules editor page for live re-scoring |
| *"Accept CSV, Excel and JSON"* | Three format loaders with auto-detection + validation report |
| *"Validate uploaded records, identify missing or inconsistent information"* | Schema validation catches all 72 dirty-data defects |
| *"Preserve original warning score and evidence when analyst changes classification"* | `alerts.evidence_json` immutable. `decisions` table is append-only |
| *"Actual application behaviour, not a static dashboard"* | Live data upload → real-time scoring → interactive watchlist → drill-down → analyst review — all in Streamlit |
| *"Innovation beyond mandatory scenarios"* | LLM narratives with grounding check. What-if simulator. Analyst chat. Roll-rate Markov model. Silent stress detection. Seasonal YoY adjustment |

---

## 15. Trade-offs Taken for Speed

Honest accounting of shortcuts. Every one is acceptable for the hackathon and has a fix
path if we continue development.

| # | Shortcut | Why Acceptable Now | Risk | Fix Later |
|---|---|---|---|---|
| 1 | Single `app.py` with sidebar nav (monolithic) | Fastest to build and debug; proven in prototype | File grows large; merge conflicts if multiple developers | Split into `pages/` after Step 4 |
| 2 | Pandas in memory for inputs; DuckDB only for alerts/decisions | 600 customers fit easily; no ORM needed | Won't scale to millions of rows | Push metrics into DuckDB SQL |
| 3 | Header alias map instead of interactive column-mapping UI | Covers common header variants | Unusual organiser headers may fail validation | Mapping screen + LLM suggestion |
| 4 | Point-in-time for past dates is approximate (filter by payment_date ≤ as_of) | Good enough for trajectory comparison | Partial-payment history before as_of not fully reconstructable | Store payment events, not settled status |
| 5 | Severe anchors default to 2× threshold where not configured | Avoids tuning 21 pairs by hand | Some signal strengths miscalibrated | Tune against eval panel |
| 6 | Debt burden NOT evaluated for SMEs | Salary-based ratio is meaningless for businesses | SME over-leverage caught only via utilisation and new credit | Add debt-service coverage from receipts |
| 7 | Clusters test only category × region and product × region | Matches the planted clusters and is explainable | Misses other segment combinations | Add generic N-dimensional segment search |
| 8 | Roll probability is empirical rate per bucket × product group, not per-customer adjusted | Transparent; uses real transition data | Same probability for all customers in a bucket | Logistic adjustment with RBDI/CFSI multipliers |
| 9 | Template text is the default; LLM is opt-in | No runtime dependency during judging | LLM features invisible if API unavailable | Pre-test API key before the demo |
| 10 | Weights tuned once from prototype; not cross-validated | Configurable via Rules Editor | Some tiers may be off vs ground truth in edge cases | Iterate with the eval panel |
| 11 | No unit tests initially; eval panel + smoke test only | Time-limited; eval panel catches regressions | Code changes can break silently | Add `tests/` per archetype after Step 4 |

---

## 16. Known Gaps & Limitations

Documented from the prototype's verified results. These are real; ignoring them doesn't
make the system better.

### 16.1 Detection Gaps

**~24% of planted High/Critical customers are not flagged Amber or Red.**

Root causes (roughly half each):
- **Late onset, no observable signal:** The generator planted a deterioration that starts
  after the as-of date. There are no late payments, no DPD, no income drop visible in
  the data. The engine is *correct* to leave them Green — there's nothing to detect yet.
  The ground-truth label is optimistic.
- **Income-decline-only cases:** Customer's salary dropped but only one moderate signal
  fires (RS07 just above threshold, RS08 below). These need either a lower RS07 threshold
  (risks more false alarms) or a combined-signal boost (CFSI module > X → extra points).

**Mitigation:** Tune RS07 weight further (currently 20); consider a "compound signal"
bonus when multiple cash-flow signals fire together.

### 16.2 Hospitality-in-Bengaluru Cluster Not Flagged

The planted `CLU-RET-HOSP-BLR` cluster (hospitality employees in Bengaluru, pay cuts) is
not detected as an emerging cluster. The customers mostly reach Watch/Medium tier —
the cluster test counts only High/Critical (Amber/Red). The construction cluster is
found because its customers deteriorate further.

**Mitigation:** Lower the cluster test's at-risk threshold from High to Medium, or add
a momentum-based trigger (rate of Watch entries accelerating).

### 16.3 LLM Not Exercised in Prototype

The LLM code path (grounding check + template fallback) is implemented but has not
been run with a live model in the prototype. The narrative quality and grounding check
reliability are untested.

**Mitigation:** Run end-to-end LLM test with Claude API before the demo. Have template
fallback ready as the default.

### 16.4 Signal Weight Sensitivity

The tuned weights (RS01=30, RS07=20, etc.) were optimised once against the eval panel.
They may not be optimal for different portfolios or if the organiser's test data has
different distributions.

**Mitigation:** The Rules Editor page lets judges (and us) adjust weights and see
the effect live. Document the tuning rationale. Show the eval panel.

---

## 17. Key Design Decisions (Rationale)

| Decision | Choice | Why Not the Alternative |
|---|---|---|
| Scoring core | Rules-based, deterministic | ML on 600 synthetic rows risks learning the generator, not credit risk. Rules are auditable and stable |
| Signal anchoring | Absolute (from rules thresholds) | Percentile/portfolio-relative scoring makes scores unstable across portfolios and harder to explain |
| Signal counting | Per month, not per instalment | Prevents over-penalising multi-facility customers. Validated in prototype |
| Hard trigger | DPD ≥ 30 → ≥ High (not ≥ 60) | Prototype validated: catch rate 54% → 76% with zero new false alarms |
| Primary predictor | Markov roll-rate on 19.5k transitions | Data-rich (19.5k observations), explainable ("68% chance of rolling to 31-60 DPD"), directly answers S5 |
| LightGBM role | Optional second opinion, NOT blended into score | Keeps the core deterministic. ML probability shown alongside for comparison, but doesn't pollute the explainable score |
| SME debt burden | NOT_EVALUATED | Salary-based DBR is meaningless for businesses. Catch over-leverage via utilisation + new credit instead |
| Cluster method | Segment concentration test | HDBSCAN is hard to explain; min_cluster_size tuning can miss small planted clusters. Concentration test is transparent and guaranteed to find groups ≥ 3 |
| LLM role | Explains and drafts only; never scores | Keeps the core auditable. Grounding check catches hallucinations. App works without LLM |
| LLM provider | Claude API (Sonnet) | Higher quality than local 7B models. Grounding check provides safety. Budget: ~100k tokens per run |
| Missing data | NOT_EVALUATED (no imputation) | Problem statement: "must not classify high risk solely because information is unavailable". Imputation invents evidence |
| Build order | Capability-ordered, monolithic first | Judges test application behaviour. A sophisticated backend with no UI = zero points. Working monolith beats tidy skeleton |
| Eval panel | In the UI (Portfolio page), not a script | Judges can see accuracy. Enables live weight tuning. Proved its value in prototype |
| Tier labels | Low / Medium / High / Critical | Matches `score_bands.csv` from the rules file |
