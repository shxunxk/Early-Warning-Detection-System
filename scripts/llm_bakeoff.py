"""Compare local models on the same customers: latency, tokens, grounding pass rate.

    uv run python scripts/llm_bakeoff.py qwen3:4b-instruct llama3.2:3b [--n 20]
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import ingest, llm, metrics, scoring  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("models", nargs="+")
ap.add_argument("--n", type=int, default=20)
args = ap.parse_args()

t, _, _ = ingest.validate(ingest.load_folder(Path("data/sample/csv")))
rules = scoring.load_rules("data/sample/rules/risk_signal_definitions.json")
p = rules["parameters"]
s = scoring.score(metrics.compute(t, p["as_of_date"], p), rules, t["customers"])
# Mix of tiers so prompts vary in size and content.
pick = pd.concat([s[s.tier == tier].head(args.n // 4) for tier in scoring.TIERS]).index

rows, samples = [], {}
for model in args.models:
    llm.llm_summary(pick[0], s.at[pick[0], "evidence"], s.at[pick[0], "reasons"], model)  # warm
    for cid in pick:
        t0 = time.time()
        out = llm.llm_summary(cid, s.at[cid, "evidence"], s.at[cid, "reasons"], model,
                              s.at[cid, "recommended_action"])
        rows.append(dict(model=model, customer=cid, tier=s.at[cid, "tier"],
                         wall_s=time.time() - t0, prompt_tokens=out["prompt_tokens"],
                         output_tokens=out["output_tokens"], grounded=out["grounded"],
                         bad=out["ungrounded_numbers"]))
        samples.setdefault(model, (cid, out["text"]))

r = pd.DataFrame(rows)
summary = r.groupby("model").agg(
    runs=("customer", "size"), grounded_pct=("grounded", lambda x: round(100 * x.mean(), 0)),
    median_s=("wall_s", "median"), p90_s=("wall_s", lambda x: x.quantile(0.9)),
    prompt_tok=("prompt_tokens", "median"), output_tok=("output_tokens", "median"),
    tok_per_s=("output_tokens", "sum"))
summary["tok_per_s"] = (summary.tok_per_s / r.groupby("model").wall_s.sum()).round(0)
print(summary.round(1).to_string())
print("\nFailures (numbers not in evidence):")
print(r[~r.grounded][["model", "customer", "tier", "bad"]].to_string(index=False) or "none")
for model, (cid, text) in samples.items():
    print(f"\n--- {model} · {cid} ---\n{text}")
