# Sentinel Early Warning Detection System

## Complete Architecture & Design Document

**Version:** 2.0  
**Date:** October 2026  
**Project:** Azentio AI Hackathon - Problem Statement 4  
**Team Branch:** `feat/pranav`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Solution Overview](#3-solution-overview)
4. [System Architecture](#4-system-architecture)
5. [Data Architecture](#5-data-architecture)
6. [Data Ingestion & Validation Pipeline](#6-data-ingestion--validation-pipeline)
7. [Metrics Engine](#7-metrics-engine)
8. [Risk Scoring Engine](#8-risk-scoring-engine)
9. [Machine Learning Pipeline](#9-machine-learning-pipeline)
10. [LLM Integration](#10-llm-integration)
11. [Advanced Analytics Modules](#11-advanced-analytics-modules)
12. [Regulatory Compliance (RBI SMA)](#12-regulatory-compliance-rbi-sma)
13. [Storage & Audit Trail](#13-storage--audit-trail)
14. [User Interface](#14-user-interface)
15. [Data Generation & Simulation](#15-data-generation--simulation)
16. [Technology Stack](#16-technology-stack)
17. [Performance & Validation Results](#17-performance--validation-results)
18. [Evolution & Commit History](#18-evolution--commit-history)

---

## 1. Executive Summary

**Sentinel** is an AI-powered Early Warning System (EWS) that continuously evaluates customer repayment behaviour, financial exposure, and emerging risk signals to identify potential credit deterioration **before** serious delinquency occurs. Built for the Azentio AI Hackathon (Problem 4), it covers 8 early warning scenarios, 21 risk signals, and delivers a complete analyst workflow from data ingestion through watchlist triage to regulatory reporting.

The system achieves:
- **97.5%** accuracy within one tier of ground truth
- **75.8%** catch rate for High/Critical risk customers
- **0.0%** false alarm rate on low-risk customers
- **AUC 0.96** for 1-month delinquency forecast
- **AUC 0.89** for 3-month delinquency forecast

---

## 2. Problem Statement

### 2.1 Business Context

Banks face significant losses when credit deterioration goes undetected until customers are already in serious delinquency. Traditional approaches relying on periodic reviews and hard DPD thresholds miss the early, subtle signals of financial stress - declining income, increasing leverage, partial payments, and cross-lender distress.

### 2.2 Challenge Requirements

Design and build an Early Warning Detection System that satisfies **5 Level 1 capabilities**:

| # | Capability | Description |
|---|-----------|-------------|
| L1-1 | **Data Ingestion** | Ingest and validate multi-source banking data (customers, facilities, repayments, financials, exposure, delinquency, bureau, evidence) |
| L1-2 | **Signal Detection** | Detect 8 early warning scenarios covering repayment, cash-flow, exposure, bureau, and data quality dimensions |
| L1-3 | **Warning Score** | Compute a composite risk score (0-100) with transparent signal-level breakdown |
| L1-4 | **Watchlist** | Generate and manage a prioritised watchlist with filtering, sorting, and drill-down |
| L1-5 | **Analyst Review** | Provide a full analyst workflow with decisions, audit trail, and regulatory reporting |

### 2.3 Early Warning Scenarios

| Scenario | Code | Description |
|----------|------|-------------|
| S1 | Repayment Deterioration | Increasing missed/late payments, worsening DPD trends |
| S2 | Declining Income | Sustained drop in inflows, salary loss, balance erosion |
| S3 | Increasing Exposure | Rising utilisation, new credit, debt burden growth |
| S4 | Temporary Exception | Isolated missed payment with subsequent recovery |
| S5 | Roll-Forward Risk | Progressive migration across DPD buckets |
| S6 | SME Stress | Seasonal cash-flow stress, sector-specific deterioration |
| S7 | Missing History | Thin files, incomplete data, bureau no-hits |
| S8 | Portfolio Clusters | Emerging risk concentration by geography/sector |

### 2.4 Target Setting

- **Geography:** India (INR currency, metro and tier-2 city distribution)
- **As-of Date:** 2026-09-30
- **History Window:** 24 months (October 2024 - September 2026)
- **Customer Segments:** Individual (retail) and SME
- **Portfolios:** Two synthetic bank datasets for validation

---

## 3. Solution Overview

### 3.1 Design Principles

1. **Rules-first, ML-second:** Transparent rule-based scoring is the primary system. ML provides a "second opinion" and forecasting layer, never overriding rules.
2. **Absolute anchoring:** Signal thresholds use absolute values (e.g., DPD >= 30), not percentile-based, ensuring consistency across portfolio sizes.
3. **Two-stage compute:** Expensive metrics computation is cached; cheap scoring re-runs instantly on rule changes.
4. **LLM as narrator, not calculator:** The LLM generates natural-language summaries but never computes scores or changes decisions.
5. **Audit everything:** Frozen alerts, append-only decisions, intervention history - nothing is ever deleted or mutated.

### 3.2 Five-Layer Architecture

```
+-------------------------------------------------------------------+
|                    Layer 5: Streamlit UI                           |
|  Home | Upload | Portfolio | Watchlist | Customer | Investigation  |
|  Review | Rules Editor | RBI Reporting | AI Assistant             |
+-------------------------------------------------------------------+
|                    Layer 4: LLM Narrative                          |
|  Ollama (qwen3:4b-instruct) | Risk Summaries | Portfolio Briefing |
|  Grounding Check | Template Fallback                              |
+-------------------------------------------------------------------+
|                    Layer 3: Scoring Engine                         |
|  Signal Evaluation | Points Summation | Mitigation | Triggers     |
|  Confidence | Trajectory | Tier Assignment                       |
+-------------------------------------------------------------------+
|                    Layer 2: Metrics Engine                         |
|  Repayment | Delinquency | Cash-Flow | Exposure | Completeness   |
|  Point-in-Time | Holiday-Aware | SME-Adjusted                    |
+-------------------------------------------------------------------+
|                    Layer 1: Data Ingestion                         |
|  CSV/Excel/JSON | Header Mapping | 21+ Validation Checks         |
|  Relationship Integrity | Error/Warning Classification            |
+-------------------------------------------------------------------+
```

---

## 4. System Architecture

### 4.1 Module Map

The `sentinel/` package contains 14 Python modules, each with a single responsibility:

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `ingest.py` | Data loading, validation, cleaning | `load_and_validate()` |
| `metrics.py` | Point-in-time metric computation | `compute()`, `_repayment_metrics()`, `_cashflow_metrics()`, `_exposure_metrics()` |
| `scoring.py` | Signal evaluation and tier assignment | `score()` |
| `forecast.py` | LightGBM delinquency prediction | `train_delinquency_model()`, `predict()` |
| `ml.py` | ML second opinion with SHAP | `build_ml_second_opinion()`, `explain_customer()` |
| `llm.py` | Local LLM integration | `summarise_customer()`, `portfolio_briefing()` |
| `insights.py` | Roll-rates, clusters, recovery profiles | `roll_rate_matrix()`, `emerging_clusters()` |
| `store.py` | DuckDB persistence and audit | `publish_alerts()`, `record_decision()` |
| `correlation.py` | Multi-signal pattern detection | `analyse()` |
| `interventions.py` | Intervention recommendations | `recommend()` |
| `priority.py` | Composite priority scoring | `score()`, `quadrants()` |
| `rbi.py` | RBI SMA regulatory reporting | `classify_sma()`, `compliance_summary()`, `export_excel()` |
| `trends.py` | Historical trend analysis | `compute_history()`, `extended_trajectory()`, `indicator_timeline()` |

### 4.2 Data Flow

```
                  CSV / Excel / JSON
                         |
                    [ ingest.py ]
                    Validate & Clean
                         |
                  clean_tables dict
                  {customers, facilities,
                   repayments, financials,
                   exposure, delinquency,
                   bank_holidays, evidence}
                         |
              +----------+-----------+
              |                      |
        [ metrics.py ]         [ forecast.py ]
        Compute metrics        Train delinquency
        (cached)               model (PIT panel)
              |                      |
         metrics_df             forecast_df
              |                   (p_1m, p_3m,
              |                    SHAP drivers)
              |                      |
        [ scoring.py ]               |
        Evaluate signals,            |
        assign tiers                 |
              |                      |
          scored_df                   |
              |                      |
     +--------+--------+            |
     |        |        |            |
  [corr]  [interv]  [prior]         |
  [trends]  [rbi]   [ml.py]  <-----+
     |        |        |
     +--------+--------+
              |
        [ store.py ]
        DuckDB persist
              |
        [ app.py ]
        Streamlit UI
              |
        [ llm.py ]
        AI Narratives
```

### 4.3 Two-Stage Compute Model

**Stage 1 - Metrics (Heavy, Cached):**
- Runs once per dataset + as-of-date combination
- Computes all 21 metric values per customer using vectorised pandas
- Handles holiday-aware grace periods, SME seasonal adjustments
- Result cached in `st.session_state` (~0.4s for 600 customers)

**Stage 2 - Scoring (Cheap, Real-time):**
- Re-runs on every rule change or parameter edit
- Maps metrics to signal strengths using absolute thresholds
- Computes weighted sum, mitigation, triggers, confidence, trajectory
- Result: scored DataFrame with tier, score, reasons, evidence (~0.5s)

---

## 5. Data Architecture

### 5.1 Input Tables

| Table | Key Columns | Grain |
|-------|------------|-------|
| **customers** | customer_id, customer_type, customer_segment, portfolio, region, declared_monthly_income, declared_annual_turnover, salary_transfer_flag | One row per customer |
| **facilities** | facility_id, customer_id, product_code, product_name, facility_type, sanctioned_amount, outstanding_balance, facility_status | One row per facility |
| **repayments** | repayment_id, facility_id, customer_id, due_date, amount_due, payment_date, amount_paid, days_late, repayment_status | One row per instalment |
| **financials** | month, customer_id, salary_credit, business_receipts, total_inflows, total_outflows, net_cash_flow, closing_balance | Monthly per customer |
| **exposure** | snapshot_date, customer_id, revolving_utilisation_pct, bureau_external_outstanding, bureau_score, debt_burden metrics | Monthly snapshot per customer |
| **delinquency** | snapshot_date, facility_id, customer_id, days_past_due, dpd_bucket, overdue_amount, ifrs9_stage, npl_flag | Monthly snapshot per facility |
| **bank_holidays** | holiday_date, holiday_name, holiday_type | Reference table |
| **exception_evidence** | evidence_id, customer_id, event_date, exception_type, description, verified_flag | Event-level evidence |

### 5.2 Synthetic Portfolios

**Portfolio 1: Synthetic Bharat Commercial Bank**
- 600 customers (420 individual, 180 SME)
- Retail-led portfolio, 0.2% NPL rate
- Default profile, INR currency

**Portfolio 2: Northern Plains Commercial Bank**
- 1,200 customers (660 individual, 540 SME)
- SME-heavy mid-market bank, 5.6% NPL rate
- Heavier stress archetypes including LEGACY_NPL
- 3 planted risk clusters: CLU-SME-LOGI-NORTH, CLU-RET-TRADE-NORTH, CLU-SME-REST-AJM

### 5.3 Data Quality Testing

The system includes a **dirty data variant** with 73 deliberately injected issues across 10 defect types:

| Defect Type | Count | Tables Affected |
|-------------|-------|-----------------|
| MISSING_MANDATORY | ~8 | customers, facilities |
| INCONSISTENT_FORMAT | ~7 | customers, repayments |
| INVALID_DATE | ~8 | facilities, repayments |
| DUPLICATE_RECORD | ~7 | customers, facilities |
| ORPHAN_REFERENCE | ~7 | facilities, repayments |
| NEGATIVE_AMOUNT | ~8 | facilities, repayments |
| CURRENCY_MISMATCH | ~7 | facilities |
| INCONSISTENT_STATUS | ~7 | facilities |
| RELATIONSHIP_MISMATCH | ~7 | repayments |
| OUTLIER | ~7 | facilities |

---

## 6. Data Ingestion & Validation Pipeline

### 6.1 File Loading (`ingest.py`)

The ingestion module supports three file formats:
- **CSV:** Direct pandas read with UTF-8 encoding
- **Excel (.xlsx):** Reads every sheet, detects table type from sheet name
- **JSON:** Handles both array-of-objects and keyed-object structures

**Table Detection:** Uses filename and sheet name hints:
- `customer` / `cust` -> customers
- `facilit` / `loan` -> facilities
- `repay` / `payment` -> repayments
- `financial` / `cashflow` -> financials
- `exposure` / `bureau` -> exposure
- `delinq` / `dpd` / `arrear` -> delinquency
- `holiday` -> bank_holidays
- `evidence` / `exception` -> exception_evidence

### 6.2 Header Normalisation

All headers are lowercased and snake_cased, then mapped through an alias dictionary:

```
cust_id, client_id           -> customer_id
loan_id, account_id          -> facility_id
product_type                 -> product_name
outstanding, balance         -> outstanding_balance
instalment, emi              -> instalment_amount
payment_amt                  -> amount_paid
```

### 6.3 Validation Checks (21+)

| # | Check | Severity |
|---|-------|----------|
| 1 | Missing mandatory columns | ERROR |
| 2 | Null values in required fields | ERROR |
| 3 | Invalid date formats | ERROR |
| 4 | Future dates beyond as-of | WARNING |
| 5 | Negative monetary amounts | ERROR |
| 6 | Duplicate primary keys | ERROR |
| 7 | Orphan foreign key references | ERROR |
| 8 | Invalid enum values (status, type) | WARNING |
| 9 | Text in numeric columns | ERROR |
| 10 | Currency mismatches | WARNING |
| 11 | Maturity before disbursement | WARNING |
| 12 | Outstanding exceeds sanctioned | WARNING |
| 13 | Payment exceeds amount due | WARNING |
| 14 | Zero or negative interest rates | WARNING |
| 15 | Facility-customer relationship integrity | ERROR |
| 16 | Repayment-facility relationship integrity | ERROR |
| 17 | Status consistency (closed + outstanding) | WARNING |
| 18 | DPD bucket vs days_past_due consistency | WARNING |
| 19 | Outlier detection (statistical) | WARNING |
| 20 | Missing financial coverage gaps | WARNING |
| 21 | Bureau score range validation | WARNING |

**Output:** `(clean_tables, issues, summary)` where issues carry ERROR (row rejected) or WARNING (normalised and kept) severity.

---

## 7. Metrics Engine

### 7.1 Overview (`metrics.py`)

The metrics engine computes point-in-time values for every customer at a given as-of date. All metrics are computed using vectorised pandas operations for performance.

### 7.2 Repayment Metrics

| Metric | Description | Logic |
|--------|-------------|-------|
| `late_or_missed_instalments` | Count of months with late/missed payments | Per-month (not per-instalment) in trailing window. Excuses moon-sighting holidays with grace_days=3 and up to 5-day adjustment |
| `partial_payments` | Count of partial payments | amount_paid > 0 but < amount_due |
| `isolated_delay_recovered` | Boolean: single late month followed by recovery | Exactly 1 late month in window AND most recent 2 months current |
| `evidence_verified` | Boolean: exception evidence exists and is verified | Matches customer_id + verified_flag=True |
| `consecutive_missed_months` | Consecutive months of missed payments | Used for hard trigger (>=2 -> at least Amber) |

### 7.3 Delinquency Metrics

| Metric | Description |
|--------|-------------|
| `current_dpd` | Current days past due (max across facilities) |
| `dpd_increase_month_ends` | Count of consecutive monthly DPD increases |
| `months_since_cure` | Months since last transition from 30+ DPD to current |
| `max_dpd_12m` | Maximum DPD in trailing 12 months |
| `dpd_history` | Full DPD time series (used by trends module) |

### 7.4 Cash-Flow Metrics

| Metric | Description | SME Adjustment |
|--------|-------------|----------------|
| `inflow_change_3m_vs_prior_3m_pct` | 3-month average inflow vs prior 3 months | SMEs use YoY comparison to account for seasonality |
| `consecutive_inflow_declines` | Count of consecutive monthly inflow drops | SMEs use YoY ratio instead of MoM |
| `months_without_salary` | Months with no salary credit (salary-at-bank only) | N/A for SMEs |
| `avg_balance_change_pct` | Average balance change over period | Same for both |
| `returned_payments` | Count of returned/bounced payments | Same for both |

### 7.5 Exposure Metrics

| Metric | Description |
|--------|-------------|
| `revolving_utilisation_pct` | Current revolving balance / limit |
| `utilisation_change_pp` | Percentage point change in utilisation |
| `debt_burden_ratio_pct` | Total obligations / income (individuals only, SME=NaN) |
| `new_facilities_or_enquiries` | New facilities + bureau enquiries in 3 months |
| `exposure_growth_minus_income_growth_pp` | Exposure growth rate minus income growth rate |
| `bureau_external_max_dpd` | Max DPD at other lenders |
| `bureau_score_change` | Bureau score change over period |
| `bureau_no_hit` | Boolean: no bureau record found |

### 7.6 Data Completeness Score

```
DCS = 0.35 x history_score + 0.30 x coverage_score + 0.15 x bureau_score + 0.20 x income_visible
```

Where:
- `history_score` = min(months_of_history / 12, 1.0)
- `coverage_score` = 1.0 - (missing_financial_months / expected_months)
- `bureau_score` = 0 if bureau_no_hit, else 1.0
- `income_visible` = 1.0 if salary_transfer or has financials, else 0.5

---

## 8. Risk Scoring Engine

### 8.1 Signal Definitions (`scoring.py`)

21 signals organised into 5 categories:

**Repayment Signals (RBDI):**

| Signal | Name | Weight | Threshold | Severe |
|--------|------|--------|-----------|--------|
| RS01 | Late or missed instalments | 30 | >= 2 | >= 4 |
| RS02 | Worsening DPD trend | 15 | >= 2 consecutive rises | >= 4 |
| RS03 | Current arrears | 15 | DPD >= 30 | DPD >= 90 (bucket-scaled) |
| RS04 | Partial payments | 10 | >= 2 | >= 4 |
| RS05 | Isolated delay recovery (mitigant) | -10 | isolated_delay_recovered=True | x1.5 if verified |
| RS06 | Recent cure | 8 | months_since_cure <= 6 | months_since_cure <= 3 |

**Cash-Flow Signals (CFSI):**

| Signal | Name | Weight | Threshold | Severe |
|--------|------|--------|-----------|--------|
| RS07 | Sustained inflow decline | 20 | decline >= 15% | >= 30% |
| RS08 | Consecutive inflow declines | 8 | >= 3 months | >= 5 |
| RS09 | Salary/income stopped | 15 | >= 2 months no salary | >= 4 |
| RS10 | Balance erosion | 10 | decline >= 30% | >= 60% |
| RS11 | Returned payments | 10 | >= 2 | >= 4 |

**Exposure Signals (LPI):**

| Signal | Name | Weight | Threshold | Severe |
|--------|------|--------|-----------|--------|
| RS12 | High revolving utilisation | 12 | >= 75% | >= 90% |
| RS13 | Utilisation spike | 10 | increase >= 20pp | >= 40pp |
| RS14 | Debt burden ratio | 15 | >= 50% (individuals only) | >= 70% |
| RS15 | New credit accumulation | 12 | >= 3 new facilities/enquiries | >= 5 |
| RS16 | Exposure outpacing income | 10 | gap >= 15pp | >= 30pp |

**Bureau Signals:**

| Signal | Name | Weight | Threshold | Severe |
|--------|------|--------|-----------|--------|
| RS17 | External delinquency | 12 | bureau_max_dpd >= 30 | >= 60 |
| RS18 | Bureau score drop | 6 | drop >= 50 points | >= 100 |

**Data Quality Signals (weight = 0, affects confidence only):**

| Signal | Name | Effect |
|--------|------|--------|
| DQ01 | Insufficient history | Reduces confidence |
| DQ02 | Missing financial months | Reduces confidence |
| DQ03 | Bureau no-hit | Reduces confidence |

### 8.2 Scoring Pipeline

```
For each customer:

1. SIGNAL EVALUATION
   - For each enabled signal, evaluate metric against threshold
   - strength = 0.0 (not firing), 0.5 (threshold met), 1.0 (severe met)

2. RAW SCORE COMPUTATION
   - raw_score = SUM(weight x strength) for positive-weight signals
   - Capped at 100

3. MODULE INDICES
   - RBDI = (RS01-RS06 earned points) / (RS01-RS06 available) x 100
   - CFSI = (RS07-RS11 earned points) / (RS07-RS11 available) x 100
   - LPI  = (RS12-RS16 earned points) / (RS12-RS16 available) x 100
   - BUREAU = (RS17-RS18 earned points) / (RS17-RS18 available) x 100

4. MITIGATION (RS05 only)
   - If isolated_delay_recovered AND (CFSI < 50 AND LPI < 50):
     - Apply RS05 weight (-10) x strength
     - If evidence_verified: multiply by 1.5 (= -15)
   - If CFSI >= 50 OR LPI >= 50: mitigation blocked

5. RISK SCORE = clip(raw_score + mitigation, 0, 100)

6. TIER ASSIGNMENT
   - Green:  0-24
   - Watch: 25-44
   - Amber: 45-69
   - Red:   70-100

7. HARD TRIGGERS (override tier upward only)
   - DPD >= 90           -> minimum Red
   - DPD >= 30           -> minimum Amber
   - consecutive_missed >= 2 -> minimum Amber

8. CONFIDENCE
   - High:   DCS >= 75
   - Medium: DCS >= 40
   - Low:    DCS < 40
   - LOW CONFIDENCE OVERRIDE: Low + no hard evidence -> capped at Watch (OV03)

9. TRAJECTORY (vs score 3 months ago)
   - New:           no prior score
   - Deteriorating: score increased >= 10 (adds +10 to score)
   - Improving:     score decreased >= 10 (subtracts -10 from score)
   - Recovering:    previously Amber/Red, now lower
   - Stable:        change < 10

10. OUTPUT: tier, score, confidence, trajectory, reasons[], evidence{}, module_indices{}
```

### 8.3 Score Bands

| Band | Score Range | Status | Recommended Action |
|------|-------------|--------|--------------------|
| Green | 0-29 | NORMAL | Routine monitoring |
| Watch | 30-59 | WATCHLIST | RM review; soft-touch contact |
| Amber | 60-79 | ACTION_REQUIRED | Contact customer; consider restructuring |
| Red | 80-100 | ESCALATE | Credit committee / collections referral |

---

## 9. Machine Learning Pipeline

### 9.1 Delinquency Forecast (`forecast.py`)

**Objective:** Predict whether a customer currently below 30 DPD will breach 30+ DPD within 1 month and 3 months.

**Training Approach:**
- Point-in-time panel construction: metrics recomputed at 17 historical month-ends
- Creates ~8,800 training rows (600 customers x ~15 usable snapshots)
- Labels derived from actual future delinquency outcomes

**Model Configuration:**
```
LightGBM Classifier
  - n_estimators: 300
  - max_depth: 4
  - learning_rate: 0.03
  - min_child_samples: 40
  - subsample: 0.8
  - colsample_bytree: 0.8
  - scale_pos_weight: auto (adjusted for class imbalance)
```

**Validation:**
- Out-of-time split: last 3 monthly snapshots held out
- No data leakage: all features computed from data available at prediction time

**Results:**
- AUC 0.96 (1-month horizon)
- AUC 0.89 (3-month horizon)
- Top-10% probability capture: 90% (1m), 67% (3m)

**Output per customer:**
- `p_1m`: Probability of 30+ DPD within 1 month
- `p_3m`: Probability of 30+ DPD within 3 months
- Top-3 SHAP feature drivers

**Red-Flag Rule:** If P(30+ DPD 3m) >= 20% AND customer is Green/Watch -> flagged as "silent stress"

### 9.2 ML Second Opinion (`ml.py`)

**Objective:** Predict whether a customer will be Amber or Red (per the rules engine) in 3 months.

**Enhancements over baseline:**

| Enhancement | Detail |
|-------------|--------|
| **SMOTE Oversampling** | Balances minority class (Amber/Red) in training data |
| **Scale Pos Weight** | Additional class imbalance handling via LightGBM parameter |
| **Stratified 5-Fold CV** | Cross-validation preserving class ratios |
| **Threshold Tuning** | Optimises classification threshold to maximise F1 score |
| **Interaction Features** | 6 pairwise interactions + aggregate sums + n_signals_firing count |

**Interaction Features:**
- `late_x_dpd`: late_or_missed x current_dpd
- `inflow_x_balance`: inflow_change x balance_change
- `util_x_burden`: utilisation x debt_burden
- `missed_x_inflow`: consecutive_missed x inflow_decline
- `dpd_trend_x_exposure`: dpd_increase x exposure_growth
- `salary_x_balance`: salary_stopped x balance_erosion
- `repay_sum`, `cashflow_sum`, `exposure_sum`: module aggregates
- `n_signals_firing`: count of metrics above non-zero thresholds

**Output per customer:**
- P(deterioration 90d): probability of reaching Amber/Red
- SHAP waterfall chart showing top feature contributions

### 9.3 Roll-Rate Analysis (`insights.py`)

**Markov Transition Matrix:**
- Built from ~19,000 facility-month delinquency snapshots
- States: Current -> 1-30 -> 31-60 -> 61-90 -> 91-180 -> 180+
- Computed per portfolio x bucket
- Each customer gets the highest `p_roll` across their facilities

**Emerging Cluster Detection:**
- Wilson confidence interval test on segment-level Amber/Red rates
- Identifies segments with statistically elevated risk vs portfolio average
- Reports lift ratio and cluster size

**Recovery Profiles:**
- Tracks customers who transitioned from Amber/Red to Green/Watch
- Analyses recovery time distribution and common recovery patterns

---

## 10. LLM Integration

### 10.1 Architecture (`llm.py`)

**Runtime:** Ollama (local inference, no cloud dependency)

**Model Hierarchy:**
1. Primary: `qwen3:4b-instruct`
2. Fallback: `llama3.2:3b`
3. Final fallback: Template-based summaries (always available)

**Configuration:**
```
temperature: 0.1
max_tokens: model-dependent
think: false (disable chain-of-thought for speed)
```

### 10.2 Use Cases

| Feature | Description | Grounding |
|---------|-------------|-----------|
| **Customer Summary** | Natural-language risk narrative for a single customer | All numbers > 12 must appear in evidence |
| **Portfolio Briefing** | Morning briefing summarising portfolio risk state | Grounded against actual KPIs |
| **AI Assistant** | Multi-turn chat for analyst questions | Conversation grounded in customer evidence |

### 10.3 Guardrails

1. **Number Grounding Check:** Any number > 12 in LLM output must exist in the evidence provided. Ungrounded numbers trigger a fallback to template summary.
2. **LLM Never Computes Scores:** The scoring engine is the sole source of truth. LLM output is purely narrative.
3. **LLM Never Changes Decisions:** Analyst decisions are recorded through the structured workflow, not through chat.
4. **Template Fallback:** If Ollama is unavailable or grounding fails, a template-based summary is generated from the scored data.

---

## 11. Advanced Analytics Modules

### 11.1 Risk Correlation (`correlation.py`)

Detects multi-signal co-occurrence patterns that indicate systemic stress:

| Pattern | Name | Condition | Implication |
|---------|------|-----------|-------------|
| CORR01 | Cash-flow driven delinquency | CFSI > 30 AND RBDI > 30 | Income stress causing payment defaults |
| CORR02 | Leveraging up under stress | LPI > 30 AND (CFSI > 30 OR RBDI > 30) | Borrowing to cover shortfalls |
| CORR03 | Cross-lender distress | BUREAU > 30 AND (RBDI > 30 OR LPI > 30) | System-wide financial stress |
| CORR04 | Full deterioration | 3+ modules > 30 | Broad-based credit deterioration |

**Combined Risk Factor:**
```
base = count(modules > 30) / 4
combined_risk_factor = base + 0.15 x count(matched patterns)
```

**Overlap Warning:** Flags signal pairs that may double-count the same underlying risk (e.g., RS01 late payments + RS03 current arrears both driven by the same delinquency event).

### 11.2 Intervention Recommendations (`interventions.py`)

Maps signal patterns to specific intervention actions:

| Scenario | Urgency | Trigger | Recommended Actions |
|----------|---------|---------|---------------------|
| S_TEMPORARY_MISS | LOW | RS05 isolated delay | Soft contact, verify reason, note exception |
| S_REPEATED_DELAYS | MEDIUM | RS01 >= 0.5, no RS05 | Schedule RM meeting, review payment capacity, consider restructure |
| S_DECLINING_INCOME | HIGH | RS07 >= 0.5 OR RS09 >= 0.5 | Urgent income verification, financial review, discuss forbearance |
| S_INCREASING_EXPOSURE | MEDIUM | RS12 >= 0.5 OR RS14 >= 0.5 | Limit review, cross-sell assessment, exposure cap discussion |
| S_SUSTAINED_HIGH_RISK | CRITICAL | Tier=Red, score >= 80 | Credit committee referral, collections preparation, provision review |
| S_INSUFFICIENT_INFO | MEDIUM | DCS < 40 | Request updated financials, initiate bureau pull, schedule site visit |

### 11.3 Priority Scoring (`priority.py`)

Composite priority score (0-100) for watchlist ordering:

```
priority = severity x 0.30
         + exposure x 0.25
         + probability x 0.25
         + trajectory x 0.15
         + correlation x 0.05
```

Where:
- **severity** = risk_score (0-100)
- **exposure** = normalised outstanding balance
- **probability** = delinquency forecast p_3m (preferred), or ML second opinion, or roll probability
- **trajectory** = extended trajectory weight (Sustained-Deteriorating=1.0 down to Sustained-Improving=0.10)
- **correlation** = combined_risk_factor x 100

**Priority Quadrants:** Classifies customers into a 2x2 matrix of High/Low Priority vs High/Low Severity for triage visualisation.

### 11.4 Trend Analysis (`trends.py`)

**Historical Scoring:**
- Re-scores the entire portfolio at 3-month intervals over the trailing 12 months
- Produces a time series of risk scores and module indices per customer

**Extended Trajectory Classification:**
- Uses OLS slope of historical scores + volatility (coefficient of variation)
- Categories: Sustained-Deteriorating, Accelerating, Volatile-Worsening, Volatile, Stable, Volatile-Improving, Improving, Sustained-Improving

**Indicator Timeline:**
- Extracts underlying metric time series (DPD, inflows, balance, utilisation) for a single customer
- Visualised as a faceted chart in the Investigation page

---

## 12. Regulatory Compliance (RBI SMA)

### 12.1 SMA Classification (`rbi.py`)

Implements the Reserve Bank of India's Special Mention Account framework:

| Classification | DPD Range | Description |
|----------------|-----------|-------------|
| Standard | 0 | No overdue |
| SMA-0 | 1-30 | Principal/interest overdue 1-30 days |
| SMA-1 | 31-60 | Principal/interest overdue 31-60 days |
| SMA-2 | 61-90 | Principal/interest overdue 61-90 days |
| NPA | 91+ | Non-performing asset |

### 12.2 Regulatory Reports

| Report | Description |
|--------|-------------|
| **SMA Distribution** | Count and exposure by SMA category |
| **SMA by Portfolio** | Breakdown across portfolio segments |
| **SMA Migration Matrix** | Period-over-period transition heatmap |
| **SMA Trend** | 12-month trend of SMA distribution |
| **SMA-2/NPA Account List** | Detailed list for regulatory filing |
| **EWS Compliance Summary** | At-risk counts, exposure, pre-delinquent vs delinquent, high-exposure threshold |

### 12.3 Export

Full regulatory pack exported as Excel workbook (`.xlsx`) with separate sheets per report, formatted for submission.

---

## 13. Storage & Audit Trail

### 13.1 DuckDB Schema (`store.py`)

```sql
-- Frozen alerts: immutable snapshot at publish time
CREATE TABLE alerts (
    alert_id        VARCHAR PRIMARY KEY,
    run_id          VARCHAR,
    customer_id     VARCHAR,
    published_at    TIMESTAMP,
    original_score  DOUBLE,
    original_tier   VARCHAR,
    confidence      VARCHAR,
    reasons_json    VARCHAR,
    evidence_json   VARCHAR
)

-- Append-only analyst decisions
CREATE TABLE analyst_decisions (
    decision_id     VARCHAR PRIMARY KEY,
    alert_id        VARCHAR,
    customer_id     VARCHAR,
    analyst         VARCHAR,
    decision        VARCHAR,  -- CONTINUE_MONITORING | REQUIRES_INVESTIGATION |
                              -- FALSE_POSITIVE | ESCALATE_FOR_REVIEW
    rationale       VARCHAR,
    decided_at      TIMESTAMP
)

-- Active interventions with status tracking
CREATE TABLE interventions (
    intervention_id VARCHAR PRIMARY KEY,
    customer_id     VARCHAR,
    scenario        VARCHAR,
    urgency         VARCHAR,
    actions_json    VARCHAR,
    status          VARCHAR,  -- OPEN | IN_PROGRESS | COMPLETED |
                              -- DEFERRED | ESCALATED
    assigned_to     VARCHAR,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)

-- Append-only intervention change log
CREATE TABLE intervention_history (
    history_id      VARCHAR PRIMARY KEY,
    intervention_id VARCHAR,
    old_status      VARCHAR,
    new_status      VARCHAR,
    changed_by      VARCHAR,
    notes           VARCHAR,
    changed_at      TIMESTAMP
)

-- Free-form investigation notes
CREATE TABLE investigation_notes (
    note_id         VARCHAR PRIMARY KEY,
    customer_id     VARCHAR,
    author          VARCHAR,
    content         VARCHAR,
    created_at      TIMESTAMP
)
```

### 13.2 Design Principles

- **Alerts are frozen:** Once published, an alert's original_score, original_tier, and evidence are never updated. Live scores may diverge, enabling before/after comparison.
- **Decisions are append-only:** Multiple decisions can exist per alert, creating a full decision history.
- **Interventions track lifecycle:** OPEN -> IN_PROGRESS -> COMPLETED/DEFERRED/ESCALATED, with full change history.

---

## 14. User Interface

### 14.1 Technology

- **Framework:** Streamlit
- **Charts:** Altair (declarative, interactive)
- **Layout:** Sidebar navigation with 10 pages

### 14.2 Page Inventory

#### Page 1: Home (Morning Briefing)

The analyst's starting page providing a portfolio-level overview:

- **KPI Tiles:** Red count, Amber count, new escalations, total at-risk exposure
- **Forecast Metrics:** Expected new 30+ DPD accounts (1m/3m), at-risk exposure from forecasts
- **AI Briefing:** LLM-generated or template morning briefing paragraph
- **Red-Flag Tabs (5):**
  1. Likely 30+ DPD (silent stress from forecast)
  2. Newly Escalated (tier increased this period)
  3. Hard Triggers (DPD-based overrides)
  4. Emerging Segments (statistically elevated clusters)
  5. Recovering (previously Amber/Red, now improving)
- **Delinquency Outlook Charts:** Forecast distribution, probability vs current score scatter
- **Model Card:** Expandable panel with AUC, feature importance, validation methodology

#### Page 2: Upload & Validate

- **Bundled Datasets:** 4 pre-loaded options (Bharat Commercial Bank clean/dirty, Northern Plains clean/dirty)
- **File Upload:** CSV/Excel/JSON upload with drag-and-drop
- **Validation Dashboard:** KPIs (total records, rejected, warnings), per-table summary cards, relationship integrity matrix, filterable issues table

#### Page 3: Portfolio Dashboard

- **Tier Distribution:** Stacked bar/donut showing Green/Watch/Amber/Red split
- **Trajectory Breakdown:** Counts by Deteriorating/Stable/Improving/Recovering
- **Emerging Clusters:** Table with segment, lift ratio, count, significance
- **Roll-Rate Heatmap:** Markov transition matrix visualised as colour-coded grid
- **Pre-Delinquent vs Delinquent:** Stacked breakdown showing early vs late warning effectiveness
- **Biggest Movers:** Customers with largest score changes
- **Intervention Workload:** Pending vs completed interventions by urgency
- **Priority Matrix:** Scatter plot of priority vs severity with quadrant labels
- **Multi-Signal Correlation:** Pattern frequency and overlap analysis
- **Accuracy Panel:** Ground-truth comparison (if labels available)

#### Page 4: Watchlist

- **9 Filters:** Tier, product, segment, portfolio, DPD range, region, trajectory, confidence, SMA classification
- **Sortable Table:** Columns include priority score, risk score, tier badge, forecast probability, top signal, recommended intervention
- **Actions:** Click to open Customer Detail or Investigation page

#### Page 5: Customer Detail

- **Score Breakdown:** Tier badge, risk score gauge, confidence indicator
- **Signal Points Chart:** Horizontal bar showing each signal's point contribution
- **Signal Table:** All 21 signals with firing status, strength, weight, contribution
- **Module Indices:** RBDI, CFSI, LPI, BUREAU as percentage gauges
- **AI Summary:** LLM-generated or template risk narrative
- **Forecast Panel:** P(30+ DPD) for 1m/3m with top-3 SHAP drivers
- **ML Second Opinion:** P(deterioration 90d) with SHAP waterfall chart
- **History Charts (4):** DPD trend, income/inflow trend, utilisation trend, total obligations trend
- **Detail Tabs (4):** Repayment schedule, facility list, exposure snapshot, exception evidence

#### Page 6: Investigation

- **Priority & SMA Metrics:** Priority score, SMA classification, urgency level
- **Correlated Deterioration:** Multi-signal correlation analysis for this customer
- **Module Index Chart:** Radar or bar chart of RBDI/CFSI/LPI/BUREAU
- **Recommended Interventions:** Matched scenarios with expandable action lists
- **Historical Scoring:** 12-month risk score trend at 3-month intervals
- **Indicator Timeline:** Faceted chart showing DPD, inflows, balance, utilisation over time
- **Case File:** Active interventions (create/update), investigation notes (add/view)

#### Page 7: Analyst Review

- **Publish Action:** Freeze current alerts to DuckDB
- **Alert List:** Filterable by tier, published date, decision status
- **Comparison View:** Original (frozen) score vs current live score, highlighting divergence
- **Decision Form:** 4 options (Continue Monitoring, Requires Investigation, False Positive, Escalate for Review) with rationale text
- **Decision History:** Append-only timeline of all decisions per alert

#### Page 8: Rules Editor

- **Signal Table:** Editable grid for all 21 signals (toggle enabled, adjust threshold, severe, weight)
- **Score Bands:** Editable band boundaries
- **Instant Re-score:** Changes trigger immediate Stage 2 re-computation
- **Before/After Counts:** Shows impact of rule changes on tier distribution
- **Reset:** Restore factory defaults
- **Download:** Export current rules as JSON

#### Page 9: RBI Reporting

- **SMA Distribution Chart:** Bar chart of account/exposure by SMA category
- **SMA by Portfolio:** Grouped bar chart
- **SMA Migration Matrix:** Heatmap showing period transitions
- **SMA Trend:** 12-month line chart
- **Account List:** Detailed SMA-2/NPA accounts table
- **Export:** Download full regulatory pack as Excel

#### Page 10: AI Assistant

- **Chat Interface:** Multi-turn conversation with the LLM
- **Context Grounding:** All responses grounded in actual customer data and evidence
- **Suggested Questions:** Pre-built prompts for common analyst queries
- **Session Memory:** Maintains conversation context within the session

---

## 15. Data Generation & Simulation

### 15.1 Generator Architecture (`datagen/`)

| Module | Purpose |
|--------|---------|
| `generate.py` | CLI entry point, orchestrates generation pipeline |
| `config.py` | Profile definitions, archetype distributions, product types |
| `rules.py` | Risk signal definitions, score bands, overrides |
| `tables.py` | Table generation logic for all 8 data tables |
| `simulate.py` | Behavioural simulation per archetype |
| `dirty.py` | Data quality defect injection |
| `checks.py` | Post-generation validation and statistics |

### 15.2 Generation Command

```bash
uv run python datagen/generate.py --profile northern_sme --customers 1200 --seed 2026 --out data/northern_sme
```

### 15.3 Archetype System

**Individual Archetypes (11):**

| Archetype | Weight | Behaviour |
|-----------|--------|-----------|
| STABLE | 50% | Regular payments, steady income |
| ISOLATED_EXCEPTION | 9% | One missed payment, then recovery |
| CHRONIC_LATE | 4% | Frequent late payments, never cures |
| REPAYMENT_DETERIORATION | 7% | Progressively worsening payments |
| INCOME_DECLINE | 6% | Salary/income drops over time |
| OVER_LEVERAGED | 6% | High utilisation, new credit seeking |
| ROLL_FORWARD | 4% | Progressive DPD bucket migration |
| CURED | 5% | Was delinquent, now recovered |
| THIN_FILE | 5% | Minimal history, bureau no-hit |
| SALARY_ELSEWHERE | 4% | Salary not visible at this bank |
| LEGACY_NPL | - | Already in 90+ DPD (Northern Plains only) |

**SME Archetypes (6):**

| Archetype | Weight | Behaviour |
|-----------|--------|-----------|
| SME_STABLE | 48% | Healthy business, regular payments |
| SME_SEASONAL | 15% | Cash-flow varies with business season |
| SME_CASHFLOW_STRESS | 14% | Declining business income |
| SME_OVER_LEVERAGED | 8% | Excessive borrowing vs revenue |
| SME_REPAYMENT_DETERIORATION | 7% | Worsening payment patterns |
| SME_THIN_FILE | 8% | New business, limited history |

### 15.4 Profile System

| Parameter | Bharat Commercial Bank (default) | Northern Plains |
|-----------|-------------------|-------------------|
| Customers | 600 | 1,200 |
| SME Ratio | 30% | 45% |
| Currency | INR | INR |
| Risk Clusters | 2 | 3 |
| NPL Rate | ~0.2% | ~5.6% |
| Stress Level | Normal | Heavy |
| LEGACY_NPL | No | Yes |

### 15.5 Outputs

For each profile, the generator produces:

```
data/{profile}/
  sample/
    csv/          # 8 clean CSV files
    excel/        # Single bundled Excel workbook
    rules/        # Signal definitions, score bands
  dirty/
    csv/          # 8 CSV files with injected defects
    excel/        # Bundled dirty Excel workbook
    _injected_issues.csv  # Ground truth of all injected defects
  _ground_truth/
    customer_labels.csv   # Expected risk bands per customer
  PROFILE.md              # Profile documentation
```

---

## 16. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **Language** | Python | 3.12+ | Core runtime |
| **Package Manager** | uv | Latest | Fast dependency management |
| **UI Framework** | Streamlit | Latest | Interactive web application |
| **Charting** | Altair | Latest | Declarative statistical visualisation |
| **Data Processing** | pandas, NumPy | Latest | Vectorised data manipulation |
| **ML Framework** | LightGBM | Latest | Gradient boosting for forecasts |
| **Explainability** | SHAP | Latest | Model interpretation |
| **ML Utilities** | scikit-learn | Latest | Preprocessing, metrics, CV |
| **Imbalanced Learning** | imbalanced-learn | Latest | SMOTE oversampling |
| **Storage** | DuckDB | Latest | Embedded analytical database |
| **LLM Runtime** | Ollama | Latest | Local LLM inference |
| **LLM Model** | qwen3:4b-instruct | 4B params | Risk narrative generation |

---

## 17. Performance & Validation Results

### 17.1 Clean Sample Results (Bharat Commercial Bank, 600 customers)

| Metric | Value |
|--------|-------|
| Records ingested | 67,897 |
| Validation issues | 0 |
| Green | 470 (78.3%) |
| Watch | 47 (7.8%) |
| Amber | 48 (8.0%) |
| Red | 35 (5.8%) |

### 17.2 Accuracy Metrics

| Metric | Value | Description |
|--------|-------|-------------|
| High/Critical catch rate | 75.8% | Customers expected Amber/Red correctly identified |
| False alarm rate (low-risk) | 0.0% | Low-risk customers incorrectly flagged |
| Within one tier | 97.5% | Predicted tier within 1 band of ground truth |
| Trap customers flagged | 0/130 | Zero false positives on planted trap cases |

### 17.3 Forecast Model Performance

| Horizon | AUC | Top-10% Capture |
|---------|-----|-----------------|
| 1 month | 0.96 | 90% |
| 3 months | 0.89 | 67% |

### 17.4 Dirty Data Handling

| Metric | Value |
|--------|-------|
| Records rejected (ERROR) | 516 |
| Records warned (WARNING) | 42 |
| Defect types detected | 15/15 (100%) |
| Injected issues | 73 |

### 17.5 Emerging Cluster Detection

| Cluster | Segment | Lift | Significance |
|---------|---------|------|-------------|
| Top cluster | Construction & Contracting, Pune | 3.2x | High |

---

## 18. Evolution & Commit History

| Commit | Description |
|--------|-------------|
| `3a533d6` | First commit - initial project setup |
| `9f95414` | Feature set definition |
| `532ead7` | Architecture diagram |
| `e9712fc` - `631750f` | Models and data architecture |
| `0cfae8f` | Synthetic EWS dataset generator |
| `81baad3` | Application architecture review |
| `f7137fe` | 1-hour prototype plan |
| `755f532` | Sentinel EWS Streamlit prototype |
| `bc01280` | LLM bake-off (selected qwen3:4b-instruct) |
| `a5ae4c9` | Gitignore and LLM documentation |
| `91f796c` | **FINAL_ARCHITECTURE:** AI Assistant, LightGBM+SHAP, recovery profiles |
| `66da100` | New files for Northern Plains dataset |
| `1478621` | **ML Enhancement:** SMOTE, cross-validation, threshold tuning, RBI regulatory docs |
| `ed62ea7` | **Phase 2:** Risk correlation, interventions, priority scoring, RBI SMA reporting, trend analysis |

---

## Appendix A: Risk Signal Quick Reference

```
REPAYMENT (RBDI)                    CASH-FLOW (CFSI)
  RS01  Late/missed       [30]       RS07  Inflow decline    [20]
  RS02  Worsening DPD     [15]       RS08  Consecutive drops [8]
  RS03  Current arrears   [15]       RS09  Salary stopped    [15]
  RS04  Partial payments  [10]       RS10  Balance erosion   [10]
  RS05  Isolated recovery [-10]      RS11  Returned payments [10]
  RS06  Recent cure       [8]

EXPOSURE (LPI)                      BUREAU
  RS12  High utilisation  [12]       RS17  External delinq   [12]
  RS13  Utilisation spike [10]       RS18  Bureau score drop [6]
  RS14  Debt burden       [15]
  RS15  New credit        [12]     DATA QUALITY (weight=0)
  RS16  Exp vs income     [10]       DQ01  Insufficient history
                                     DQ02  Missing financials
                                     DQ03  Bureau no-hit
```

## Appendix B: Hard Trigger Rules

| Override | Condition | Effect |
|----------|-----------|--------|
| OV01 | DPD >= 90 | Minimum tier = Red |
| OV02 | DPD >= 30 | Minimum tier = Amber |
| OV03 | Low confidence + no hard evidence | Maximum tier = Watch |

## Appendix C: Module Index Formulas

```
RBDI   = (RS01 + RS02 + RS03 + RS04 + RS06 earned) / (RS01-RS04 + RS06 available) x 100
CFSI   = (RS07 + RS08 + RS09 + RS10 + RS11 earned) / (RS07-RS11 available) x 100
LPI    = (RS12 + RS13 + RS14 + RS15 + RS16 earned) / (RS12-RS16 available) x 100
BUREAU = (RS17 + RS18 earned) / (RS17-RS18 available) x 100
```

---

*Document generated from codebase analysis on 2026-10-31. Covers all changes through commit `ed62ea7` on branch `feat/pranav`.*
