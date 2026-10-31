# Sentinel: Early Warning System (Azentio AI Hackathon, problem statement 4)

Explainable early-warning scoring for a loan portfolio: ingests customer, facility,
repayment, delinquency, exposure and account data, detects emerging credit-risk signals,
scores every customer 0–100 (higher = riskier), and gives analysts a watchlist and review
workflow that preserves the original evidence.

## Run

```bash
uv sync
uv run streamlit run app.py
```

Open http://localhost:8501. The clean sample portfolio loads automatically.

Optional local LLM summaries (no API keys; the app works without it):

```bash
ollama serve
ollama pull qwen3:4b-instruct   # default; llama3.2:3b is the fallback
```

Use the **instruct** tag: plain `qwen3:4b` is the thinking-only build, which spends its token
budget on reasoning text. Model choice from `scripts/llm_bakeoff.py` (20 customers across all
tiers, M5 Pro):

| Model | Grounded | Median latency | Tokens in / out | Notes |
|---|---|---|---|---|
| **qwen3:4b-instruct** | 100% | 1.4 s | ~268 / ~107 | Most accurate wording; default |
| llama3.2:3b | 100% | 1.0 s | ~272 / ~94 | Fastest; occasionally mislabels facts (e.g. a count called "score") |
| llama3.1:8b | 100% | 2.5 s | ~262 / ~102 | No quality gain for 2× the time and memory |

Every summary's numbers are checked against the evidence; failures fall back to template text.
Summaries are cached per customer, rules version and model.

## Pages
| Page | What it shows |
|---|---|
| Upload & Validate | CSV / Excel / JSON upload or bundled sample; accepted vs rejected rows; validation issues |
| Portfolio | Tier mix, trajectory, emerging risk clusters, roll-rate matrix, accuracy vs ground truth |
| Watchlist | Filterable customer list with score, tier, confidence, trajectory and contributing indicators |
| Customer | Score breakdown, fired signals vs thresholds, history charts, AI summary |
| Review | Publish frozen alerts; record append-only analyst decisions |
| Rules | Edit thresholds, severities and weights; instant re-score |

## Layout
```
app.py                 Streamlit UI
sentinel/              ingest · metrics · scoring · insights · store · llm
datagen/               synthetic data generator + consistency checks
data/sample            clean input set (csv, excel; json via generator) + rules
data/dirty             same data with 72 injected defects
data/_ground_truth     planted archetypes (evaluation only)
docs/                  data architecture, app architecture, 1-hour prototype plan
sql/schema.sql         DuckDB DDL
```

Regenerate data with `uv run python datagen/generate.py` and run the data checks with
`uv run python datagen/checks.py`.
