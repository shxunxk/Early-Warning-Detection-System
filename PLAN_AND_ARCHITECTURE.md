# SENTINEL — Early Warning Detection System

## Plan & Architecture

---

## 1. Problem Summary

Build an AI-powered Early Warning System that continuously evaluates customer repayment
behaviour, financial exposure and emerging risk signals to identify potential credit
deterioration **before** delinquency becomes severe. The system must go beyond DPD flags —
it must detect silent stress, distinguish temporary blips from sustained deterioration, and
explain every score.

**Primary focus areas:**
- **Early Warning Signal Detection** — 21 configurable signals across 8 scenarios
- **Customer Warning Score Calculation** — composite 0–100 score with explainable tier assignment

**Setting:** Indian bank, 600 customers (420 individual, 180 MSME), INR currency, as-of date 2026-09-30.

---

## 2. Data Landscape

### 2.1 Available Tables (from `data/sample/csv/`)

| Table | Rows | Grain | Key Columns |
|---|---|---|---|
| `customers` | 600 | 1 per customer | customer_id, type (INDIVIDUAL/SME), segment, region, salary_transfer_flag, declared_income/turnover |
| `facilities` | 1,037 | 1 per loan/card | facility_id, product_code (PF/AF/HF/CC/TL/WC/EF), type (INSTALMENT/REVOLVING), sanctioned_amount, outstanding_balance, instalment_amount |
| `repayments` | 19,252 | 1 per instalment due | due_date, amount_due, payment_date, amount_paid, days_late, repayment_status |
| `delinquency` | 19,508 | facility × month-end | days_past_due, dpd_bucket, overdue_amount, max_dpd_12m, ifrs9_stage |
| `exposure` | 13,744 | customer × month-end | on-us totals + bureau (external facilities, outstanding, obligations, card utilisation, enquiries, bureau_score) |
| `financials` | 13,681 | customer × month | salary_credit, business_receipts, total_inflows/outflows, balances, returned_payments_count |
| `exception_evidence` | 51 | per event | exception_type, evidence_source, verified_flag |
| `bank_holidays` | 24 | per date | holiday_type (FIXED/LUNAR), schedule_adjusted flag |

### 2.2 Rules Engine Config (`data/sample/rules/`)

- `risk_signal_definitions.json` — 21 signals (RS01–RS18, DQ01–DQ03), each with metric, lookback, threshold, weight, severity
- `score_bands.csv` — Low (0–29), Medium (30–54), High (55–74), Critical (75–100)
- 3 overrides: 90+ DPD → Critical, 60+ DPD → High, insufficient data → low confidence (not higher band)

### 2.3 Ground Truth (`_ground_truth/customer_labels.csv`)

15 behaviour archetypes with expected_risk_band and expected_data_sufficiency.
Key archetypes to detect: REPAYMENT_DETERIORATION (23), INCOME_DECLINE (44),
OVER_LEVERAGED (25), ROLL_FORWARD (12), SME_CASHFLOW_STRESS (27).
Two planted risk clusters: CLU-SME-CONSTR-NCR, CLU-RET-HOSP-BLR.

### 2.4 Built-in Traps (must handle)

| Trap | Wrong response | Correct response |
|---|---|---|
| Seasonal SME 3-month decline | Flag as cash-flow stress | Compare YoY; normal seasonality → no flag |
| Lunar holiday direct-debit delay (1–4 days) | Flag as late payment | Excuse via bank_holidays calendar + grace_days |
| FIFO allocation stuck at ~30 DPD | Escalate as delinquent | Recognise customer is paying monthly; DPD is structural |
| Unverified "salary delay" claim | Clear the warning | Keep warning; unverified evidence ≠ mitigation |
| Salary not domiciled (salary_transfer_flag=N) | Flag missing salary as income drop | No salary expected; not a signal |
| Bureau delinquency before on-us | Ignore external signals | External DPD is an early warning for on-us default |

### 2.5 Dirty Data (`data/dirty/`)

72 injected defects across tables: missing mandatory fields, invalid dates, duplicate records,
orphan foreign keys, negative amounts, currency mismatches, inconsistent statuses, impossible
DPD values. The app must validate uploads and report these issues.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — DATA INGESTION & VALIDATION                                     │
│                                                                             │
│  Upload (CSV/Excel/JSON) → Schema Validation → Dedup → FK Checks           │
│  → Type Coercion → Holiday Calendar Load → DuckDB / Pandas DataFrames      │
│                                                                             │
│  Output: Validated DataFrames + Data Quality Report                         │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — FEATURE ENGINEERING (per customer, as-of 2026-09-30)            │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Repayment    │  │ Cash Flow    │  │ Exposure     │  │ SME Stress   │   │
│  │ Module (S1)  │  │ Module (S2)  │  │ Module (S3)  │  │ Module (S6)  │   │
│  │ → RBDI       │  │ → CFSI       │  │ → LPI        │  │ → SMESI      │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │                 │             │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────┴───────┐   │
│  │ Exception    │  │ Roll-Forward │  │ Data         │  │ Portfolio    │   │
│  │ Engine (S4)  │  │ Risk (S5)    │  │ Complete(S7) │  │ Cluster (S8) │   │
│  │ → Mitigation │  │ → Roll Prob  │  │ → DCS        │  │ → Cluster ID │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         └─────────────────┴─────────────────┴─────────────────┘             │
│                                   │                                         │
│                     Feature Vector (per customer)                           │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — SCORING ENGINE (ML + Rules + LLM)                               │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  3A. RULES ENGINE                                                    │   │
│  │  Evaluate 21 signals (RS01–RS18, DQ01–DQ03) per customer             │   │
│  │  Sum weighted triggers → Raw Score (0–100, capped)                   │   │
│  │  Apply overrides (OV01–OV03) → Band assignment                       │   │
│  │  Apply exception mitigation (S4) → Adjusted Band                     │   │
│  │  Apply data confidence cap (S7) → Final Band + Confidence            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  3B. ML MODELS                                                       │   │
│  │                                                                      │   │
│  │  LightGBM ──→ P(deterioration in 90d) ──→ Isotonic Calibration      │   │
│  │  Logistic Reg (WoE) ──→ Baseline/Challenger probability             │   │
│  │  Markov Chain ──→ Roll-forward P(next bucket) at 30d/60d            │   │
│  │  Isolation Forest ──→ Anomaly flag (unusual signal combinations)    │   │
│  │  HDBSCAN ──→ Portfolio risk clusters                                │   │
│  │  SHAP ──→ Per-prediction feature contributions                      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  3C. LLM INTEGRATION (Claude API)                                    │   │
│  │                                                                      │   │
│  │  Signal Synthesizer ── Cross-checks ML + Rules, detects conflicts   │   │
│  │  Reason Code Generator ── SHAP values → natural language reasons    │   │
│  │  Exception Judge ── Contextual evaluation of missed payments        │   │
│  │  Analyst Chat ── Q&A grounded in customer's full signal profile     │   │
│  │  Portfolio Narrator ── Cluster-level risk summaries                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Final Output per Customer:                                                 │
│    warning_score (0–100) | band (Low/Med/High/Critical) | confidence        │
│    trajectory | top reason codes | ML probability | roll probs              │
│    mitigation flag | cluster tag | recommended action                       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — APPLICATION (Streamlit)                                          │
│                                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐  │
│  │  Data Upload │ │  Watchlist   │ │  Customer   │ │  Portfolio          │  │
│  │  & Validation│ │  & Filters   │ │  Deep-Dive  │ │  Risk Dashboard     │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────────┘  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐  │
│  │  Analyst     │ │  What-If     │ │  AI Chat    │ │  Data Quality       │  │
│  │  Review      │ │  Simulator   │ │  Assistant  │ │  Report             │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Early Warning Signal Detection (Deep Dive)

### 4.1 Signal Evaluation Pipeline

For each customer, the engine evaluates all 21 signals from `risk_signal_definitions.json`:

```
For each signal (RS01..RS18, DQ01..DQ03):
  1. Compute the metric value from the feature vector
  2. Apply the lookback window (0, 3, 4, 6, or 12 months)
  3. Evaluate: metric {operator} threshold → triggered (bool)
  4. If triggered: contribution = weight × severity_scale
  5. Special handling:
     - RS03 (current DPD): scale weight by bucket (50%/75%/100%)
     - RS05 (isolated delay): negative weight = mitigation
     - RS08 (consecutive declines): YoY comparison for SMEs
     - RS09 (salary stopped): skip if salary_transfer_flag = N
     - DQ01–DQ03: weight=0, only affects confidence
```

### 4.2 Eight Scenario Modules

#### S1: Repayment Behaviour Deterioration (signals RS01, RS04)
**Input:** `repayments` table (per instalment: due_date, paid_date, amount_due, amount_paid, days_late)

**Features computed:**
| Feature | Formula | Purpose |
|---|---|---|
| Lateness Rate (F1.1) | late_payments / total_due over 6m | Frequency of late payments |
| Delinquency Severity (F1.2) | 0.6×avg_DPD + 0.4×max_DPD (6m, cap 90) | How bad the delays are |
| Payment Shortfall (F1.3) | 1 − (sum_paid / sum_due) over 3m, 6m | Partial payment detection |
| Missed Intensity (F1.4) | missed_6m + 2×max_consecutive_missed | Bunching of misses |
| Inconsistency (F1.5) | std_dev of days_late (6m) | Erratic behaviour |
| Deterioration Trend (F1.6) | slope of monthly DPD + 3m vs prior 3m | Direction of travel |
| Baseline Shift (F1.7) | (recent_3m_lateness − 12m_mean) / 12m_std | Z-score vs own history |
| Recurrence Flag (F1.8) | late in ≥3 of last 6 months (gate) | Isolated vs sustained |

**Composite:** RBDI (0–100) = weighted sum of normalized F1.1–F1.7, gated by F1.8.

**Key logic:** Recurrence=0 + low lateness → isolated delay, no escalation. Recurrence=1 + rising trend → sustained, escalate.

#### S2: Declining Income & Cash Flow (signals RS07, RS08, RS09, RS10)
**Input:** `financials` table (salary_credit, business_receipts, total_inflows, balances)

**Features computed:**
| Feature | Formula | Purpose |
|---|---|---|
| Inflow Trend (F2.1) | Regression slope of 6m inflow / mean (% change/month) | Income trajectory |
| Decline Magnitude (F2.2) | 1 − (3m_avg_inflow / 12m_avg_inflow) | How much income dropped |
| Consecutive Decline (F2.3) | Months in a row inflow < prior (>2% tolerance) | Sustained decline signal |
| Inflow Volatility (F2.4) | Coefficient of variation (6m) | Income stability |
| Income Stability Index (F2.5) | Salary regularity (std of credit day) + source HHI | Multi-factor stability |
| Net Cash Flow Ratio (F2.6) | (inflow − outflow) / inflow, 3m avg + trend | Spending vs earning |
| EMI Coverage Buffer (F2.7) | closing_balance / (monthly_EMI / 30) in days | Runway before default |

**Composite:** CFSI (0–100) = weighted sum of normalized features.

**Critical detection — "Silent Stress":** CFSI high AND RBDI low → customer still paying but income eroding. Flag as Watch/Amber. This is a key early warning the system must catch.

#### S3: Increasing Obligations & Exposure (signals RS12–RS16)
**Input:** `exposure` table (on-us + bureau), `facilities`

**Features computed:**
| Feature | Formula | Purpose |
|---|---|---|
| Utilisation Level + Trend (F3.1) | Current revolving utilisation + 3m change | Limit usage |
| New Credit Velocity (F3.2) | 2×new_facilities_90d + enquiries_90d | Credit hunger |
| DSR Level + Trend (F3.3) | total_EMIs / income + 3m/6m change | Debt service burden |
| Exposure Growth Rate (F3.4) | % growth in total outstanding (6m) | Debt accumulation speed |
| Income Growth Rate (F3.5) | % growth in income/inflow (6m) | Earning capacity change |
| Exposure-Capacity Gap (F3.6) | (1+F3.4) / (1+F3.5) — >1 means debt outpacing income | **Key metric** |
| Headroom Ratio (F3.7) | available_limit / total_limit | Remaining borrowing capacity |

**Composite:** LPI (0–100) = weighted sum, anchored on Exposure-Capacity Gap and DSR.

#### S4: Temporary Missed Payments — Exception Engine (signal RS05)
**Input:** `bank_holidays`, `exception_evidence`, `repayments`, `financials`

Triggers only when a missed/late event exists. Produces a Mitigation Score that can
**downgrade the tier by one level**.

| Check | Source | Weight |
|---|---|---|
| Calendar Exemption | due_date on/near LUNAR holiday (FIXED already adjusted) | 20% |
| Salary Delay | salary_credit arrived late + payment followed within X days | 20% |
| Recovery Speed | Days to clear arrears; 1 if next cycle on time | 20% |
| Clean History Depth | Consecutive on-time before the event | 20% |
| Evidence Flag | verified exception_evidence record exists | 10% |
| Event Isolation | Only 1 miss in last 12m | 10% |

**Guardrails (never mitigate):** consecutive missed ≥ 2, or Recurrence Flag = 1, or CFSI/LPI is high.

#### S5: Early Delinquency & Roll-Forward Risk (signals RS02, RS03, RS06)
**Input:** `delinquency` snapshots, features from S1/S2/S3

**Markov transition matrix** by segment (product × vintage):
```
States: Current → 1-30 DPD → 31-60 DPD → 61-90 DPD → NPA (90+)
Output: P(roll to next bucket) at 30d, 60d
        P(self-cure back to Current)
```

**Recovery profiles:**
- Quick-Recover: first episode, fast cure, low CFSI
- Repeat: ≥ 2 episodes, each cured
- Worsening: cure times lengthening or DPD rising across episodes

#### S6: SME & Business Stress
**Input:** `financials` (business_receipts), `facilities`, `exposure`

**Critical:** Apply seasonality adjustment first — deseasonalise receipts using YoY
comparison so seasonal businesses (tourism, construction) are NOT falsely flagged.

Additional SME features: receipt-to-EMI coverage, liquidity trend, bounce intensity,
activity irregularity, payer concentration.

#### S7: Missing or Incomplete History (signals DQ01–DQ03)
**Input:** All tables — measure what's present vs expected.

**Data Completeness Score (DCS, 0–100):**
- History Length (35%) + Transaction Coverage (30%) + Bureau Availability (15%) + Field Completeness (20%)

**Rules:** Missing features stay NULL (never impute as 0). DCS < 40 → tier capped at "Watch - Insufficient Evidence". Never Red from missing data alone.

#### S8: Portfolio Concentration & Risk Clusters
**Input:** All customer scores + segment attributes.

**Method:** HDBSCAN clustering on behaviour features, then measure segment deterioration
rate, lift vs portfolio, exposure at risk, spread breadth, and momentum.

**Planted clusters to detect:** CLU-SME-CONSTR-NCR (construction MSMEs, Delhi NCR/Pune,
Mar–May 2026), CLU-RET-HOSP-BLR (hospitality employees, Bengaluru, Apr–Jun 2026).

---

## 5. Customer Warning Score Calculation (Deep Dive)

### 5.1 Scoring Formula

```python
# Step 1: Rules-based raw score
raw_score = sum(
    signal.weight * severity_scale(signal)
    for signal in triggered_signals
    if signal.weight > 0
)
raw_score = min(raw_score, 100)

# Step 2: Apply mitigant signals (RS05 etc.)
mitigated_score = max(0, raw_score + sum(
    signal.weight  # negative weights
    for signal in triggered_mitigants
))

# Step 3: ML probability blending
# LightGBM gives P(deterioration in 90d), calibrated via isotonic regression
ml_prob = lightgbm_model.predict_proba(feature_vector)
ml_score = ml_prob * 100

# Step 4: Ensemble — rules and ML complement each other
#   Rules catch known patterns; ML catches non-linear interactions
final_score = 0.55 * mitigated_score + 0.45 * ml_score

# Step 5: Apply hard overrides
if current_dpd >= 90:
    final_band = "Critical"
elif current_dpd >= 60:
    final_band = max(final_band, "High")
else:
    final_band = lookup_band(final_score)  # from score_bands.csv

# Step 6: Confidence adjustment
if data_completeness_score < 40:
    confidence = "Low"
    final_band = min(final_band, "Medium")  # cap; never Red from missing data
elif data_completeness_score < 75:
    confidence = "Medium"
else:
    confidence = "High"
```

### 5.2 What Makes Each Score Explainable

Every score comes with:
1. **Triggered signals** — which of the 21 rules fired, with actual metric values
2. **SHAP contributions** — which ML features pushed the probability up/down
3. **LLM-generated reason codes** — natural language explanations grounded in data
4. **Trajectory** — Improving / Stable / Deteriorating / Recovering (from trend features)

### 5.3 Score Validation Against Ground Truth

| Archetype | Expected Band | Signals That Should Fire |
|---|---|---|
| STABLE (~200) | Low | None or minimal |
| ISOLATED_EXCEPTION (~40) | Low (mitigated) | RS05 should mitigate the late payment |
| CHRONIC_LATE_PAYER (~20) | Medium | RS01 (late instalments), but paying in full |
| REPAYMENT_DETERIORATION (~23) | High/Critical | RS01, RS02, RS03, RS04 |
| INCOME_DECLINE (~44) | Medium/High | RS07, RS08, RS09 — possibly before RS01 fires |
| OVER_LEVERAGED (~25) | Medium/High | RS12–RS16 |
| ROLL_FORWARD (~12) | High/Critical | RS02, RS03 |
| THIN_FILE (~19) | Low (low confidence) | DQ01, DQ02, DQ03 |
| SME_SEASONAL (~23) | Low (after YoY adjust) | Must NOT fire RS08 |
| SME_CASHFLOW_STRESS (~27) | High | RS07, RS08, RS10, RS11 |

---

## 6. ML Models — Detailed Design

### 6.1 LightGBM — Primary Predictor

**Task:** Binary classification — P(customer deteriorates within 90 days)

**Target definition:** From ground truth labels, a customer "deteriorates" if their
max DPD worsens by ≥ 1 bucket or warning band increases within the horizon.

**Features (input):**
- All 8 composite indices: RBDI, CFSI, LPI, Mitigation Score, Roll Risk,
  SME Stress Index, DCS, Cluster Alert Score
- Underlying granular features (~50 raw features from the feature spec)
- Customer attributes: customer_type, segment, product mix, tenure

**Training:**
- Time-based split: train on months 1–18, validate on months 19–21, test on months 22–24
- Handle class imbalance via `scale_pos_weight = n_negative / n_positive`
- Missing values: keep as NULL (LightGBM handles natively)
- Early stopping: 30 rounds on validation set
- Monotonic constraints on key features (e.g., missed_instalment_intensity must increase risk)

**Hyperparameters:**
```python
{
    "objective": "binary",
    "n_estimators": 400,
    "learning_rate": 0.05,
    "max_depth": 4,
    "num_leaves": 15,
    "min_child_samples": 50,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0
}
```

**Calibration:** Isotonic regression on validation set → outputs true probabilities.

**Evaluation:** PR-AUC (primary, handles class imbalance), ROC-AUC, calibration plot,
lead-time comparison vs DPD-only detection.

### 6.2 Logistic Regression — Baseline Scorecard

**Purpose:** Transparent, auditable baseline. If regulators ask "how does the model work",
point to the scorecard.

**Input features:** WoE-binned versions of the composite indices.

**Params:** L2 penalty, balanced class weights, max 1000 iterations.

### 6.3 Markov Transition Matrix — Roll-Forward Risk

**States:** Current → 1-30 → 31-60 → 61-90 → NPA (90+)

**Method:** Compute segment-specific (product × vintage) transition matrices from the
24-month delinquency history. Adjust per customer using RBDI, CFSI, LPI as multipliers.

**Output:** 30-day and 60-day state transition probabilities per customer.

### 6.4 Isolation Forest — Anomaly Detection

**Purpose:** Catch customers whose *combination* of signals is unusual even when no single
signal is extreme. Example: moderate CFSI + moderate LPI + declining bureau score —
individually fine, together concerning.

**Input:** Composite indices (RBDI, CFSI, LPI, SME Stress) as a vector.

### 6.5 HDBSCAN — Portfolio Risk Clustering

**Purpose:** Detect emerging risk clusters (Scenario 8).

**Input:** Customer behaviour features + segment attributes (industry, region).

**Validation:** Should identify the planted clusters (construction-Pune, hospitality-Bengaluru).

### 6.6 SHAP — Explainability

**Applied to:** LightGBM predictions.

**Output:** Per-customer feature contribution vector → fed to the LLM for natural language
reason code generation.

---

## 7. LLM Integration — Novel Approach (Deep Dive)

### 7.1 Why LLMs Here (Not Just ML)

| Capability | ML alone | ML + LLM |
|---|---|---|
| Score calculation | Numerical, fast | Same (ML does the scoring) |
| Reason codes | Template strings ("Late in 4 of 6 months") | Natural language synthesis ("Income dropped 22% while debt obligations rose, creating a widening gap — the customer is still paying but the runway is shrinking") |
| Contradiction detection | Would need hand-coded rules | LLM cross-checks signals: "RBDI says low risk but CFSI says high stress → silent stress case" |
| Exception judgment | Binary rules (mitigate if score > threshold) | Contextual: "Salary delay was verified by employer letter, and the customer has 24 months of clean history — this is a genuine exception" |
| Analyst Q&A | Not possible | "Why is CIF100150 flagged?" → grounded answer from the signal profile |
| Portfolio narratives | Cluster ID + numbers | "12 construction MSMEs in Pune are showing correlated cash-flow stress, potentially indicating a regional slowdown in the construction sector" |

### 7.2 LLM Components

#### 7.2.1 Signal Synthesizer

**When called:** After scoring, for each customer in the watchlist (Medium+ band).

**Input:** Structured JSON with all signal values, triggered rules, ML probability, SHAP top features.

**Prompt structure:**
```
You are a credit risk analyst at an Indian bank.

Given the following Early Warning signals for customer {customer_id} ({customer_type}):

SCORING SUMMARY:
- Warning Score: {score}/100 → Band: {band}
- ML 90-day Deterioration Probability: {ml_prob}%
- Data Confidence: {confidence}

TRIGGERED SIGNALS:
{for each triggered signal: signal_name, metric_value, threshold, weight, severity}

COMPOSITE INDICES:
- Repayment (RBDI): {rbdi}/100
- Cash Flow Stress (CFSI): {cfsi}/100
- Leverage Pressure (LPI): {lpi}/100
- Roll-Forward Risk: {roll_prob}%

SHAP TOP DRIVERS:
{top 5 SHAP features with contribution values}

CUSTOMER CONTEXT:
- Type: {individual/SME}, Segment: {segment}, Product: {product}
- Tenure: {months_on_book} months
- Exception Evidence: {evidence if any}

Tasks:
1. Write a 2–3 sentence risk narrative synthesizing all signals.
2. Flag any contradictions between signals (e.g., RBDI low but CFSI high).
3. Assign trajectory: Improving | Stable | Deteriorating | Recovering.
4. Generate 3–5 reason codes as plain English sentences, grounded in actual values.
5. Recommend one action: Continue Monitoring | RM Review | Restructuring Discussion | Escalate.
```

**Output:** Structured JSON with narrative, trajectory, reason_codes, recommended_action.

#### 7.2.2 Exception Judge

**When called:** Whenever Scenario 4 exception engine triggers (missed payment + possible excuse).

**Input:** The exception evidence, holiday calendar context, repayment history, mitigation score.

**Purpose:** Provides a human-readable explanation of why a missed payment is or isn't mitigated.

Example output: *"Missed payment on 2025-06-07 coincides with Bakrid (moon-sighting holiday,
not adjusted in payment schedules). Customer has 18 consecutive on-time payments prior.
Payment recovered within 3 days. Verified holiday period evidence on file. Assessment:
genuine exception — tier downgraded from Medium to Low."*

#### 7.2.3 Analyst Chat Interface

**Grounded Q&A:** The LLM has access to the customer's full signal profile, repayment
history, and scoring breakdown. It answers analyst questions without hallucinating:

- "Why is this customer flagged?" → cites specific signals and values
- "Is this likely to get worse?" → references trajectory and roll-forward probability
- "What's different about this customer vs similar ones who recovered?" → compares to
  archetype patterns from the portfolio
- "What would their score be if income recovers?" → describes which signals would change

#### 7.2.4 Portfolio Narrator

**When called:** After cluster analysis (Scenario 8), for each identified risk cluster.

**Input:** Cluster attributes (segment, region, industry), member count, deterioration rate,
exposure at risk, common signal pattern.

**Output:** A paragraph-level narrative suitable for a risk committee report.

### 7.3 LLM Cost & Performance Considerations

| Component | Model | Calls per run | Latency tolerance |
|---|---|---|---|
| Signal Synthesizer | Claude Sonnet | ~100–150 (watchlist customers) | 2–3s per customer |
| Exception Judge | Claude Sonnet | ~40–50 (exception events) | 2s per event |
| Analyst Chat | Claude Sonnet | On-demand | Real-time |
| Portfolio Narrator | Claude Sonnet | 5–10 (clusters) | 5s per cluster |

All LLM calls are **optional enhancements** — the system works fully without them (rules
+ ML provide scores, template reason codes, and bands). The LLM layer adds richness,
not correctness.

---

## 8. Project Structure

```
Early-Warning-Detection-System/
│
├── PLAN_AND_ARCHITECTURE.md       ← This document
├── requirements.txt               ← Python dependencies
├── app.py                         ← Streamlit entry point
│
├── data/
│   ├── sample/csv/                ← Clean input data (8 CSVs)
│   ├── sample/rules/              ← Signal definitions, score bands
│   ├── dirty/                     ← Dirty data for validation testing
│   └── _ground_truth/             ← Benchmark labels (not app input)
│
├── src/
│   ├── __init__.py
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── loader.py              ← CSV/Excel/JSON file reader
│   │   ├── validator.py           ← Schema checks, dedup, FK validation
│   │   └── schema.py              ← Table definitions, relationships, types
│   │
│   ├── features/
│   │   ├── __init__.py
│   │   ├── repayment.py           ← S1: RBDI computation
│   │   ├── cashflow.py            ← S2: CFSI computation
│   │   ├── exposure.py            ← S3: LPI computation
│   │   ├── exceptions.py          ← S4: Mitigation Score
│   │   ├── rollforward.py         ← S5: Roll-forward features
│   │   ├── sme.py                 ← S6: SME Stress Index
│   │   ├── completeness.py        ← S7: Data Completeness Score
│   │   └── portfolio.py           ← S8: Cluster features
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── trainer.py             ← LightGBM + LogReg training pipeline
│   │   ├── markov.py              ← Transition matrix estimation
│   │   ├── anomaly.py             ← Isolation Forest
│   │   ├── clustering.py          ← HDBSCAN for portfolio clusters
│   │   └── explainer.py           ← SHAP computation
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── signals.py             ← Evaluate 21 signals per customer
│   │   ├── scorer.py              ← Composite score + band assignment
│   │   └── overrides.py           ← Hard overrides + confidence cap
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py              ← Claude API wrapper
│   │   ├── synthesizer.py         ← Signal synthesis → risk narrative
│   │   ├── exception_judge.py     ← Contextual missed-payment evaluation
│   │   ├── chat.py                ← Analyst Q&A interface
│   │   └── portfolio_narrator.py  ← Cluster-level summaries
│   │
│   └── ui/
│       ├── __init__.py
│       ├── upload.py              ← Data upload + validation report page
│       ├── watchlist.py           ← Customer risk watchlist page
│       ├── customer_detail.py     ← Individual customer deep-dive
│       ├── portfolio_dashboard.py ← Portfolio-level heatmaps & clusters
│       ├── review.py              ← Analyst review + classification workflow
│       ├── simulator.py           ← What-if scenario simulator
│       └── chat_panel.py          ← AI chat assistant panel
│
└── tests/
    ├── test_ingestion.py
    ├── test_features.py
    ├── test_signals.py
    ├── test_scorer.py
    └── test_traps.py             ← Validates handling of built-in traps
```

---

## 9. Technology Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | ML ecosystem, Streamlit compatibility |
| Data processing | Pandas + DuckDB | Pandas for feature computation, DuckDB for fast SQL queries on CSVs |
| ML — Gradient boosting | LightGBM | Fast, handles missing values natively, monotonic constraints, GPU optional |
| ML — Baseline | scikit-learn LogisticRegression | Transparent scorecard for auditability |
| ML — Anomaly | scikit-learn IsolationForest | Catches unusual signal combinations |
| ML — Clustering | hdbscan | Density-based, no need to pre-specify k, handles noise |
| ML — Explainability | SHAP (shap library) | Per-prediction feature contributions for LightGBM |
| ML — Calibration | scikit-learn IsotonicRegression | Converts LightGBM scores to true probabilities |
| LLM | Claude API (anthropic SDK) | Signal synthesis, reason codes, analyst chat |
| Frontend | Streamlit | Rapid prototyping, interactive widgets, built-in charts |
| Charts | Plotly | Interactive heatmaps, time series, waterfall charts for SHAP |
| Storage | SQLite (via DuckDB) | Persist analyst reviews, session state |

### Requirements
```
streamlit>=1.38
pandas>=2.2
duckdb>=1.1
lightgbm>=4.5
scikit-learn>=1.5
shap>=0.46
hdbscan>=0.8
plotly>=5.24
anthropic>=0.40
openpyxl>=3.1
```

---

## 10. Build Phases (Hackathon Timeline)

### Phase 1: Data Foundation (0–1.5 hours)
- [ ] Data ingestion: CSV/Excel/JSON loader with format detection
- [ ] Schema validation: type checks, dedup, FK integrity, date parsing
- [ ] Data quality report: surface all 72 dirty-data defects
- [ ] Load into Pandas DataFrames with proper types and relationships

### Phase 2: Feature Engineering (1.5–3 hours)
- [ ] S1: Repayment features → RBDI per customer
- [ ] S2: Cash-flow features → CFSI per customer
- [ ] S3: Exposure features → LPI per customer
- [ ] S4: Exception engine (holiday calendar, salary delay, recovery speed)
- [ ] S5: Roll-forward features from delinquency snapshots
- [ ] S6: SME features with seasonality adjustment (YoY comparison)
- [ ] S7: Data Completeness Score
- [ ] Assemble full feature vector per customer

### Phase 3: Scoring Engine (3–4.5 hours)
- [ ] Rules engine: evaluate 21 signals per customer → raw score
- [ ] LightGBM: train on features → calibrated 90-day probability
- [ ] Logistic Regression: WoE baseline scorecard
- [ ] Markov chain: segment transition matrices → roll-forward probabilities
- [ ] Composite scorer: blend rules + ML → final score + band
- [ ] Apply overrides (OV01–OV03) + confidence cap
- [ ] SHAP: compute per-prediction explanations
- [ ] Validate against ground truth labels

### Phase 4: LLM Integration (4.5–6 hours)
- [ ] Claude API client with structured output parsing
- [ ] Signal Synthesizer: risk narratives for watchlist customers
- [ ] Exception Judge: contextual missed-payment assessment
- [ ] Reason Code Generator: SHAP → natural language
- [ ] Portfolio Narrator: cluster-level summaries

### Phase 5: Streamlit Application (6–8 hours)
- [ ] Data upload page with validation results
- [ ] Watchlist: sortable/filterable table with score, band, top signals
- [ ] Customer detail: repayment timeline, signal gauges, SHAP waterfall, LLM narrative
- [ ] Portfolio dashboard: heatmap by segment × band, cluster map
- [ ] Analyst review: inspect alert, record decision + reason
- [ ] What-if simulator: adjust income/DPD/exposure, see score change live
- [ ] AI Chat: conversational Q&A grounded in customer data

### Phase 6: Polish & Demo Scenarios (8–9 hours)
- [ ] Demo: Silent Stress case (income declining, still paying)
- [ ] Demo: Temporary exception (Bakrid holiday delay, mitigated)
- [ ] Demo: SME seasonal (normal dip, NOT flagged)
- [ ] Demo: Portfolio cluster (construction MSMEs, Pune)
- [ ] Demo: Thin-file customer (low confidence, not high risk)
- [ ] Performance tuning, edge case handling

---

## 11. Key Demo Scenarios

### Demo 1: Silent Stress Detection
**Customer:** INCOME_DECLINE archetype, still making payments on time.
**What the system shows:** RBDI is low (good repayment), but CFSI is high (salary dropped
22% over 3 months). Rules engine catches RS07 (sustained inflow decline). LLM synthesizer
flags the contradiction: "Customer is still meeting obligations but income has declined
significantly — this is a silent stress case requiring proactive RM outreach."

### Demo 2: Temporary Exception (Not a Real Risk)
**Customer:** ISOLATED_EXCEPTION archetype, one missed payment near Bakrid.
**What the system shows:** RS01 fires (1 late instalment), but RS05 mitigates it (isolated
delay + recovery + holiday calendar match). Exception Judge explains: "Payment due 30 Jun
fell during Bakrid (moon-sighting holiday, not pre-adjusted). Customer paid within 3 days.
18 prior on-time payments. Assessment: genuine exception."

### Demo 3: Seasonal SME (False Alarm Avoided)
**Customer:** SME_SEASONAL archetype, 3-month summer decline.
**What the system shows:** Raw month-over-month comparison would trigger RS08 (consecutive
declines). But YoY comparison shows this pattern repeats every year — it's normal
seasonality. System does NOT flag. LLM narrative: "Receipts declined May–July, consistent
with the tourism sector's annual seasonal pattern. Year-over-year comparison shows no
material change."

### Demo 4: Portfolio Risk Cluster
**Cluster:** CLU-SME-CONSTR-NCR — construction MSMEs in Delhi NCR/Pune.
**What the system shows:** HDBSCAN identifies a cluster of ~10 SMEs showing correlated
cash-flow stress (declining receipts, rising WC utilisation) starting Mar 2026. Portfolio
Narrator: "Emerging concentration risk in the Delhi NCR/Pune construction sector — 10 MSMEs
showing coordinated cash-flow deterioration since March 2026."

### Demo 5: Thin-File Customer (Low Confidence, Not High Risk)
**Customer:** THIN_FILE archetype, 3 months of history, bureau no-hit.
**What the system shows:** DQ01 (insufficient history), DQ03 (bureau no-hit) fire as
confidence flags. DCS = 28 → confidence = Low. No risk signals triggered. Band: "Low —
Insufficient Evidence". System does NOT classify as high risk.

---

## 12. Evaluation Criteria Alignment

| Evaluation Criterion | How We Address It |
|---|---|
| "Analyse multiple indicators" | 21 signals across 8 scenarios, covering repayment, cash flow, exposure, exceptions, delinquency, SME, data quality, portfolio |
| "Recognise changes in behaviour" | Trend features (slopes, 3m vs 6m deltas), baseline shift z-scores, changepoint detection |
| "Distinguish temporary from sustained" | Exception engine (S4) with guardrails; Recurrence Flag gates; LLM Exception Judge |
| "Explainable assessment" | SHAP contributions + LLM-generated reason codes + triggered signal list |
| "Individual + portfolio monitoring" | Customer-level scoring + HDBSCAN portfolio clustering |
| "Actual application behaviour" | Streamlit app with live data ingestion, scoring, filtering, drill-down, analyst review |
| "Innovation beyond mandatory scenarios" | LLM signal synthesis, anomaly detection (Isolation Forest), what-if simulator, conversational AI assistant |
