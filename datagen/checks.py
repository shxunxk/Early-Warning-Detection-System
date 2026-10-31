"""Consistency checks on the generated CSVs. The clean sample must pass all of them.

    uv run python datagen/checks.py [data/sample/csv]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "data/sample/csv")
L = {p.stem: pd.read_csv(p) for p in folder.glob("*.csv")}
cu, fa, rp, dq, ex, fi = (L[k] for k in ("customers", "facilities", "repayments",
                                         "delinquency", "exposure", "financials"))
results = []
DATE_COLS = {"facilities": ["disbursement_date"], "repayments": ["due_date", "payment_date"]}
NUM_COLS = {"facilities": ["sanctioned_amount", "outstanding_balance"],
            "repayments": ["amount_due", "amount_paid"],
            "delinquency": ["days_past_due", "overdue_amount"],
            "financials": ["salary_credit", "total_inflows", "total_outflows", "net_cash_flow"]}
coerced = {}
for t, cols in DATE_COLS.items():
    for col in cols:
        raw = L[t][col]
        L[t][col] = pd.to_datetime(raw, format="ISO8601", errors="coerce")
        coerced[f"{t}.{col}: unparseable date"] = raw.notna() & L[t][col].isna()
for t, cols in NUM_COLS.items():
    for col in cols:
        raw = L[t][col]
        L[t][col] = pd.to_numeric(raw, errors="coerce")
        coerced[f"{t}.{col}: non-numeric"] = raw.notna() & L[t][col].isna()


def check(name, bad):
    n = int(bad.sum()) if hasattr(bad, "sum") else int(bad)
    results.append((name, n))


for name, bad in coerced.items():
    check(name, bad)
check("customers: missing customer_type", cu.customer_type.isna())
check("customers: invalid customer_type", ~cu.customer_type.dropna().isin(["INDIVIDUAL", "SME"]))
check("facilities: currency != AED", fa.currency != "AED")
cust = set(cu.customer_id)
fac_owner = dict(zip(fa.facility_id, fa.customer_id))
check("customers: duplicate ids", cu.customer_id.duplicated())
check("facilities: orphan customer", ~fa.customer_id.isin(cust))
check("facilities: non-positive amount", fa.sanctioned_amount <= 0)
check("facilities: closed with balance", (fa.facility_status == "CLOSED")
      & (fa.outstanding_balance > 0))
check("repayments: duplicate ids", rp.repayment_id.duplicated())
check("repayments: orphan facility", ~rp.facility_id.isin(fac_owner))
check("repayments: customer != facility owner", rp.customer_id != rp.facility_id.map(fac_owner))
check("repayments: paid > due", rp.amount_paid > rp.amount_due + 0.01)
check("repayments: negative amounts", (rp.amount_paid < 0) | (rp.amount_due <= 0))
due, paid = pd.to_datetime(rp.due_date), pd.to_datetime(rp.payment_date)
late = (paid - due).dt.days
check("repayments: on-time but paid after due",
      (rp.repayment_status == "PAID_ON_TIME") & (late > 0))
check("repayments: late but paid on/before due", (rp.repayment_status == "PAID_LATE") & (late <= 0))
check("repayments: unpaid with amount", (rp.repayment_status == "UNPAID") & (rp.amount_paid > 0))
disb = pd.to_datetime(rp.facility_id.map(dict(zip(fa.facility_id, fa.disbursement_date))))
check("repayments: due before disbursement", due <= disb)
check("delinquency: dpd>0 without overdue", (dq.days_past_due > 0) & (dq.overdue_amount <= 0))
check("delinquency: overdue without dpd", (dq.days_past_due == 0) & (dq.overdue_amount > 0))
check("delinquency: negative dpd", dq.days_past_due < 0)
# DPD can rise by at most the days elapsed between consecutive month-ends.
d = dq.sort_values(["facility_id", "snapshot_date"]).copy()
d["gap"] = pd.to_datetime(d.snapshot_date).groupby(d.facility_id).diff().dt.days
check("delinquency: dpd jump > elapsed days",
      d.days_past_due.groupby(d.facility_id).diff() > d.gap + 0.5)
# Final unpaid instalments should agree with the final delinquency snapshot.
last = dq[dq.snapshot_date == dq.snapshot_date.max()].set_index("facility_id")
open_rp = rp[rp.repayment_status.isin(["UNPAID", "PARTIALLY_PAID"])]
arrears = (open_rp.amount_due - open_rp.amount_paid).groupby(open_rp.facility_id).sum()
common = last.index.intersection(arrears.index)
check("delinquency vs repayments: overdue mismatch",
      (last.loc[common, "overdue_amount"] - arrears.loc[common]).abs() > 1)
check("exposure: orphan customer", ~ex.customer_id.isin(cust))
check("exposure: bureau score out of range", ~ex.bureau_score.dropna().between(300, 900))
check("exposure: utilisation out of range",
      ~ex.revolving_utilisation_pct.dropna().between(0, 110))
check("financials: duplicate customer-month", fi.duplicated(["customer_id", "month"]))
check("financials: net != in - out",
      (fi.net_cash_flow - (fi.total_inflows - fi.total_outflows)).abs() > 0.05)
check("financials: closing != opening + net",
      (fi.closing_balance - fi.opening_balance - fi.net_cash_flow).abs() > 0.05)
check("financials: negative balance", fi.closing_balance < 0)

width = max(len(n) for n, _ in results)
for name, n in results:
    print(f"  {'OK  ' if n == 0 else 'FAIL'} {name:<{width}}  {n}")
print(f"\n{sum(n == 0 for _, n in results)}/{len(results)} checks passed")
sys.exit(0 if all(n == 0 for _, n in results) else 1)
