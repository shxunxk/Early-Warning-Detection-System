-- Sentinel EWS: physical data model (DuckDB dialect; also valid PostgreSQL apart from
-- the read_csv_auto loaders at the bottom).
--
-- Layer 1 (INPUT)  : the 8 tables the generator produces / the organiser will supply.
-- Layer 2 (RULES)  : configurable risk signal definitions.
-- Layer 3 (OUTPUT) : what the app writes. Alerts are immutable snapshots; analyst
--                    decisions are append-only, so the original score and evidence are
--                    always preserved (required capability 5).

-- =====================================================================================
-- 1. INPUT
-- =====================================================================================

CREATE TABLE customers (
    customer_id                     VARCHAR PRIMARY KEY,          -- CIF1xxxxx
    customer_name                   VARCHAR,
    customer_type                   VARCHAR NOT NULL CHECK (customer_type IN ('INDIVIDUAL', 'SME')),
    date_of_birth                   DATE,                         -- individuals only
    employment_type                 VARCHAR CHECK (employment_type IN ('SALARIED', 'SELF_EMPLOYED', 'BUSINESS')),
    employment_or_business_category VARCHAR,                      -- employer sector / SME industry
    customer_segment                VARCHAR,                      -- Mass..Affluent / Micro..Medium
    portfolio                       VARCHAR,                      -- Consumer / Business Banking
    emirate                         VARCHAR,
    customer_since                  DATE NOT NULL,
    onboarding_risk_grade           CHAR(1) CHECK (onboarding_risk_grade IN ('A', 'B', 'C', 'D', 'E')),
    salary_transfer_flag            CHAR(1) CHECK (salary_transfer_flag IN ('Y', 'N')),
    declared_monthly_income         DECIMAL(14, 2),
    declared_annual_turnover        DECIMAL(16, 2),
    years_in_business               INTEGER,
    legal_form                      VARCHAR,
    relationship_manager_id         VARCHAR
);

CREATE TABLE facilities (
    facility_id          VARCHAR PRIMARY KEY,                     -- <product_code><7 digits>
    customer_id          VARCHAR NOT NULL REFERENCES customers (customer_id),
    product_code         VARCHAR NOT NULL CHECK (product_code IN ('PF', 'AF', 'HF', 'CC', 'TL', 'WC', 'EF')),
    product_name         VARCHAR,
    portfolio            VARCHAR,                                 -- Retail Secured/Unsecured, SME Lending
    facility_type        VARCHAR NOT NULL CHECK (facility_type IN ('INSTALMENT', 'REVOLVING')),
    secured_flag         CHAR(1),
    currency             CHAR(3) NOT NULL DEFAULT 'AED',
    sanctioned_amount    DECIMAL(16, 2) NOT NULL CHECK (sanctioned_amount > 0),  -- limit if revolving
    disbursement_date    DATE NOT NULL,
    tenure_months        INTEGER,                                 -- NULL for revolving
    maturity_date        DATE,
    interest_rate_pct    DECIMAL(6, 2),
    instalment_amount    DECIMAL(14, 2),                          -- NULL for revolving
    repayment_frequency  VARCHAR DEFAULT 'MONTHLY',
    due_day              INTEGER CHECK (due_day BETWEEN 1 AND 31),
    payment_channel      VARCHAR CHECK (payment_channel IN ('DIRECT_DEBIT', 'STANDING_INSTRUCTION', 'MANUAL_TRANSFER', 'CHEQUE')),
    outstanding_balance  DECIMAL(16, 2),
    facility_status      VARCHAR CHECK (facility_status IN ('ACTIVE', 'CLOSED')),
    closure_date         DATE
);

-- One row per instalment (revolving: monthly minimum due). Payments allocated FIFO.
CREATE TABLE repayments (
    repayment_id      VARCHAR PRIMARY KEY,
    facility_id       VARCHAR NOT NULL REFERENCES facilities (facility_id),
    customer_id       VARCHAR NOT NULL REFERENCES customers (customer_id),  -- denormalised
    instalment_no     INTEGER,
    due_date          DATE NOT NULL,
    amount_due        DECIMAL(14, 2) NOT NULL CHECK (amount_due > 0),
    payment_date      DATE,                                        -- settle / last partial date
    amount_paid       DECIMAL(14, 2) NOT NULL DEFAULT 0 CHECK (amount_paid >= 0),
    days_late         INTEGER,
    repayment_status  VARCHAR NOT NULL CHECK (repayment_status IN ('PAID_ON_TIME', 'PAID_LATE', 'PARTIALLY_PAID', 'UNPAID')),
    payment_channel   VARCHAR
);

-- Facility x month-end.
CREATE TABLE delinquency (
    snapshot_date        DATE NOT NULL,
    facility_id          VARCHAR NOT NULL REFERENCES facilities (facility_id),
    customer_id          VARCHAR NOT NULL REFERENCES customers (customer_id),
    days_past_due        INTEGER NOT NULL CHECK (days_past_due >= 0),
    dpd_bucket           VARCHAR CHECK (dpd_bucket IN ('Current', '1-30', '31-60', '61-90', '91-180', '180+')),
    overdue_amount       DECIMAL(14, 2) NOT NULL CHECK (overdue_amount >= 0),
    overdue_instalments  INTEGER,
    max_dpd_12m          INTEGER,
    times_30plus_12m     INTEGER,
    ever_30plus_flag     CHAR(1),
    ever_60plus_flag     CHAR(1),
    ever_90plus_flag     CHAR(1),
    ifrs9_stage          INTEGER CHECK (ifrs9_stage IN (1, 2, 3)),
    npl_flag             CHAR(1),
    PRIMARY KEY (facility_id, snapshot_date)
);

-- Customer x month-end: on-us aggregates + credit bureau (off-us) view.
CREATE TABLE exposure (
    snapshot_date                         DATE NOT NULL,
    customer_id                           VARCHAR NOT NULL REFERENCES customers (customer_id),
    onus_active_facilities                INTEGER,
    onus_sanctioned_total                 DECIMAL(16, 2),
    onus_outstanding_total                DECIMAL(16, 2),
    onus_monthly_obligation               DECIMAL(14, 2),
    revolving_limit_total                 DECIMAL(16, 2),
    revolving_balance_total               DECIMAL(16, 2),
    revolving_utilisation_pct             DECIMAL(6, 1),         -- may exceed 100 (over limit)
    new_onus_facilities_3m                INTEGER,
    bureau_external_facilities            INTEGER,               -- bureau_* all NULL = no-hit
    bureau_external_outstanding           DECIMAL(16, 2),
    bureau_external_monthly_obligation    DECIMAL(14, 2),
    bureau_external_card_limit            DECIMAL(14, 2),
    bureau_external_card_utilisation_pct  DECIMAL(6, 1),
    bureau_enquiries_3m                   INTEGER,
    bureau_external_max_dpd               INTEGER,
    bureau_score                          INTEGER CHECK (bureau_score BETWEEN 300 AND 900),
    PRIMARY KEY (customer_id, snapshot_date)
);

-- Customer x month: operating/current account aggregates. Gaps are intentional (feed gaps).
CREATE TABLE financials (
    month                    VARCHAR NOT NULL,                     -- YYYY-MM
    month_end_date           DATE NOT NULL,
    customer_id              VARCHAR NOT NULL REFERENCES customers (customer_id),
    salary_credit            DECIMAL(14, 2),                       -- NULL if salary not with bank
    business_receipts        DECIMAL(16, 2),                       -- SMEs only
    other_inflows            DECIMAL(16, 2),
    total_inflows            DECIMAL(16, 2) NOT NULL,
    total_outflows           DECIMAL(16, 2) NOT NULL,
    net_cash_flow            DECIMAL(16, 2),
    opening_balance          DECIMAL(16, 2),
    closing_balance          DECIMAL(16, 2),
    average_balance          DECIMAL(16, 2),
    minimum_balance          DECIMAL(16, 2),
    inflow_txn_count         INTEGER,
    outflow_txn_count        INTEGER,
    returned_payments_count  INTEGER,
    PRIMARY KEY (customer_id, month)
);

CREATE TABLE exception_evidence (
    evidence_id      VARCHAR PRIMARY KEY,
    customer_id      VARCHAR NOT NULL REFERENCES customers (customer_id),
    facility_id      VARCHAR REFERENCES facilities (facility_id),
    event_date       DATE NOT NULL,
    exception_type   VARCHAR,     -- SALARY_DELAY, PROCESSING_DELAY, TEMPORARY_HARDSHIP, ...
    description      VARCHAR,
    evidence_source  VARCHAR,     -- EMPLOYER_LETTER, BANK_OPERATIONS_LOG, CUSTOMER_CALL, ...
    verified_flag    CHAR(1) CHECK (verified_flag IN ('Y', 'N')),
    recorded_date    DATE,
    recorded_by      VARCHAR
);

CREATE TABLE bank_holidays (
    holiday_date       DATE PRIMARY KEY,
    holiday_name       VARCHAR,
    holiday_type       VARCHAR CHECK (holiday_type IN ('FIXED', 'LUNAR')),
    schedule_adjusted  CHAR(1)    -- Y: due dates already rolled past it
);

-- =====================================================================================
-- 2. RULES (loaded from rules/risk_signal_definitions.json)
-- =====================================================================================

CREATE TABLE risk_signals (
    signal_id        VARCHAR PRIMARY KEY,   -- RS01.., DQ01..
    name             VARCHAR NOT NULL,
    category         VARCHAR,               -- REPAYMENT, CASH_FLOW, EXPOSURE, BUREAU, MITIGANT, DATA_QUALITY
    scenario         VARCHAR,               -- S1..S8
    metric           VARCHAR NOT NULL,
    lookback_months  INTEGER,
    operator         VARCHAR CHECK (operator IN ('>=', '>', '<=', '<', '==')),
    threshold        DOUBLE,
    weight           DOUBLE,                -- negative = mitigant, 0 = confidence flag only
    severity         VARCHAR,
    description      VARCHAR
);

CREATE TABLE score_bands (
    band                VARCHAR PRIMARY KEY,
    min_score           INTEGER,
    max_score           INTEGER,
    monitoring_status   VARCHAR,
    recommended_action  VARCHAR
);

-- =====================================================================================
-- 3. OUTPUT (proposed; written by the app)
-- =====================================================================================

CREATE TABLE scoring_runs (
    run_id         VARCHAR PRIMARY KEY,
    as_of_date     DATE NOT NULL,
    rules_version  VARCHAR,
    model_version  VARCHAR,
    started_at     TIMESTAMP DEFAULT current_timestamp,
    source_files   VARCHAR              -- JSON list of ingested files + checksums
);

CREATE TABLE validation_issues (
    run_id       VARCHAR REFERENCES scoring_runs (run_id),
    table_name   VARCHAR,
    record_key   VARCHAR,
    column_name  VARCHAR,
    issue_type   VARCHAR,               -- ORPHAN_REFERENCE, INVALID_DATE, ...
    severity     VARCHAR,               -- ERROR (row rejected) / WARNING (row kept)
    message      VARCHAR
);

-- Immutable once written: one row per customer per run.
CREATE TABLE customer_scores (
    run_id              VARCHAR REFERENCES scoring_runs (run_id),
    customer_id         VARCHAR REFERENCES customers (customer_id),
    risk_score          DOUBLE,          -- 0-100, higher = riskier
    tier                VARCHAR,         -- Green / Watch / Amber / Red
    confidence          VARCHAR,         -- High / Medium / Low (Data Completeness Score)
    trajectory          VARCHAR,         -- Improving / Stable / Deteriorating / Recovering
    rbdi DOUBLE, cfsi DOUBLE, lpi DOUBLE, roll_risk DOUBLE, sme_index DOUBLE, dcs DOUBLE,
    ml_probability_90d  DOUBLE,
    mitigated_flag      BOOLEAN,
    cluster_id          VARCHAR,
    evidence_json       VARCHAR,         -- fired signals, values, thresholds, reason codes
    PRIMARY KEY (run_id, customer_id)
);

CREATE TABLE alerts (
    alert_id        VARCHAR PRIMARY KEY,
    run_id          VARCHAR REFERENCES scoring_runs (run_id),
    customer_id     VARCHAR REFERENCES customers (customer_id),
    created_at      TIMESTAMP DEFAULT current_timestamp,
    original_score  DOUBLE NOT NULL,     -- frozen copy, never updated
    original_tier   VARCHAR NOT NULL,
    evidence_json   VARCHAR NOT NULL,
    current_status  VARCHAR              -- latest decision (derived, convenience only)
);

-- Append-only audit trail of analyst decisions.
CREATE TABLE analyst_decisions (
    decision_id  VARCHAR PRIMARY KEY,
    alert_id     VARCHAR NOT NULL REFERENCES alerts (alert_id),
    decision     VARCHAR NOT NULL CHECK (decision IN ('CONTINUE_MONITORING', 'REQUIRES_INVESTIGATION', 'FALSE_POSITIVE', 'ESCALATE_FOR_REVIEW')),
    reason       VARCHAR NOT NULL,
    analyst_id   VARCHAR NOT NULL,
    decided_at   TIMESTAMP DEFAULT current_timestamp
);

-- =====================================================================================
-- Loading the sample (DuckDB)
-- =====================================================================================
-- INSERT INTO customers SELECT * FROM read_csv_auto('data/sample/csv/customers.csv');
-- ... same for facilities, repayments, delinquency, exposure, financials,
--     exception_evidence, bank_holidays.
