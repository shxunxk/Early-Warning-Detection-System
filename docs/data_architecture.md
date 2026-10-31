# Sentinel EWS: data architecture

How data enters, is stored, and leaves the Early Warning System. The physical model is in
[`sql/schema.sql`](../sql/schema.sql) (DuckDB). The column-level dictionary and generator
details are in [`data/README.md`](../data/README.md).

## 1. Layers

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        GEN["datagen/<br/>synthetic generator"]
        ORG["Organiser sample<br/>+ hidden tests"]
    end
    subgraph IN["1 · Input (8 tables)<br/>CSV / Excel / JSON"]
        RAW[(customers · facilities<br/>repayments · delinquency<br/>exposure · financials<br/>exception_evidence · bank_holidays)]
        RULES[(rules/<br/>risk_signal_definitions)]
    end
    subgraph CORE["2 · Core"]
        VAL["Validate +<br/>link records"]
        FEAT["features.py<br/>8 scenario modules"]
        ENG["engine.py<br/>score · tier · reasons"]
    end
    subgraph OUT["3 · Output (app-owned)"]
        RUN[(scoring_runs)]
        VI[(validation_issues)]
        CS[(customer_scores)]
        AL[(alerts)]
        AD[(analyst_decisions<br/>append-only)]
    end
    GEN --> RAW
    ORG --> RAW
    RAW --> VAL --> FEAT --> ENG
    RULES --> ENG
    VAL --> VI
    ENG --> CS --> AL
    AL --> AD
    RUN -.-> VI & CS & AL
```

## 2. Input model (entity relationships)

```mermaid
erDiagram
    CUSTOMERS ||--o{ FACILITIES : holds
    CUSTOMERS ||--o{ EXPOSURE : "monthly snapshot"
    CUSTOMERS ||--o{ FINANCIALS : "monthly account"
    CUSTOMERS ||--o{ EXCEPTION_EVIDENCE : explains
    FACILITIES ||--o{ REPAYMENTS : "instalments due"
    FACILITIES ||--o{ DELINQUENCY : "month-end DPD"
    FACILITIES ||--o{ EXCEPTION_EVIDENCE : "relates to"
    BANK_HOLIDAYS ||..o{ REPAYMENTS : "excuses delays on"

    CUSTOMERS {
        varchar customer_id PK
        varchar customer_type "INDIVIDUAL | SME"
        varchar employment_or_business_category
        varchar customer_segment
        varchar portfolio
        varchar region
        date customer_since
        char onboarding_risk_grade "A-E"
        char salary_transfer_flag "Y | N"
        decimal declared_monthly_income
        decimal declared_annual_turnover
    }
    FACILITIES {
        varchar facility_id PK
        varchar customer_id FK
        varchar product_code "PF AF HF CC TL WC EF"
        varchar facility_type "INSTALMENT | REVOLVING"
        decimal sanctioned_amount "limit if revolving"
        date disbursement_date
        int tenure_months
        decimal instalment_amount
        varchar payment_channel
        decimal outstanding_balance
        varchar facility_status
    }
    REPAYMENTS {
        varchar repayment_id PK
        varchar facility_id FK
        varchar customer_id FK
        date due_date
        decimal amount_due
        date payment_date
        decimal amount_paid
        int days_late
        varchar repayment_status
    }
    DELINQUENCY {
        varchar facility_id PK, FK
        date snapshot_date PK
        int days_past_due
        varchar dpd_bucket
        decimal overdue_amount
        int max_dpd_12m
        int times_30plus_12m
        int ifrs9_stage
    }
    EXPOSURE {
        varchar customer_id PK, FK
        date snapshot_date PK
        decimal onus_outstanding_total
        decimal onus_monthly_obligation
        decimal revolving_utilisation_pct
        int new_onus_facilities_3m
        decimal bureau_external_monthly_obligation
        int bureau_enquiries_3m
        int bureau_external_max_dpd
        int bureau_score
    }
    FINANCIALS {
        varchar customer_id PK, FK
        varchar month PK
        decimal salary_credit "NULL if salary elsewhere"
        decimal business_receipts "SME"
        decimal total_inflows
        decimal total_outflows
        decimal average_balance
        int returned_payments_count
    }
    EXCEPTION_EVIDENCE {
        varchar evidence_id PK
        varchar customer_id FK
        varchar facility_id FK
        date event_date
        varchar exception_type
        char verified_flag
    }
    BANK_HOLIDAYS {
        date holiday_date PK
        varchar holiday_type "FIXED | LUNAR"
    }
```

**Grain:** `repayments` is per instalment, `delinquency` is per facility per month-end, and
`exposure` / `financials` are per customer per month. Everything joins on `customer_id`;
time joins use the month-end date (`snapshot_date` = `month_end_date`).

## 3. Output model (app-owned)

Required capability 5 says the original warning score and evidence must survive analyst
reclassification. So alerts are written once and never updated, and decisions are an
append-only log.

```mermaid
erDiagram
    SCORING_RUNS ||--o{ VALIDATION_ISSUES : reports
    SCORING_RUNS ||--o{ CUSTOMER_SCORES : produces
    SCORING_RUNS ||--o{ ALERTS : raises
    CUSTOMER_SCORES ||--o| ALERTS : "becomes (tier >= Watch)"
    ALERTS ||--o{ ANALYST_DECISIONS : "reviewed by"

    SCORING_RUNS {
        varchar run_id PK
        date as_of_date
        varchar rules_version
        varchar model_version
    }
    VALIDATION_ISSUES {
        varchar run_id FK
        varchar table_name
        varchar record_key
        varchar issue_type
        varchar severity "ERROR | WARNING"
    }
    CUSTOMER_SCORES {
        varchar run_id PK, FK
        varchar customer_id PK, FK
        double risk_score "0-100 higher = riskier"
        varchar tier "Green Watch Amber Red"
        varchar confidence "High Medium Low"
        varchar trajectory
        double rbdi
        double cfsi
        double lpi
        double roll_risk
        double sme_index
        double dcs
        double ml_probability_90d
        varchar evidence_json
    }
    ALERTS {
        varchar alert_id PK
        varchar run_id FK
        varchar customer_id FK
        double original_score "frozen"
        varchar original_tier "frozen"
        varchar evidence_json "frozen"
    }
    ANALYST_DECISIONS {
        varchar decision_id PK
        varchar alert_id FK
        varchar decision
        varchar reason
        varchar analyst_id
        timestamp decided_at
    }
```

## 4. Source tables per scenario module

How the Sentinel feature spec (`ews_final_features.txt`) maps onto the tables.

| Module | Index | Primary tables | Key columns |
|---|---|---|---|
| S1 Repayment deterioration | RBDI | repayments, delinquency | days_late, repayment_status, amount_paid / amount_due, days_past_due |
| S2 Income & cash flow | CFSI | financials | salary_credit, total_inflows, average_balance, net_cash_flow |
| S3 Obligations & exposure | LPI | exposure, financials, customers | revolving_utilisation_pct, onus + bureau obligation, new_onus_facilities_3m, bureau_enquiries_3m |
| S4 Temporary exceptions | Mitigation | repayments, bank_holidays, exception_evidence, financials | due_date vs holiday, salary credit timing, verified_flag |
| S5 Roll-forward | Roll Risk | delinquency | dpd_bucket month-to-month transitions |
| S6 SME stress | SME Index | financials, exposure | business_receipts (YoY / deseasonalised), returned_payments_count, WC utilisation |
| S7 Missing history | DCS | all | months on book, missing months, bureau no-hit (bureau_* NULL) |
| S8 Clusters | Cluster Alert | customer_scores + customers | segment = product × industry × region × segment |

## 5. Gaps against the Sentinel minimum schema

| Sentinel expects | What we have | Proposal |
|---|---|---|
| `transactions` (row-level) | monthly aggregates in `financials` | Enough for Level 1. Features needing daily data (salary credit day, max gap, active days) are dropped or approximated. Raw transactions can be added to the generator later if wanted. |
| `enquiries` (dated) | `exposure.bureau_enquiries_3m` | Use the rolling count. |
| `sme_extra.gst_turnover`, `supplier_payments` | `declared_annual_turnover`, `total_outflows` | F6.7 compares declared turnover with receipts; F6.6 uses WC utilisation. GST returns could be added as a synthetic table. |
| `od_utilisation` | WC facility in `exposure.revolving_utilisation_pct` | Covered. |
| `labels.deteriorated_30/60/90` | `_ground_truth/customer_labels.csv` (as-of only) | For LightGBM training, derive point-in-time labels from the 24 monthly snapshots: features at month t, label = DPD ≥ 30 within t+1..t+3. |
| branch, channel, vintage | region, payment_channel, disbursement_date | Vintage derived from disbursement_date. |
| Parquet storage | CSV / Excel / JSON | Ingest any of the three formats, persist to DuckDB (or Parquet) inside the app. |
| Currency in ₹, Republic Day | INR, Indian holiday calendar | Matches; currency and holidays come from the `currency` rule parameter and the `bank_holidays` table, not code. |

## 6. Conventions

- **As-of date** comes from `rules.parameters.as_of_date` (2026-09-30), never `today()`.
- **Missing ≠ zero.** NULL means unknown; DQ signals lower confidence and never add risk.
- **Score direction:** 0–100, higher is riskier.
- **Grace and holidays:** `PAID_LATE` has no grace applied. The engine applies
  `grace_days` and excuses LUNAR holiday shifts.
- **IDs:** `CIF…` customers, `<product><7 digits>` facilities, `RP…` repayments, `EV…` evidence.
