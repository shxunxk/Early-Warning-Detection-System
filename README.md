# Sentinel Early Warning System

Sentinel is a prototype credit-risk early warning system (EWS) for banking
portfolios. It validates uploaded account data, computes explainable customer
risk scores, and provides advisory forecasts of future delinquency. Its bundled
data is synthetic and is intended for demonstrations and development, not for
credit decisions.

## What it does

- Accepts portfolio tables as CSV, Excel, or JSON and reports validation errors
  and warnings before analysis.
- Assigns each customer a rules-based risk score and tier, with signal-level
  reasons, confidence, hard-trigger overrides, and a data-completeness measure.
- Shows portfolio trends, delinquency roll rates, emerging risk segments,
  customer-level histories, and a filterable watchlist.
- Trains LightGBM forecasts from the uploaded portfolio's historical snapshots
  to estimate the chance that customers currently below 30 days past due (DPD)
  will reach 30+ DPD within one or three months.
- Supports alert review, editable risk rules, and a what-if simulator.
- Optionally uses Ollama for evidence-grounded customer summaries and an AI
  assistant. If an LLM is unavailable, the summary view has deterministic
  template text.
- Includes a separate, optional LangChain/LangGraph RAG command-line agent for
  searching project documents.

## Architecture

```text
CSV / Excel / JSON upload
          |
          v
src/ingest.py -- normalize headers, validate rows and table relationships
          |
          v
src/metrics.py -- calculate point-in-time customer metrics
          |
          +--> src/scoring.py -- weighted risk signals, rules, tiers and reasons
          |
          +--> src/insights.py -- roll rates, recovery and segment analysis
          |
          +--> src/forecast.py -- point-in-time LightGBM 30+ DPD forecasts
          |
          v
src/app.py -- Streamlit dashboard
          |
          +--> data/app/sentinel.duckdb -- persistent alerts and analyst decisions
          +--> optional Ollama -- language summaries (not the risk-scoring engine)
```

The standalone RAG workflow in `rag/` is separate from the dashboard AI
assistant. It builds a local Chroma index and queries it through a LangGraph
agent.

## Requirements and installation

Use Python 3.11 or newer where available. From the repository root, create and
activate a virtual environment, then install the project dependencies:

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell prevents environment activation, either allow activation for the
current terminal according to your organization's policy or invoke the
environment's interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` includes the dashboard, data-generation, and optional RAG
dependencies. The LightGBM implementation runs on CPU; RAPIDS/cuML and a GPU
are not required.

## Run the dashboard

Run this command from the repository root:

```bash
streamlit run src/app.py
```

On Windows, if the virtual environment is not activated:

```powershell
.\.venv\Scripts\python.exe -m streamlit run src\app.py
```

The app opens in a browser. Upload and validate a complete dataset to enable the
summary and analysis pages. The upload-first dashboard is implemented in
`src/app.py`; use that file as the application entry point.
The repository-root `app.py` is a legacy entry point from an earlier package
layout and is not the supported launch target.

### Dashboard pages

| Page | Purpose |
|---|---|
| Upload | Upload the portfolio tables and inspect accepted/rejected rows, validation issues, and relationships. |
| Summary | Portfolio briefing, headline risk measures, high-priority customers, and delinquency outlook. |
| Portfolio | Tier distribution, recent trajectory, emerging clusters, roll rates, and (for bundled test data) comparison with synthetic ground truth. |
| Watchlist | Filter and prioritize customers by tier, product, segment, delinquency, region, confidence, and trajectory. |
| Customer | Review an individual customer's score, signals, history, forecast, and explanations. |
| Review | Publish alerts and record analyst decisions. |
| Rules | Inspect and edit risk signals, score bands, and overrides for the current app session; download rules as JSON. |
| What-If Simulator | Change selected metrics and see the resulting rules-based score and tier. |
| AI Assistant | Ask questions about a selected customer using the customer evidence provided to the local LLM. |

## Upload data

The upload control accepts `.csv`, `.xlsx`, and `.json` files. Table names are
recognized from file names or Excel sheet names (for example,
`delinquency.csv`, a `delinquency` sheet, or a filename containing `dpd`).
Common bank-header aliases are normalized by `src/ingest.py`.

All of the following tables are required. `exception_evidence` is optional.
For CSV, provide one file per table. An Excel workbook can contain one table per
sheet. JSON can be a table-record list in a table-named file or an object keyed
by table name.

| Table | Required columns |
|---|---|
| `customers` | `customer_id`, `customer_type` |
| `facilities` | `facility_id`, `customer_id`, `product_code`, `sanctioned_amount`, `disbursement_date` |
| `repayments` | `repayment_id`, `facility_id`, `due_date`, `amount_due` |
| `delinquency` | `facility_id`, `snapshot_date`, `days_past_due` |
| `exposure` | `customer_id`, `snapshot_date` |
| `financials` | `customer_id`, `month`, `total_inflows`, `total_outflows` |
| `bank_holidays` | `holiday_date` |
| `exception_evidence` (optional) | `customer_id`, `event_date` |

Additional columns improve analysis. The data dictionary in
[`data/README.md`](data/README.md) describes the bundled schema, relationships,
synthetic scenarios, and conventions. In general:

- Use stable customer, facility, and record identifiers across tables.
- Use parseable dates, preferably ISO format (`YYYY-MM-DD`).
- Keep amounts and numeric measures numeric; use consistent currency. The
  bundled portfolio is AED.
- Include month-end delinquency and exposure snapshots, and monthly financial
  history, to support trend and forecast calculations.
- Include both historical and current snapshots if you want to train and score
  the forward-looking forecast.

The app rejects rows with invalid mandatory data or broken key relationships,
normalizes some formats, and reports warnings separately. Required tables must
be present, and the core tables must retain accepted records before analysis
can continue. The as-of date is set to the latest accepted delinquency snapshot
date.

## Risk scoring and model behavior

### Rules-based score

The rules engine is the primary scoring mechanism; LightGBM does **not** decide
the customer's tier. It evaluates configured behavioural signals and combines
them into a 0–100 score, where higher means riskier. The configured bands map
scores to Green, Watch, Amber, and Red. The current rules cover repayment
behaviour, cash flow, exposure, and bureau indicators, with module summaries
such as RBDI, CFSI, and LPI.

The engine also applies hard delinquency / consecutive-miss triggers, uses
data completeness to set confidence, and may cap a high tier when evidence is
limited and no hard trigger exists. Missing values are treated as not evaluated,
not as zero-risk observations. Verified exception evidence can support a
mitigation rule, subject to the configured safeguards.

Default rules are in
[`data/sample/rules/risk_signal_definitions.json`](data/sample/rules/risk_signal_definitions.json).
They can be edited in the Rules page for the current session. Download changes
from the page if you want to retain them; the dashboard does not automatically
overwrite the default rules file.

### Forward-looking LightGBM forecast

`src/forecast.py` creates point-in-time training rows from the uploaded
portfolio's historical month-end data. For each eligible customer and
snapshot, the target is whether they reach 30+ DPD within one or three months.
Customers already at 30+ DPD are not forecast; roll-rate analysis covers
existing delinquency.

The model uses the behavioural metrics plus an SME indicator. It holds out the
most recent three labelled snapshots for out-of-time evaluation, reports
metrics such as ROC-AUC, PR-AUC, and top-decile capture, then refits on all
labelled history for the current predictions. SHAP is used to show forecast
drivers. Forecast probabilities are advisory and do not alter the rules score
or tier.

Forecasts need sufficient historical snapshots and both outcome classes for
meaningful training and validation. A forecast may be unavailable for an
upload with limited history or too few eligible examples. The current code
does not explicitly apply probability calibration or monotonic constraints;
probabilities and model metrics should be assessed before operational use.

`src/ml.py` also contains a separate LightGBM second-opinion model for
comparing prior-period metrics with the current Amber/Red label. It is not
blended into the rules-based score.

## Synthetic data and validation

The repository includes fully synthetic portfolios for development:

- `data/sample/csv/` — default Synthetic Gulf Bank sample.
- `data/northern_sme/sample/csv/` — Northern Emirates SME-heavy sample.
- `data/dirty/csv/` and `data/northern_sme/dirty/csv/` — validation test
  datasets with injected defects.
- `data/_ground_truth/` and `data/northern_sme/_ground_truth/` — synthetic
  generator labels for evaluation; **do not upload these as input tables**.

To load a bundled portfolio into the dashboard, upload its eight CSV files from
the relevant `sample/csv/` directory. The bundled clean sample also includes
an Excel workbook. The generator writes JSON exports, but those directories
are git-ignored and may not be present in a checkout.

Generate or replace the default synthetic dataset:

```bash
python datagen/generate.py --customers 600 --seed 42 --out data
```

Generate the Northern Emirates profile:

```bash
python datagen/generate.py --profile northern_sme --customers 1200 --seed 42 --out data/northern_sme
```

**Caution:** the generator replaces the `sample`, `dirty`, and
`_ground_truth` directories beneath the specified output path. Back up any
files there that you need before regenerating.

Run consistency checks on the default generated CSVs:

```bash
python datagen/checks.py
```

Or specify a CSV directory:

```bash
python datagen/checks.py data/northern_sme/sample/csv
```

The optional Ollama comparison utility runs several local chat models against
the same customer cases and reports latency and numeric-grounding rates:

```bash
python scripts/llm_bakeoff.py qwen3:4b-instruct llama3.2:3b --n 20
```

The named Ollama models must already be available locally.

## Optional Ollama setup

The dashboard's AI summaries and assistant use Ollama locally. The scoring and
forecasting functionality do not require Ollama. Install and start Ollama
separately, then download a model, for example:

```bash
ollama pull qwen3:4b-instruct
```

The dashboard checks Ollama at `http://localhost:11434` by default. Override
this with `OLLAMA_HOST` if needed. If Ollama is unavailable, AI summaries use
template text and the AI Assistant page is disabled. The dashboard assistant
uses the provided customer evidence as context; it is not connected to the
standalone RAG index.

## Optional standalone RAG agent

The separate RAG command-line workflow indexes top-level Markdown, text, and
PDF files in `rag/documents/`, persists a Chroma database under
`rag/chroma_db/`, and answers questions with a LangGraph agent. It requires a
running Ollama service and both an embedding and a chat model:

```bash
ollama pull nomic-embed-text
ollama pull llama3.1
python rag/build_index.py
python rag/agent.py "What does the project say about data completeness?"
```

Relevant environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL, also used by the dashboard. |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model used to build and query the Chroma index. |
| `OLLAMA_CHAT_MODEL` | `llama3.1` | Chat model for the RAG agent. |
| `RAG_DOCUMENTS_DIR` | `rag/documents` | Source document directory. |
| `RAG_VECTOR_STORE_DIR` | `rag/chroma_db` | Persistent Chroma index directory. |

Keep the embedding model consistent between index creation and queries. Rebuild
the index if you change embedding models.

## Persistence and local files

- Analyst alerts and decisions are stored in
  `data/app/sentinel.duckdb` and persist between app sessions.
- The RAG index is stored in `rag/chroma_db/`.
- Python environments and bytecode caches are excluded from version control.
- Uploaded data is processed by the app server; treat portfolio data as
  confidential and restrict access to the application server, database, and
  index directories. This prototype does not provide production-grade
  authentication, authorization, encryption-at-rest, or deployment hardening.

## Repository map

```text
.
├── src/
│   ├── app.py          # Streamlit dashboard entry point
│   ├── ingest.py       # File loading, normalization, validation
│   ├── metrics.py      # Customer feature computation
│   ├── scoring.py      # Rules-based risk scores and tiers
│   ├── forecast.py     # Point-in-time LightGBM delinquency forecasts
│   ├── ml.py           # Optional second-opinion LightGBM + SHAP
│   ├── insights.py     # Roll rates, recovery, segments, evaluation
│   ├── llm.py          # Ollama summaries and grounded response checks
│   └── store.py        # DuckDB alert and analyst-decision persistence
├── datagen/            # Synthetic data generator and consistency checks
├── data/               # Synthetic sample, rules, and local app database
├── rag/                # Standalone document indexing and RAG agent
├── scripts/            # Utilities, including the Ollama model bake-off
├── requirements.txt
└── README.md
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `ModuleNotFoundError: No module named 'src'` | Run from the repository root using `streamlit run src/app.py`, and confirm the project environment is active. |
| Upload reports missing tables | Supply all required tables, including `bank_holidays`; only `exception_evidence` is optional. Check table names in filenames or workbook sheet names. |
| Many rows rejected | Review the Upload page's validation issues, required columns, date formats, duplicate keys, and customer/facility references. |
| Forecast is unavailable | Check that the dataset has multiple monthly delinquency snapshots, enough history, and sufficient examples both with and without future 30+ DPD. |
| Ollama connection refused | Start the Ollama service, verify `OLLAMA_HOST`, and pull the selected model. The dashboard's scoring pipeline does not depend on Ollama. |
| RAG index not found | Run `python rag/build_index.py` before `python rag/agent.py`; verify the configured documents directory contains readable PDF, Markdown, or text files. |
| Excel import fails | Ensure `openpyxl` is installed (it is included in `requirements.txt`) and that each workbook sheet can be recognized as a table. |

## Limitations

Sentinel is a hackathon / prototype system, not a validated credit decision
platform. The shipped portfolios are synthetic. Model performance on synthetic
data does not establish performance on real portfolios. Before any real use,
validate data quality, leakage controls, calibration, fairness, explainability,
model drift, regulatory requirements, and human-review procedures with
qualified risk and compliance teams.
