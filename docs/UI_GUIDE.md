# Sentinel EWS: user interface guide

Every element of the Streamlit app: what it shows, where the value comes from, and how to
use it. For the calculations behind the numbers see [IMPLEMENTATION.md](IMPLEMENTATION.md).

Start the app with `uv run streamlit run app.py` and open http://localhost:8501.

---

## Contents
1. [Layout and conventions](#1-layout-and-conventions)
2. [Sidebar](#2-sidebar)
3. [Upload & Validate](#3-upload--validate)
4. [Portfolio](#4-portfolio)
5. [Watchlist](#5-watchlist)
6. [Customer](#6-customer)
7. [Review](#7-review)
8. [Rules](#8-rules)
9. [Common interactions](#9-common-interactions)
10. [Suggested demo walkthrough](#10-suggested-demo-walkthrough)
11. [Glossary](#11-glossary)
12. [Known UI quirks](#12-known-ui-quirks)

---

## 1. Layout and conventions

- **Two areas:** a fixed **sidebar** on the left (navigation and context), and the **main
  area**, which shows one page at a time.
- **Score direction:** every score is 0–100 and **higher means riskier**.
- **Tier colours**, used in badges and charts:

| Tier | Colour | Score | Meaning |
|---|---|---|---|
| Green | 🟩 dark green | 0–24 | No concern; routine monitoring |
| Watch | 🟨 yellow | 25–44 | Watchlist; RM review, soft-touch contact |
| Amber | 🟧 orange | 45–69 | Action required; contact the customer |
| Red | 🟥 red | 70–100, or a hard trigger | Escalate to credit committee / collections |

- **Money** is in AED. **Dates** are ISO (YYYY-MM-DD).
- **The as-of date** (2026-09-30) is the "today" of the analysis. Every "last N months" window counts back from it.
- **Tables** can be sorted by clicking a column header, searched with the 🔍 icon,
  expanded with ⛶, and downloaded as CSV with ⬇. These buttons appear when you hover over a
  table's top-right corner.
- **Charts** show a tooltip on hover. The ⋯ menu on a chart can save it as PNG/SVG or show the data.

---

## 2. Sidebar

| Element | Type | What it shows / does |
|---|---|---|
| **🛡️ Sentinel EWS** | Title | App name |
| *Early Warning System · prototype* | Caption | — |
| **Navigate** | Radio buttons | Switches between the 6 pages. The Watchlist's **Open customer** button also changes it |
| **Data:** | Text | Which dataset is loaded: *Sample portfolio (clean)*, *Sample portfolio (dirty, for validation)*, or *Upload (N files)* |
| **As-of date:** | Text | The analysis date from the rules file parameters |
| **Rules version:** | Code tag (e.g. `a6ea3bc9`) | Short fingerprint of the rules in use. It changes when rules are edited on the Rules page. It's part of the alert run ID and the AI summary cache key, so you can tell which rules produced a result |
| **Local LLM:** | Status | 🟢 *model name* when Ollama is running with a supported model; ⚪ *off (template text)* otherwise. Checked on every page load (1.5 s timeout) |
| *Score 0–100 · higher = riskier* | Caption | A reminder of the score direction |

---

## 3. Upload & Validate

**Purpose:** load portfolio data and show what was accepted, rejected and why
(required capability 1). This is the landing page.

### 3.1 Loading data
| Element | Type | Behaviour |
|---|---|---|
| **Bundled dataset** | Dropdown | *Sample portfolio (clean)*: the 600-customer synthetic portfolio. *Sample portfolio (dirty, for validation)*: the same data with 72 deliberate defects |
| **Load bundled dataset** | Primary button | Loads and validates the selected dataset, then refreshes every page. Results are cached, so switching back is instant |
| **…or upload files** | File drop zone | Accepts multiple `.csv`, `.xlsx` or `.json` files (200 MB each). Tables are recognised by file or sheet name (e.g. `customers.csv`, a workbook with a `repayments` sheet, `loans.json` → facilities) |
| **Validate uploaded files** | Button (appears after files are chosen) | Reads, maps headers, validates and replaces the current dataset. The sidebar then shows *Upload (N files)* |

### 3.2 Summary metrics
| Metric | Meaning |
|---|---|
| **Records received** | Total rows read across all tables, including rejected ones |
| **Accepted** | Rows that passed validation and are used for scoring |
| **Rejected (errors)** | Rows dropped because of an ERROR (blank mandatory field, bad date, negative amount, orphan reference, duplicate key…) |
| **Warnings** | Issues that were corrected or flagged, with the row kept |

### 3.3 Per table
A table with one row per input table:

| Column | Meaning |
|---|---|
| `table` | customers, facilities, repayments, delinquency, exposure, financials, exception_evidence, bank_holidays |
| `received` | Rows read |
| `rejected` | Rows dropped (including knock-on orphans) |
| `accepted` | Rows kept |

### 3.4 Relationships
Shows that the tables are linked to each other:

| Column | Meaning |
|---|---|
| `link` | The relationship, e.g. *customers → facilities* |
| `linked` | Distinct parent keys that have at least one child row |
| `of` | Total parent rows |

For example, *facilities → repayments, 1037 of 1037* means every facility has repayment records.

### 3.5 Validation issues (N)
Shown when there are issues. With the clean sample it shows a green
*"No issues found. All records accepted and linked."* box instead.

| Element | Behaviour |
|---|---|
| **Severity** filter | ERROR / WARNING (both selected by default) |
| **Table** filter | Restrict to one or more tables (empty = all) |
| **Issue type** filter | Restrict to issue types (empty = all) |
| **Bar chart** | Count of issues per type after filtering |
| **Issues table** | One row per issue |

Issues table columns:

| Column | Meaning |
|---|---|
| `table` | Where the problem is |
| `record_key` | Primary key of the offending row (e.g. `RP00012518`, or `facility_id\|snapshot_date`) |
| `column` | Column at fault (blank for table-level issues) |
| `issue_type` | e.g. `ORPHAN_REFERENCE`, `INVALID_DATE`, `NEGATIVE_AMOUNT`, `DUPLICATE_RECORD`, `TEXT_IN_NUMERIC`, `STATUS_MISMATCH`, `OUT_OF_RANGE` |
| `severity` | ERROR = row rejected · WARNING = row kept |
| `message` | Plain-English explanation and what was done (e.g. *"net != inflows - outflows; recomputed"*) |

---

## 4. Portfolio

**Purpose:** a management view of portfolio risk, emerging concentrations and model accuracy.

### 4.1 Headline metrics
| Metric | Meaning |
|---|---|
| **Customers** | Customers scored |
| **Red / Amber / Watch** | Customers in each tier (Green = the rest) |
| **Outstanding at Amber/Red** | Sum of current on-us outstanding balance for Amber and Red customers, in AED millions |

### 4.2 Tier mix by portfolio
- **Chart:** horizontal stacked bar, one bar per portfolio (Consumer Banking, Business Banking).
- **Reading it:** segment length is the number of customers in each tier, coloured by tier.
  A bigger orange and red share means a riskier portfolio. Hover for exact counts.

### 4.3 Trajectory (vs 3 months ago)
- **Chart:** horizontal bar of customers per trajectory.
- **Categories:**
  - **Stable:** score within ±10 points
  - **Deteriorating:** +10 or more
  - **Improving:** −10 or more
  - **Recovering:** was Amber or Red, now Green or Watch
  - **New:** no score 3 months ago

### 4.4 Emerging risk clusters
- **Caption:** the rule used. A segment is emerging when its Amber/Red rate is significantly
  above the portfolio rate (Wilson 95% lower bound above the portfolio rate, with at least 3
  at-risk customers).
- **Table:** the top 15 segments, emerging ones first, then by lift.

| Column | Meaning |
|---|---|
| `segment` | Segment values, e.g. *Construction & Contracting · Sharjah* |
| `dimensions` | Which attributes define it: `category × emirate`, `primary_product × emirate`, `category`, `segment × portfolio` |
| `customers` | Customers in the segment (minimum 5) |
| `at_risk` | Amber or Red customers in it |
| `at_risk_rate` | at_risk ÷ customers, % |
| `portfolio_rate` | Amber/Red share across the whole portfolio, % |
| `lift` | at_risk_rate ÷ portfolio_rate (2.0 = twice as risky as average) |
| `wilson_low` | Conservative (95% lower-bound) estimate of the segment rate, % |
| `momentum_pp` | Change in at-risk share vs 3 months ago, percentage points; positive = getting worse |
| `exposure_at_risk` | Outstanding balance of the segment's Amber/Red customers, AED |
| **Emerging** | ☑ when the segment meets the emerging rule |

### 4.5 Roll-rate matrix
- **Chart:** heatmap with text labels.
- **Rows:** this month's DPD bucket. **Columns:** next month's bucket.
- **Cells:** the percentage of facilities in the row bucket that moved to the column bucket
  one month later. Each row sums to 100%. Darker orange = more likely.
- **Reading it:** the diagonal is "stayed". Cells right of the diagonal are **roll-forward**
  (getting worse); cells left of it are **cures**. For example, *1-30 → Current 60%* means
  60% of early-delinquent facilities cure the next month.

### 4.6 Accuracy vs planted ground truth
Shown **only for the clean sample**, which has planted answers. Hidden for the dirty set and uploads.

| Metric | Meaning |
|---|---|
| **High/Critical caught (Amber+)** | % of customers planted as High or Critical that the app rated Amber or Red |
| **False alarms on low-risk** | % of customers planted as Low that the app rated Amber or Red |
| **Within one tier** | % where the app's tier is at most one step from the planted band |
| **Trap customers flagged** | Amber/Red among customers designed to look risky but not be: isolated exceptions, seasonal SMEs, thin files, salary elsewhere, cured. Lower is better |

| Table | Meaning |
|---|---|
| **Expected (rows) vs predicted (columns)** | Confusion matrix. Planted Low/Medium/High/Critical are mapped to Green/Watch/Amber/Red; the diagonal is correct |
| **Predicted tier by planted archetype** | For each behaviour profile (STABLE, ROLL_FORWARD, SME_SEASONAL…), how many customers landed in each tier |

---

## 5. Watchlist

**Purpose:** the working list of customers needing attention (required capability 4).

### 5.1 Filters
Two rows of multi-select filters. Empty means no filter, and filters combine with AND.

| Filter | Options | Default |
|---|---|---|
| **Risk tier** | Green, Watch, Amber, Red | Watch + Amber + Red (Green hidden) |
| **Product** | Any product name; a customer matches if any of their **active** facilities is that product | — |
| **Customer segment** | Mass, Mass Affluent, Affluent, Micro, Small, Medium | — |
| **Portfolio** | Consumer Banking, Business Banking | — |
| **Delinquency status** | DPD bucket: Current, 1-30, 31-60, 61-90, 91-180, 180+ | — |
| **Emirate** | Region | — |
| **Trajectory** | Stable, Deteriorating, Improving, Recovering, New | — |
| **Confidence** | High, Medium, Low | — |

The caption under the filters shows the number of matching customers and the sort order:
**tier (Red first) → score → exposure at risk**.

### 5.2 Table columns
| Column | Meaning |
|---|---|
| (checkbox) | Select a row to enable **Open customer** |
| `customer_id` | CIF number |
| `customer_name` | Synthetic name |
| `products` | Active products, comma-separated |
| **Outstanding (AED)** | Current on-us outstanding balance |
| **Score** | Risk score as a 0–100 progress bar |
| `tier` | Green / Watch / Amber / Red |
| `confidence` | High / Medium / Low, from data completeness. Low means "limited data", not "low risk" |
| `trajectory` | Change vs 3 months ago |
| **Contributing indicators** | Names of the top 3 signals driving the score, e.g. *Repeated late or missed instalments · Currently in arrears · Delinquency at other lenders* |
| `dpd_bucket` | Current delinquency bucket |
| `monitoring_status` | Status for the tier: NORMAL · WATCHLIST · ACTION_REQUIRED · ESCALATE |
| `review_status` | Latest analyst decision on the customer's most recent alert. **OPEN** = alert published but not decided; **—** = no alert published |
| `customer_segment`, `portfolio` | Segment attributes |

### 5.3 Actions
| Element | Behaviour |
|---|---|
| **Open CIFxxxxx →** | Appears after selecting a row. Goes to the Customer page for that customer |
| **Download watchlist (CSV)** | Downloads the filtered list as shown |

---

## 6. Customer

**Purpose:** explain one customer's score and show the evidence behind it (required
capabilities 3 and 4).

### 6.1 Header
| Element | Meaning |
|---|---|
| **Customer** dropdown | All customers, highest score first, shown as `ID · name · tier score`. Type to search. Defaults to the last opened customer |
| **Name + tier badge** | Customer name with a coloured tier pill |
| Caption line | ID · type (INDIVIDUAL/SME) · employer sector or industry · segment · emirate · active products |

### 6.2 Headline metrics
| Metric | Meaning |
|---|---|
| **Risk score** | Current 0–100 score. The small delta below is the change vs 3 months ago; **an increase shows red, a decrease green** |
| **Confidence** | High / Medium / Low, with the **Data Completeness Score (DCS)** below it. DCS weighs history length, months of data present, bureau availability and salary/receipt visibility |
| **Trajectory** | Stable / Deteriorating / Improving / Recovering / New |
| **Current DPD** | Days past due now (worst facility) |
| **Roll-forward prob.** | Historical probability that a facility in this customer's current bucket and portfolio moves to a worse bucket next month (from the roll-rate matrix) |
| **Outstanding** | On-us outstanding balance, AED |

### 6.3 Banners
| Banner | When it appears |
|---|---|
| 🔵 **Insufficient evidence…** (info) | Confidence is Low and there's no hard evidence (no arrears, no repeated lateness). The tier is capped at Watch, because missing data is not treated as risk |
| 🔴 **Hard trigger: DPD ≥ 90 / DPD ≥ 30** (error) | An override forced the tier up: 90+ days → Red, 30+ days → at least Amber |
| **Recommended action:** | Policy action for the tier, from the rules file |

### 6.4 How the score was calculated (left column)
| Element | Meaning |
|---|---|
| **Points bar chart** | One bar per signal that contributed. **Orange** bars add risk; **green** bars (negative) are mitigation. Hover for the metric value, operator, threshold, strength (0–1) and points |
| **Score arithmetic** caption | `Raw <sum of risk points> + mitigation <negative points> → score <capped 0–100> → tier <tier>`, plus `(trigger: …)` if an override applied and *"mitigation blocked by underlying stress"* if a one-off-delay credit was refused |
| **Reason bullets** | Each fired signal in plain English with its value vs threshold, largest effect first, e.g. *"Delinquency at other lenders: 120 days (threshold ≥ 30 days)"* |
| **All signals (incl. not evaluated)** expander | Every rule for this customer: `id`, `name`, `category`, `value`, `operator`, `threshold`, `strength`, `points` and **status**: *fired* (earned points), *ok* (evaluated, below threshold) or *not evaluated (no data)* (e.g. no salary visibility, bureau no-hit) |

### 6.5 Module indices (right column)
A small table showing how stressed each area is, from 0 to 100: the share of available
points earned among signals that could be evaluated.

| Module | Area |
|---|---|
| **RBDI** | Repayment behaviour (late/missed, DPD trend, arrears, partials, recent cure) |
| **CFSI** | Cash flow (income decline, consecutive declines, salary stopped, balance erosion, bounced payments) |
| **LPI** | Leverage and exposure (utilisation, spikes, debt burden, new credit, exposure vs income) |
| **BUREAU** | Other lenders (external delinquency, bureau score drop) |

A blank index means none of that module's signals could be evaluated (no data).

### 6.6 AI risk summary (right column)
| Element | Behaviour |
|---|---|
| **Generate summary (model)** button | Shows the model to be used, e.g. *(qwen3:4b-instruct)*, or *(template)* when Ollama is off. Click to generate |
| *Running model locally…* spinner | Shown while the local model writes (usually 1–4 s) |
| **Summary text** | 3–5 sentences on the risk position and drivers, ending with *Suggested action:* |
| ⚠️ Warning box | Shown if the model wrote a number that isn't in the evidence. The deterministic template summary is shown instead |
| **Source** caption | Model name, *"numbers verified against evidence"*, tokens in/out and seconds; or *"deterministic template"* |

Summaries are kept per customer + rules version + model. Revisiting a customer shows the
stored summary without calling the model again; press the button to regenerate. **The AI
never changes the score or tier.**

### 6.7 History charts
| Chart | Shows |
|---|---|
| **Max days past due (month-end)** | Red area chart of the worst facility's DPD at each month-end over 24 months. A rising area is deterioration; a drop to zero is a cure |
| **Income / inflows / average balance (AED)** | Monthly lines: `income` (salary credit, or business receipts for SMEs), `total_inflows`, `average_balance`. Gaps are missing months; there's no income line if salary isn't credited to this bank |
| **Revolving utilisation %** (captioned *"…and bureau score"*, see §12) | Monthly card / working-capital utilisation. Above 85% is high; above 100% is over limit |
| **Obligations: on-us vs other lenders (AED / month)** | Stacked area: `onus_monthly_obligation` (this bank) and `bureau_external_monthly_obligation` (other lenders). A growing top layer means new borrowing elsewhere |

### 6.8 Detail tabs
| Tab | Contents |
|---|---|
| **Repayments** | Every instalment in the window, newest first: due date, amount due, payment date, amount paid, days late, status (PAID_ON_TIME / PAID_LATE / PARTIALLY_PAID / UNPAID), channel |
| **Facilities** | The customer's loans and cards: product, limit or amount, dates, rate, instalment, channel, outstanding, status |
| **Exposure** | Monthly exposure snapshots, newest first: on-us totals, utilisation, new facilities, bureau facilities, obligations, enquiries, external DPD, bureau score |
| **Evidence on file** | Exception records (salary delay, processing delay, hardship, holiday) with source and **verified** flag. Verified evidence strengthens one-off-delay mitigation |

---

## 7. Review

**Purpose:** analyst triage with an audit trail, preserving the original score and
evidence (required capability 5).

### 7.1 Publishing alerts
| Element | Behaviour |
|---|---|
| Explanation text | Publishing freezes score, tier and evidence; decisions never change the frozen alert |
| **Publish alerts for run `<as-of>-<rules version>`** | Creates one frozen alert for every Watch, Amber or Red customer in the current view. Re-publishing the same run adds only customers not already alerted. Editing the rules creates a new run ID |
| ✅ *N new alerts frozen* | Confirmation after publishing |
| ℹ️ *No alerts yet. Publish a run first.* | Shown until the first publish |

### 7.2 Alert list
| Element | Meaning |
|---|---|
| **Status** filter | OPEN (no decision yet) or any decision type. Defaults to OPEN; clear it to see all |
| Alerts table | `alert_id` (AL-xxxxxxxx), `customer_id`, `original_tier`, `original_score`, `confidence`, `status` (latest decision or OPEN), `decisions` (count), `run_id`, `created_at`. Sorted by original score, highest first |
| **Alert** dropdown | Choose the alert to review (`ID · customer · original tier`) |

### 7.3 Alert detail
| Element | Meaning |
|---|---|
| **Original (frozen)** + badge | Tier, score and confidence when the alert was published. Never changes |
| Frozen reason bullets | Reasons as they were at publish time |
| **Frozen evidence JSON** expander | The full evidence record captured: every signal value, points, mitigation, trigger, indices |
| **Live (current rules)** + badge | The customer's score now, under the current rules and data |
| *Live score differs…* caption | Shown when live ≠ frozen (e.g. after a rules edit), confirming the original is preserved |

### 7.4 Decision form
| Element | Behaviour |
|---|---|
| **Decision** radio | Continue Monitoring · Requires Investigation · False Positive · Escalate For Review |
| **Reason (required)** | Free text; must not be empty |
| **Analyst** | Name or ID; remembered for the session |
| **Record decision** | Appends the decision. If reason or analyst is missing, shows *"Reason and analyst are required."* and saves nothing. On success the form clears, and with the OPEN filter active the decided alert drops out of the list |

### 7.5 Decision history (append-only)
Every decision ever recorded for the selected alert, newest first: `decision`, `reason`,
`analyst_id`, `decided_at`. Reclassifying adds a new row; nothing is edited or deleted.

---

## 8. Rules

**Purpose:** show that thresholds and weights are configuration, and let you test changes
live across the whole portfolio.

### 8.1 Signal table (editable)
| Column | Editable | Meaning |
|---|---|---|
| `signal_id` | — | RS01–RS18 risk signals, DQ01–DQ03 data-quality flags |
| `enabled` | ✅ checkbox | Untick to switch a signal off |
| `name`, `category`, `metric` | — | What the signal measures and which module it feeds |
| `operator` | — | `>=`, `<=`, `<`, `==` |
| `threshold` | ✅ | Value at which the signal starts firing (earns **half** its weight) |
| `severe` | ✅ | Value at which it earns its **full** weight (linear in between) |
| `weight` | ✅ | Maximum points. A negative weight is a mitigant (RS05); 0 means a flag only (DQ signals) |
| `lookback_months` | — | Window the metric looks at (informational) |
| `description` | — | Plain-English definition |

Double-click a cell to edit it; press Enter to confirm.

### 8.2 Score band table (editable)
| Column | Editable | Meaning |
|---|---|---|
| `band` | — | Green, Watch, Amber, Red |
| `min_score` | ✅ | Lowest score in the band. **This is what assigns tiers** |
| `max_score` | ✅ | Informational only (see §12) |
| `monitoring_status` | ✅ | Label shown on the watchlist |
| `recommended_action` | ✅ | Text shown on the Customer page and sent to the AI summary |

### 8.3 Buttons and results
| Element | Behaviour |
|---|---|
| **Apply and re-score** | Applies the edited table to the session and re-scores all customers (under 1 s). The sidebar rules version changes |
| **Reset to file defaults** | Reloads the rules from `data/sample/rules/risk_signal_definitions.json` |
| **Download rules JSON** | Saves the current (possibly edited) rules, ready to commit as new defaults |
| **Effect of last change** table | Rows *before*, *after* and *change*; columns Green/Watch/Amber/Red customer counts |

Edits last for the browser session only. To make them permanent, download the JSON or
edit `datagen/rules.py` and regenerate.

---

## 9. Common interactions

| Task | How |
|---|---|
| Find the riskiest customers | **Watchlist** (default filter) → top rows |
| Drill into a customer | Tick a watchlist row → **Open …** · or pick from the **Customer** dropdown |
| See why a customer is rated X | Customer → points chart + reason bullets → **All signals** expander |
| Check a "no data" customer isn't penalised | Customer → Confidence Low + blue *Insufficient evidence* banner; signals show *not evaluated* |
| Find emerging segments | **Portfolio → Emerging risk clusters** (☑ Emerging) |
| Record an analyst decision | **Review → Publish alerts** (once) → choose alert → decision + reason + analyst → **Record decision** |
| Prove the original score is preserved | Record a decision, then another; the frozen block and evidence JSON stay unchanged and history shows both |
| Test a threshold change | **Rules** → edit → **Apply and re-score** → *Effect of last change*; Review shows *Live score differs* for affected alerts |
| Test data validation | **Upload & Validate** → *Sample portfolio (dirty…)* → **Load** → filter the issues |
| Export | Watchlist **Download CSV** · Rules **Download JSON** · any table's ⬇ icon |

---

## 10. Suggested demo walkthrough

About 6 minutes:

1. **Upload & Validate**
   - Load the **dirty** set: 516 rows rejected, 42 warnings. Filter to `ORPHAN_REFERENCE` and `INVALID_DATE`.
   - Reload the **clean** set.
2. **Portfolio**
   - Read the tier counts and outstanding at Amber/Red.
   - Point to the **Construction & Contracting · Sharjah** cluster (lift ~4.3).
   - Read one roll-rate cell, e.g. 1-30 → Current.
   - Show accuracy: 75.8% caught, 0% false alarms, 0/130 traps.
3. **Watchlist**
   - Filter **Business Banking + Amber**, then open the top customer.
4. **Customer**
   - Walk through the points chart, reasons and trajectory.
   - Generate the **AI summary** and point to the verified-numbers caption.
   - Show the history charts.
5. **Silent stress and fairness examples:**
   - A seasonal SME (Green despite a summer dip).
   - A thin-file customer (Low confidence, capped at Watch).
6. **Review**
   - Publish, then decide *Requires Investigation*, then reclassify *False Positive*.
   - Show the frozen original and the two-row history.
7. **Rules**
   - Lower the RS07 threshold or raise a weight, then **Apply**. Show *Effect of last change*.
   - Return to **Review** to show *Live score differs* while the original is unchanged.

Reset first for a clean demo:

```bash
rm data/app/sentinel.duckdb
```

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **As-of date** | Date the analysis is run "as at"; all windows count back from it |
| **DPD** | Days past due: days since the oldest unpaid instalment's due date |
| **DPD bucket** | Current, 1-30, 31-60, 61-90, 91-180, 180+ |
| **Roll-forward / cure** | Moving to a worse / better DPD bucket |
| **On-us / off-us** | This bank's facilities / other lenders' (from the credit bureau) |
| **Utilisation** | Revolving balance ÷ limit for cards and working-capital lines |
| **Debt burden ratio (DBR)** | Total monthly obligations ÷ monthly income (UAE guideline: 50%) |
| **Signal** | One configurable rule (e.g. RS07 Sustained inflow decline) |
| **Strength** | 0–1 measure of how far past its threshold a signal is |
| **Points** | weight × strength; the sum gives the raw score |
| **Mitigation** | Negative points for an isolated, recovered delay (blocked if other stress exists) |
| **Hard trigger** | Override that forces a minimum tier (DPD ≥ 30 → Amber, ≥ 90 → Red) |
| **DCS** | Data Completeness Score, 0–100, which sets confidence |
| **Confidence** | How much data supports the score (High ≥ 75, Medium ≥ 40, Low < 40 DCS) |
| **RBDI / CFSI / LPI** | Repayment-behaviour, cash-flow-stress and leverage-pressure module indices |
| **Trajectory** | Score change vs 3 months ago |
| **Alert / run** | Frozen snapshot of a customer's score and evidence for a given as-of date and rules version |
| **Lift / Wilson lower bound** | Segment risk ÷ portfolio risk / conservative estimate of the segment's risk rate |

---

## 12. Known UI quirks

| Where | Quirk | Impact |
|---|---|---|
| Customer → history | Third chart's caption says *"Revolving utilisation % and bureau score"* but plots utilisation only | Cosmetic; bureau score is in the Exposure tab |
| Rules → score bands | `max_score` is editable but unused; tiers are assigned from `min_score` | Editing `max_score` has no effect |
| Rules → edits | Changes last for the browser session only | Use **Download rules JSON** to keep them |
| Upload | Uploaded data replaces the current dataset; there's no merge or append | Re-load the bundled sample to go back |
| Review | The database (`data/app/sentinel.duckdb`) persists across restarts and keeps test alerts | Delete it for a clean demo |
| Accuracy panel | Clean sample only | Hidden for dirty or uploaded data by design |
| Layout | Verified at ~800 px wide only | Check full-screen / projector before presenting |
| Sidebar LLM status | Ollama is probed on every page load | Up to 1.5 s delay if Ollama is unreachable |
