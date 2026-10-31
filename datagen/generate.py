"""Generate the synthetic EWS dataset.

    uv run python datagen/generate.py [--customers 600] [--seed 42] [--out data]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import config as C  # noqa: E402
import rules  # noqa: E402
from dirty import make_dirty  # noqa: E402
from simulate import Generator  # noqa: E402
from tables import build_tables  # noqa: E402

INPUT_TABLES = ["customers", "facilities", "repayments", "delinquency", "exposure",
                "financials", "exception_evidence", "bank_holidays"]
INT_COLUMNS = ["tenure_months", "years_in_business", "bureau_external_facilities",
               "bureau_enquiries_3m", "bureau_external_max_dpd", "bureau_score"]


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in INT_COLUMNS:
        if col in df:
            df[col] = df[col].astype("Int64")
    return df


def to_records(df: pd.DataFrame) -> list[dict]:
    out = []
    for rec in df.astype(object).to_dict("records"):
        out.append({k: (None if v is None or (isinstance(v, float) and v != v) or v is pd.NA
                        else v.isoformat() if isinstance(v, date) else v)
                    for k, v in rec.items()})
    return out


def write_set(tables: dict, folder: Path, xlsx_name: str, json_too: bool):
    (folder / "csv").mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(folder / "csv" / f"{name}.csv", index=False)
    if json_too:
        (folder / "json").mkdir(exist_ok=True)
        for name, df in tables.items():
            (folder / "json" / f"{name}.json").write_text(
                json.dumps(to_records(df), separators=(",", ":"), default=str))
    (folder / "excel").mkdir(exist_ok=True)
    with pd.ExcelWriter(folder / "excel" / xlsx_name, engine="openpyxl") as xw:
        for name, df in tables.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)


def apply_profile(name: str) -> dict:
    """Override config constants for an alternative institution (see config.PROFILES)."""
    profile = C.PROFILES[name]
    for key, value in profile.items():
        if key != "label":
            setattr(C, key, value)
    return profile


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--profile", default="default", choices=sorted(C.PROFILES))
    profile_name = pre.parse_known_args()[0].profile
    profile = apply_profile(profile_name)

    ap = argparse.ArgumentParser(description=__doc__, parents=[pre])
    ap.add_argument("--customers", type=int, default=C.DEFAULT_CUSTOMERS)
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    args = ap.parse_args()

    t0 = time.time()
    tables = build_tables(Generator(args.customers, args.seed).build())
    tables = {k: tidy(v) for k, v in tables.items()}
    inputs = {k: tables[k] for k in INPUT_TABLES}

    out = args.out
    for sub in ("sample", "dirty", "_ground_truth"):
        shutil.rmtree(out / sub, ignore_errors=True)

    # Clean sample in all three formats + rule definitions.
    write_set(inputs, out / "sample", "ews_portfolio.xlsx", json_too=True)
    defs = rules.definitions()
    rules_dir = out / "sample" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "risk_signal_definitions.json").write_text(json.dumps(defs, indent=2))
    pd.DataFrame(defs["signals"]).to_csv(rules_dir / "risk_signals.csv", index=False)
    pd.DataFrame(defs["score_bands"]).to_csv(rules_dir / "score_bands.csv", index=False)
    with pd.ExcelWriter(rules_dir / "risk_signal_definitions.xlsx", engine="openpyxl") as xw:
        pd.DataFrame(defs["signals"]).to_excel(xw, sheet_name="signals", index=False)
        pd.DataFrame(defs["score_bands"]).to_excel(xw, sheet_name="score_bands", index=False)
        pd.DataFrame(defs["overrides"]).to_excel(xw, sheet_name="overrides", index=False)
        pd.DataFrame([defs["parameters"]]).astype(str).to_excel(
            xw, sheet_name="parameters", index=False)

    # Dirty copy for validation testing.
    dirty, manifest = make_dirty(inputs, args.seed)
    write_set({k: dirty[k] for k in INPUT_TABLES if k in dirty}, out / "dirty",
              "ews_portfolio_dirty.xlsx", json_too=False)
    manifest.to_csv(out / "dirty" / "_injected_issues.csv", index=False)

    # Ground truth: generator intent, kept apart so the app never ingests it.
    (out / "_ground_truth").mkdir(parents=True)
    tables["ground_truth"].to_csv(out / "_ground_truth" / "customer_labels.csv", index=False)

    gt = tables["ground_truth"]
    (out / "PROFILE.md").write_text(
        f"# {C.BANK_NAME}\n\n{profile['label']}\n\n"
        f"- Generated with: `uv run python datagen/generate.py --profile {profile_name} "
        f"--customers {args.customers} --seed {args.seed} --out {args.out}`\n"
        f"- As-of date: {C.AS_OF_DATE} · history {C.HISTORY_MONTHS} months · currency "
        f"{C.CURRENCY}\n"
        f"- Customers: {len(gt)} ({(gt.customer_type == 'SME').sum()} SME)\n"
        f"- Clusters: " + "; ".join(f"{c['cluster_id']} ({c['note']})" for c in C.CLUSTERS)
        + "\n\nSee `data/README.md` for the table dictionary (same schema as the default "
        "sample).\n")
    print(f"Profile: {profile_name} · {C.BANK_NAME}")
    print(f"Generated {args.customers} customers (seed {args.seed}, as-of "
          f"{C.AS_OF_DATE}) in {time.time() - t0:.1f}s -> {out}")
    for k, v in inputs.items():
        print(f"  {k:20s} {len(v):>7,} rows  {v.shape[1]:>3} cols")
    print(f"  dirty issues injected: {len(manifest)}")
    print("  expected risk bands:",
          tables["ground_truth"].expected_risk_band.value_counts().to_dict())


if __name__ == "__main__":
    main()
