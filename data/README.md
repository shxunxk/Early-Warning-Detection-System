# Synthetic EWS dataset

Fully synthetic loan-portfolio data for the Early Warning System challenge (problem
statement 4). No real customers, PII or bank data. Setting: a UAE bank, amounts in AED.

- **As-of date:** 2026-09-30. All "last N months" logic should use this date, not today.
- **History window:** 24 months, Oct 2024 to Sep 2026.
- **Size (default):** 600 customers (420 individuals, 180 SMEs), ~1,040 facilities,
  ~19k repayment rows, ~19.5k facility-month snapshots, ~13.7k customer-month snapshots.
- **Regenerate:** `uv run python datagen/generate.py [--customers N] [--seed S]`
- **Verify:** `uv run python datagen/checks.py` (41 consistency checks; clean set passes all)

## Second portfolio: Northern Emirates Commercial Bank

`data/northern_sme/` has the same schema, generated with
`uv run python datagen/generate.py --profile northern_sme --out data/northern_sme`
(profile in `datagen/config.py → PROFILES`; summary in `data/northern_sme/PROFILE.md`).

| | Gulf Bank (default) | Northern Emirates |
|---|---|---|
| Customers / SME share | 600 / 30% | 1,200 / 45% |
| Footprint | Dubai, Abu Dhabi led | Sharjah, Ajman, RAK led |
| Median salary | ~AED 16k | ~AED 12k (mid-market) |
| Customer IDs | `CIF1xxxxx` | `CIF5xxxxx` |
| NPL by value | 0.2% | 5.6% (retail 3.4%, SME 5.9%) |
| Extra archetypes | — | `LEGACY_NPL`, `SME_LEGACY_NPL`: defaulted 5–20 months before as-of, token payments only; retail defaults are recent (median ~210 DPD, i.e. before typical write-off), SME workouts older (median ~330 DPD) |
| Clusters | Construction (Sharjah/Ajman), hospitality staff (Dubai) | Logistics (Sharjah/RAK), retail-trade staff (Sharjah/Ajman), restaurants (Ajman/Sharjah) |

## Layout

```
data/
  sample/                   clean input set (load this into the app)
    csv/  json/  excel/     same 8 tables in each format (Excel = one sheet per table)
    rules/                  risk signal definitions (JSON, CSV, Excel)
  dirty/                    same tables with 72 injected defects, for validation testing
    _injected_issues.csv    manifest of every defect (table, key, column, type)
  _ground_truth/            generator intent per customer. NOT an app input.
```

## Relationships

```
customers (customer_id)
 ├── facilities (facility_id, customer_id)
 │     ├── repayments   (facility_id)            one row per instalment due in window
 │     └── delinquency  (facility_id, snapshot)  one row per facility per month-end
 ├── exposure    (customer_id, snapshot_date)    on-us totals + credit bureau view
 ├── financials  (customer_id, month)            current/operating account aggregates
 └── exception_evidence (customer_id, facility_id)
bank_holidays                                     reference calendar
```

## Tables

### customers
| column | notes |
|---|---|
| customer_id | `CIF1xxxxx` |
| customer_name | synthetic |
| customer_type | `INDIVIDUAL` / `SME` |
| date_of_birth | individuals only |
| employment_type | `SALARIED`, `SELF_EMPLOYED`, `BUSINESS` |
| employment_or_business_category | employer sector (individuals) or industry (SMEs) |
| customer_segment | Mass / Mass Affluent / Affluent; Micro / Small / Medium for SMEs |
| portfolio | Consumer Banking / Business Banking |
| emirate | region |
| customer_since | onboarding date; recent for thin-file customers |
| onboarding_risk_grade | A (best) to E at approval time |
| salary_transfer_flag | `Y` if salary is credited to this bank. If `N`, there is no salary in `financials`, and that is **not** an income drop |
| declared_monthly_income / declared_annual_turnover | as declared at onboarding (slightly optimistic) |
| years_in_business, legal_form | SMEs only |
| relationship_manager_id | RMs for SME and affluent customers |

### facilities
`product_code`: PF Personal Finance, AF Auto Finance, HF Home Finance, CC Credit Card,
TL Business Term Loan, WC Working Capital Facility, EF Equipment Finance.
`facility_type` is `INSTALMENT` (fixed EMI) or `REVOLVING` (CC/WC; `sanctioned_amount` is the
limit). Other columns: `secured_flag`, `currency`, `disbursement_date`, `tenure_months`,
`maturity_date`, `interest_rate_pct` (annual), `instalment_amount`, `due_day`,
`payment_channel` (DIRECT_DEBIT / STANDING_INSTRUCTION / MANUAL_TRANSFER / CHEQUE),
`outstanding_balance` (as of the as-of date), `facility_status` (ACTIVE / CLOSED), `closure_date`.

### repayments
One row per instalment (revolving: the monthly minimum due) with a due date in the window.
Payments are allocated **FIFO to the oldest unpaid instalment**, as core banking systems do.
A catch-up payment therefore settles old arrears first, and a customer who pays exactly one
instalment a month while one behind stays stuck in arrears.

| column | notes |
|---|---|
| due_date | rolled past weekends and fixed holidays; **not** past lunar holidays |
| amount_due / amount_paid | amount_paid is what has been allocated so far |
| payment_date | date the instalment was fully settled (or last partial payment) |
| days_late | settle date minus due date; for open instalments, as-of minus due date |
| repayment_status | `PAID_ON_TIME`, `PAID_LATE`, `PARTIALLY_PAID`, `UNPAID` |

`PAID_LATE` has **no grace period** applied. 1–4 day delays on direct debits around Eid are
genuine bank-side delays. Use `bank_holidays` and the `grace_days` rule parameter to excuse them.

### delinquency (facility × month-end)
`days_past_due` (from the oldest unpaid instalment; an instalment due *on* the snapshot date is
not overdue), `dpd_bucket` (Current, 1-30, 31-60, 61-90, 91-180, 180+), `overdue_amount`,
`overdue_instalments`, `max_dpd_12m`, `times_30plus_12m`, `ever_30plus_flag` /
`ever_60plus_flag` / `ever_90plus_flag` (within the window), `ifrs9_stage` (1/2/3), `npl_flag`.

### exposure (customer × month-end)
- **On-us:** `onus_active_facilities`, `onus_sanctioned_total`, `onus_outstanding_total`,
  `onus_monthly_obligation` (EMIs + 5% of card limits + WC interest, per the UAE debt-burden
  convention), `revolving_limit_total`, `revolving_balance_total`, `revolving_utilisation_pct`
  (can exceed 100% when over limit), `new_onus_facilities_3m`.
- **Bureau (other lenders):** `bureau_external_facilities`, `_outstanding`,
  `_monthly_obligation`, `_card_limit`, `_card_utilisation_pct`, `bureau_enquiries_3m`,
  `bureau_external_max_dpd`, `bureau_score` (300–900). All bureau columns are blank for
  **no-hit** customers (some thin-file customers).
- Debt burden ratio = (onus + bureau monthly obligation) / monthly income. The app computes it.

### financials (customer × month)
`salary_credit` (blank if salary is not with this bank), `business_receipts` (SMEs),
`other_inflows` (includes loan disbursements credited), `total_inflows`, `total_outflows`,
`net_cash_flow`, `opening_balance`, `closing_balance`, `average_balance`, `minimum_balance`,
`inflow_txn_count`, `outflow_txn_count`, `returned_payments_count` (failed direct debits and
bounced cheques). Some customer-months are **missing on purpose** (feed gaps, scenario 7).

### exception_evidence
Recorded explanations for irregular payments: `exception_type` (SALARY_DELAY,
PROCESSING_DELAY, TEMPORARY_HARDSHIP, HOLIDAY_PERIOD, CUSTOMER_PAYMENT_DELAY),
`evidence_source`, `verified_flag`. Some deteriorating customers have **unverified** excuses,
so evidence alone should not clear a warning.

### bank_holidays
`holiday_type` FIXED (schedules already adjusted) or LUNAR (announced late; not adjusted).
Lunar dates are approximate.

### rules/risk_signal_definitions.json
The configurable rules input: `parameters` (as-of date, grace days, minimum history),
21 `signals` (metric, lookback, operator, threshold, weight, severity), `score_bands`
(0–100, **higher = riskier**; Low < 30, Medium < 55, High < 75, Critical ≥ 75) and `overrides`.
Data-quality signals (DQ01–DQ03) have weight 0: missing data lowers confidence, never adds risk.

## Behaviour profiles (in `_ground_truth/customer_labels.csv`)

| archetype | count | scenarios | what it looks like |
|---|---|---|---|
| STABLE | ~200 | — | regular payer; rare 1–2 day slips |
| ISOLATED_EXCEPTION | ~40 | S4 | one delayed/missed instalment (salary delay, processing delay, hardship, Eid travel), then recovered |
| CHRONIC_LATE_PAYER | ~20 | S1 | 2–9 days late most months but always pays in full |
| REPAYMENT_DETERIORATION | ~23 | S1, S5 | lateness grows, then partials and misses |
| INCOME_DECLINE | ~44 | S2 | pay cut, job loss or gradual decline; repayments lag the income drop |
| OVER_LEVERAGED | ~25 | S3 | new on-us and off-us credit, rising utilisation and debt burden |
| ROLL_FORWARD | ~12 | S5 | current → 1-30 → 31-60/61-90 DPD |
| CURED | ~15 | S5 | 2–3 month delinquency episode early on, fully cured since |
| THIN_FILE | ~19 | S7 | 2–5 months of history, possibly bureau no-hit |
| SALARY_ELSEWHERE | ~20 | S7 | no salary visibility, sparse account data with feed gaps |
| SME_STABLE | ~92 | — | stable receipts with normal seasonality |
| SME_SEASONAL | ~23 | S6 | summer receipts fall 3+ months in a row, then recover |
| SME_CASHFLOW_STRESS | ~27 | S2, S6 | receipts falling for months, WC limit maxed, bounced cheques |
| SME_OVER_LEVERAGED | ~14 | S3, S6 | additional term/equipment debt |
| SME_REPAYMENT_DETERIORATION | ~11 | S1, S5, S6 | business repayments deteriorating |
| SME_THIN_FILE | ~13 | S7 | new business, little history |

**Emerging risk clusters (S8):** `CLU-SME-CONSTR-NORTH`, construction SMEs in Sharjah/Ajman
going into cash-flow stress in Mar–May 2026; `CLU-RET-HOSP-DXB`, hospitality employees in
Dubai with pay cuts and layoffs from Apr–Jun 2026.

`expected_risk_band` and `expected_data_sufficiency` are the generator's intended outcome,
adjusted for actual DPD at the as-of date. Treat them as a benchmark, not exact answers.

## Built-in traps
- Seasonal SMEs: three consecutive monthly declines that are normal. Compare year-on-year.
- Lunar-holiday direct debits are 1–4 days late. Excuse them via the holiday calendar.
- FIFO allocation keeps some customers stuck at ~30 DPD while paying every month.
- Unverified "salary delay" claims from customers who are really deteriorating.
- Off-us (bureau) delinquency often appears 1–2 months before on-us delinquency.
- Salary not domiciled ≠ income stopped; missing months ≠ zero inflow.
- Recent job losses show **no arrears yet**: the salary signal is the early warning.
