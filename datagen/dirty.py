"""Build a 'dirty' copy of the dataset with known, logged data-quality defects.

Used to exercise the app's validation step (required capability 1). Every injected
defect is recorded in a manifest so validation output can be scored against it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class Injector:
    def __init__(self, tables: dict[str, pd.DataFrame], rng: np.random.Generator):
        self.rng = rng
        self.t = {k: v.astype(object).copy() for k, v in tables.items()}
        self.manifest = []
        self.used: dict[str, set] = {k: set() for k in tables}

    def rows(self, table: str, k: int, where=None) -> list[int]:
        df = self.t[table]
        idx = df.index if where is None else df.index[where(df)]
        idx = [i for i in idx if i not in self.used[table]]
        pick = list(self.rng.choice(idx, size=min(k, len(idx)), replace=False))
        self.used[table].update(pick)
        return pick

    def log(self, table, row, key_col, column, issue, value, desc):
        key = self.t[table].at[row, key_col] if key_col else row
        self.manifest.append(dict(table=table, record_key=str(key), column=column,
                                  issue_type=issue, injected_value=str(value),
                                  description=desc))

    def set(self, table, row, key_col, column, value, issue, desc):
        self.t[table].at[row, column] = value
        self.log(table, row, key_col, column, issue, value, desc)

    def duplicate(self, table, k, key_col, desc):
        df = self.t[table]
        rows = self.rows(table, k)
        dup = df.loc[rows].copy()
        for r in rows:
            self.log(table, r, key_col, key_col, "DUPLICATE_RECORD", df.at[r, key_col], desc)
        self.t[table] = pd.concat([df, dup], ignore_index=True)


def make_dirty(tables: dict[str, pd.DataFrame], seed: int):
    rng = np.random.default_rng(seed + 1)
    j = Injector(tables, rng)
    T = j.t

    # ---------------------------------------------------------------- customers
    for r in j.rows("customers", 2):
        j.set("customers", r, "customer_id", "customer_type", None, "MISSING_MANDATORY",
              "customer_type is blank")
    for r in j.rows("customers", 2):
        v = " individual" if T["customers"].at[r, "customer_type"] == "INDIVIDUAL" else "sme "
        j.set("customers", r, "customer_id", "customer_type", v, "INCONSISTENT_FORMAT",
              "Wrong case / stray whitespace in enumerated value")
    for r in j.rows("customers", 1):
        j.set("customers", r, "customer_id", "date_of_birth", "1990-13-45", "INVALID_DATE",
              "Impossible date")
    j.duplicate("customers", 3, "customer_id", "Exact duplicate customer record")

    # ---------------------------------------------------------------- facilities
    for n, r in enumerate(j.rows("facilities", 3)):
        j.set("facilities", r, "facility_id", "customer_id", f"CIF99{n:04d}",
              "ORPHAN_REFERENCE", "customer_id does not exist in customers")
    for r in j.rows("facilities", 2):
        v = -abs(float(T["facilities"].at[r, "sanctioned_amount"]))
        j.set("facilities", r, "facility_id", "sanctioned_amount", v, "NEGATIVE_AMOUNT",
              "Sanctioned amount is negative")
    for r, v in zip(j.rows("facilities", 2), ["2025-02-30", "N/A"]):
        j.set("facilities", r, "facility_id", "disbursement_date", v, "INVALID_DATE",
              "Unparseable disbursement date")
    for r in j.rows("facilities", 2):
        j.set("facilities", r, "facility_id", "currency", "USD", "CURRENCY_MISMATCH",
              "Currency differs from portfolio currency (INR)")
    for r in j.rows("facilities", 2, lambda d: d.facility_status == "ACTIVE"):
        j.set("facilities", r, "facility_id", "facility_status", "CLOSED",
              "INCONSISTENT_STATUS", "Marked CLOSED but has outstanding balance")

    # ---------------------------------------------------------------- repayments
    for n, r in enumerate(j.rows("repayments", 4)):
        j.set("repayments", r, "repayment_id", "facility_id", f"PF99{n:05d}",
              "ORPHAN_REFERENCE", "facility_id does not exist in facilities")
    for r in j.rows("repayments", 2):
        other = T["customers"].customer_id.iloc[int(rng.integers(len(T["customers"])))]
        j.set("repayments", r, "repayment_id", "customer_id", other, "RELATIONSHIP_MISMATCH",
              "customer_id differs from the facility's owner")
    for r in j.rows("repayments", 3, lambda d: d.amount_paid.astype(float) > 0):
        v = -float(T["repayments"].at[r, "amount_paid"])
        j.set("repayments", r, "repayment_id", "amount_paid", v, "NEGATIVE_AMOUNT",
              "Amount paid is negative")
    for r in j.rows("repayments", 2, lambda d: d.amount_paid.astype(float) > 0):
        v = round(float(T["repayments"].at[r, "amount_due"]) * 50, 2)
        j.set("repayments", r, "repayment_id", "amount_paid", v, "OUTLIER",
              "Amount paid is 50x the amount due")
    for r in j.rows("repayments", 3):
        j.set("repayments", r, "repayment_id", "amount_due", None, "MISSING_MANDATORY",
              "amount_due is blank")
    for r in j.rows("repayments", 6):
        d = pd.Timestamp(T["repayments"].at[r, "due_date"])
        j.set("repayments", r, "repayment_id", "due_date", d.strftime("%d/%m/%Y"),
              "INCONSISTENT_FORMAT", "Date in DD/MM/YYYY instead of ISO")
    for r in j.rows("repayments", 2, lambda d: d.payment_date.notna()):
        j.set("repayments", r, "repayment_id", "payment_date", "2019-01-01", "LOGICAL_ERROR",
              "Payment date precedes the facility's disbursement")
    for r in j.rows("repayments", 4, lambda d: d.repayment_status == "PAID_LATE"):
        j.set("repayments", r, "repayment_id", "repayment_status", "PAID_ON_TIME",
              "STATUS_MISMATCH", "Status says on time but payment date is after due date")
    j.duplicate("repayments", 5, "repayment_id", "Exact duplicate repayment record")

    # ---------------------------------------------------------------- delinquency
    key = "facility_id"
    for r in j.rows("delinquency", 3, lambda d: d.days_past_due.astype(int) > 0):
        j.set("delinquency", r, key, "overdue_amount", 0.0, "INCONSISTENT_VALUES",
              f"DPD > 0 but overdue amount is zero (snapshot "
              f"{T['delinquency'].at[r, 'snapshot_date']})")
    for r in j.rows("delinquency", 2):
        j.set("delinquency", r, key, "days_past_due", -5, "OUT_OF_RANGE",
              f"Negative DPD (snapshot {T['delinquency'].at[r, 'snapshot_date']})")
    for r in j.rows("delinquency", 2, lambda d: d.dpd_bucket == "Current"):
        j.set("delinquency", r, key, "dpd_bucket", "31-60", "INCONSISTENT_VALUES",
              f"Bucket does not match DPD (snapshot {T['delinquency'].at[r, 'snapshot_date']})")

    # ---------------------------------------------------------------- exposure
    for r in j.rows("exposure", 2, lambda d: d.bureau_score.notna()):
        j.set("exposure", r, "customer_id", "bureau_score", 1200, "OUT_OF_RANGE",
              "Bureau score outside 300-900")
    for r in j.rows("exposure", 1, lambda d: d.revolving_utilisation_pct.notna()):
        j.set("exposure", r, "customer_id", "revolving_utilisation_pct", -10.0, "OUT_OF_RANGE",
              "Negative utilisation")
    for n, r in enumerate(j.rows("exposure", 2)):
        j.set("exposure", r, "customer_id", "customer_id", f"CIF98{n:04d}", "ORPHAN_REFERENCE",
              "customer_id does not exist in customers")

    # ---------------------------------------------------------------- financials
    for r in j.rows("financials", 3):
        j.set("financials", r, "customer_id", "total_inflows", None, "MISSING_MANDATORY",
              f"total_inflows blank for {T['financials'].at[r, 'month']}")
    for r in j.rows("financials", 2):
        v = float(T["financials"].at[r, "net_cash_flow"]) + 25000
        j.set("financials", r, "customer_id", "net_cash_flow", round(v, 2),
              "INCONSISTENT_VALUES", "net_cash_flow != total_inflows - total_outflows")
    for r in j.rows("financials", 2, lambda d: d.salary_credit.notna()):
        v = f"INR {float(T['financials'].at[r, 'salary_credit']):,.2f}"
        j.set("financials", r, "customer_id", "salary_credit", v, "TEXT_IN_NUMERIC",
              "Formatted text in numeric column")
    j.duplicate("financials", 3, "customer_id", "Same customer-month reported twice")

    manifest = pd.DataFrame(j.manifest)
    manifest.insert(0, "issue_id", [f"DQ{i + 1:04d}" for i in range(len(manifest))])
    return j.t, manifest
