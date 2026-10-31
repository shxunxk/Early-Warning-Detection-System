"""Load CSV / Excel / JSON inputs, map headers, validate, and link the tables.

Errors reject the row; warnings keep it (after normalising) but are reported.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd

TABLES = ["customers", "facilities", "repayments", "delinquency", "exposure",
          "financials", "exception_evidence", "bank_holidays"]

KEYS = {
    "customers": ["customer_id"],
    "facilities": ["facility_id"],
    "repayments": ["repayment_id"],
    "delinquency": ["facility_id", "snapshot_date"],
    "exposure": ["customer_id", "snapshot_date"],
    "financials": ["customer_id", "month"],
    "exception_evidence": ["evidence_id"],
    "bank_holidays": ["holiday_date"],
}
REQUIRED = {
    "customers": ["customer_id", "customer_type"],
    "facilities": ["facility_id", "customer_id", "product_code", "sanctioned_amount",
                   "disbursement_date"],
    "repayments": ["repayment_id", "facility_id", "due_date", "amount_due"],
    "delinquency": ["facility_id", "snapshot_date", "days_past_due"],
    "exposure": ["customer_id", "snapshot_date"],
    "financials": ["customer_id", "month", "total_inflows", "total_outflows"],
    "exception_evidence": ["customer_id", "event_date"],
    "bank_holidays": ["holiday_date"],
}
DATES = {
    "customers": ["date_of_birth", "customer_since"],
    "facilities": ["disbursement_date", "maturity_date", "closure_date"],
    "repayments": ["due_date", "payment_date"],
    "delinquency": ["snapshot_date"],
    "exposure": ["snapshot_date"],
    "financials": ["month_end_date"],
    "exception_evidence": ["event_date", "recorded_date"],
    "bank_holidays": ["holiday_date"],
}
NON_NEGATIVE = {
    "facilities": ["sanctioned_amount", "outstanding_balance"],
    "repayments": ["amount_due", "amount_paid"],
    "delinquency": ["days_past_due", "overdue_amount"],
    "financials": ["total_inflows", "total_outflows"],
}
ENUMS = {
    ("customers", "customer_type"): {"INDIVIDUAL", "SME"},
    ("facilities", "facility_type"): {"INSTALMENT", "REVOLVING"},
    ("repayments", "repayment_status"): {"PAID_ON_TIME", "PAID_LATE", "PARTIALLY_PAID",
                                         "UNPAID"},
}
# Common header variants seen in bank extracts -> canonical name.
ALIASES = {
    "cust_id": "customer_id", "cif": "customer_id", "customer_no": "customer_id",
    "client_id": "customer_id", "loan_id": "facility_id", "account_id": "facility_id",
    "facility_no": "facility_id", "dpd": "days_past_due", "due_amount": "amount_due",
    "paid_amount": "amount_paid", "paid_date": "payment_date", "emi": "instalment_amount",
    "limit": "sanctioned_amount", "sanctioned_limit": "sanctioned_amount",
    "outstanding": "outstanding_balance", "status": "repayment_status",
    "segment": "customer_segment",
    "emirate": "region", "city": "region", "state": "region", "location": "region",
}
NAME_HINTS = {  # file name fragment -> table
    "customer": "customers", "facilit": "facilities", "loan": "facilities",
    "repay": "repayments", "delinq": "delinquency", "dpd": "delinquency",
    "exposure": "exposure", "financ": "financials", "transaction": "financials",
    "evidence": "exception_evidence", "exception": "exception_evidence",
    "holiday": "bank_holidays", "calendar": "bank_holidays",
}


def _canon(col: str) -> str:
    c = re.sub(r"[^0-9a-z]+", "_", str(col).strip().lower()).strip("_")
    return ALIASES.get(c, c)


def table_for(name: str) -> str | None:
    n = name.lower()
    if n in TABLES:
        return n
    for hint, table in NAME_HINTS.items():
        if hint in n:
            return table
    return None


def read_any(name: str, payload: bytes) -> dict[str, pd.DataFrame]:
    """Return {table: frame} from one file (Excel workbooks may hold several tables)."""
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        sheets = pd.read_excel(io.BytesIO(payload), sheet_name=None, dtype=str)
        return {t: df for s, df in sheets.items() if (t := table_for(s))}
    if suffix == ".json":
        obj = json.loads(payload)
        if isinstance(obj, dict):  # {"customers": [...], ...} or {"records": [...]}
            out = {t: pd.DataFrame(v) for k, v in obj.items() if (t := table_for(k))}
            if out:
                return {k: v.astype(str).where(v.notna(), None) for k, v in out.items()}
            obj = next(iter(obj.values()))
        df = pd.DataFrame(obj)
        df = df.astype(object).where(df.notna(), None)
        return {table_for(Path(name).stem): df} if table_for(Path(name).stem) else {}
    df = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=True)
    t = table_for(Path(name).stem)
    return {t: df} if t else {}


def load_folder(folder: Path) -> dict[str, pd.DataFrame]:
    raw: dict[str, pd.DataFrame] = {}
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in (".csv", ".xlsx", ".json"):
            raw.update(read_any(p.name, p.read_bytes()))
    return raw


# ======================================================================================
# Validation
# ======================================================================================

def validate(raw: dict[str, pd.DataFrame]):
    """Return (clean tables, issues frame, summary frame)."""
    issues = []
    clean = {}

    def flag(table, df, mask, column, issue, severity, msg):
        for idx in df.index[mask]:
            key = "|".join(str(df.at[idx, k]) for k in KEYS[table] if k in df.columns)
            issues.append(dict(table=table, record_key=key, column=column,
                               issue_type=issue, severity=severity, message=msg))
        return mask

    for table in TABLES:
        if table not in raw:
            issues.append(dict(table=table, record_key="", column="", issue_type="MISSING_TABLE",
                               severity="ERROR" if table != "exception_evidence" else "WARNING",
                               message="Table not supplied"))
            continue
        df = raw[table].copy()
        df.columns = [_canon(c) for c in df.columns]
        df = df.astype(object).where(df.notna(), None)
        reject = pd.Series(False, index=df.index)

        missing_cols = [c for c in REQUIRED[table] if c not in df.columns]
        for c in missing_cols:
            issues.append(dict(table=table, record_key="", column=c, issue_type="MISSING_COLUMN",
                               severity="ERROR", message="Required column absent"))
        if missing_cols:
            continue

        for c in REQUIRED[table]:
            blank = df[c].isna() | (df[c].astype(str).str.strip() == "")
            reject |= flag(table, df, blank, c, "MISSING_MANDATORY", "ERROR", f"{c} is blank")

        # Enumerations: normalise case/whitespace (warning) or reject unknown (error).
        for (t, c), allowed in ENUMS.items():
            if t != table or c not in df.columns:
                continue
            norm = df[c].astype(str).str.strip().str.upper()
            fixable = df[c].notna() & (norm != df[c].astype(str)) & norm.isin(allowed)
            flag(table, df, fixable, c, "INCONSISTENT_FORMAT", "WARNING",
                 f"Normalised to upper case: {c}")
            bad = df[c].notna() & ~norm.isin(allowed)
            reject |= flag(table, df, bad, c, "INVALID_VALUE", "ERROR", f"Unknown {c}")
            df[c] = norm.where(df[c].notna(), None)

        # Dates: ISO first; day-first fallback is a warning; unparseable is an error.
        for c in DATES[table]:
            if c not in df.columns:
                continue
            raw_c = df[c]
            iso = pd.to_datetime(raw_c, format="ISO8601", errors="coerce")
            alt = pd.to_datetime(raw_c.where(iso.isna()), dayfirst=True, errors="coerce",
                                 format="mixed")
            flag(table, df, raw_c.notna() & iso.isna() & alt.notna(), c, "INCONSISTENT_FORMAT",
                 "WARNING", f"{c} not ISO; parsed day-first")
            bad = raw_c.notna() & iso.isna() & alt.isna()
            sev = "ERROR" if c in REQUIRED[table] else "WARNING"
            hit = flag(table, df, bad, c, "INVALID_DATE", sev, f"Unparseable {c}")
            if sev == "ERROR":
                reject |= hit
            df[c] = iso.fillna(alt)

        # Numerics.
        for c in df.columns:
            if c in DATES[table] or c in ("month",) or df[c].dtype != object:
                continue
            vals = df[c].dropna().astype(str)
            if vals.empty:
                continue
            looks_numeric = vals.str.fullmatch(r"-?[\d.]+(e[-+]?\d+)?").mean() > 0.9
            if not looks_numeric:
                continue
            num = pd.to_numeric(df[c], errors="coerce")
            text = df[c].notna() & num.isna()
            if text.any():
                cleaned = pd.to_numeric(df[c].astype(str).str.replace(r"[^\d.\-]", "", regex=True),
                                        errors="coerce")
                flag(table, df, text & cleaned.notna(), c, "TEXT_IN_NUMERIC", "WARNING",
                     f"Text stripped from {c}")
                num = num.fillna(cleaned.where(text))
            df[c] = num

        for c in NON_NEGATIVE.get(table, []):
            if c in df.columns:
                neg = pd.to_numeric(df[c], errors="coerce") < 0
                reject |= flag(table, df, neg, c, "NEGATIVE_AMOUNT", "ERROR", f"{c} < 0")

        dup = df.duplicated(KEYS[table], keep="first")
        flag(table, df, dup, ",".join(KEYS[table]), "DUPLICATE_RECORD", "WARNING",
             "Duplicate key; first occurrence kept")
        reject |= dup
        clean[table] = df[~reject].reset_index(drop=True)
        clean[table].attrs["rejected"] = int(reject.sum())
        clean[table].attrs["received"] = len(df)

    _cross_checks(clean, issues)
    issues_df = pd.DataFrame(issues, columns=["table", "record_key", "column", "issue_type",
                                              "severity", "message"])
    summary = pd.DataFrame([dict(table=t, received=clean[t].attrs.get("received", 0),
                                 rejected=clean[t].attrs.get("rejected", 0),
                                 accepted=len(clean[t]))
                            for t in TABLES if t in clean])
    return clean, issues_df, summary


def _cross_checks(t: dict[str, pd.DataFrame], issues: list):
    def drop(table, mask, column, issue, msg, severity="ERROR"):
        df = t[table]
        for idx in df.index[mask]:
            key = "|".join(str(df.at[idx, k]) for k in KEYS[table])
            issues.append(dict(table=table, record_key=key, column=column, issue_type=issue,
                               severity=severity, message=msg))
        if severity == "ERROR":
            kept = df[~mask].reset_index(drop=True)
            kept.attrs = {**df.attrs, "rejected": df.attrs.get("rejected", 0) + int(mask.sum())}
            t[table] = kept

    if "customers" not in t:
        return
    cust = set(t["customers"].customer_id)
    if "facilities" in t:
        f = t["facilities"]
        drop("facilities", ~f.customer_id.isin(cust), "customer_id", "ORPHAN_REFERENCE",
             "Unknown customer")
        f = t["facilities"]
        if "currency" in f:
            drop("facilities", f.currency.notna() & (f.currency != "INR"), "currency",
                 "CURRENCY_MISMATCH", "Non-portfolio currency", "WARNING")
        if {"facility_status", "outstanding_balance"} <= set(f.columns):
            drop("facilities", (f.facility_status == "CLOSED") & (f.outstanding_balance > 0),
                 "facility_status", "INCONSISTENT_STATUS", "CLOSED with balance", "WARNING")
    fac = t.get("facilities")
    owner = dict(zip(fac.facility_id, fac.customer_id)) if fac is not None else {}
    disb = dict(zip(fac.facility_id, fac.disbursement_date)) if fac is not None else {}
    for table in ("repayments", "delinquency"):
        if table in t:
            d = t[table]
            drop(table, ~d.facility_id.isin(owner), "facility_id", "ORPHAN_REFERENCE",
                 "Unknown facility")
            d = t[table]
            if "customer_id" in d:
                drop(table, d.customer_id != d.facility_id.map(owner), "customer_id",
                     "RELATIONSHIP_MISMATCH", "customer_id differs from facility owner; "
                     "facility owner used", "WARNING")
                d["customer_id"] = d.facility_id.map(owner)
    if "repayments" in t:
        r = t["repayments"]
        before = r.payment_date.notna() & (r.payment_date < r.facility_id.map(disb))
        drop("repayments", before, "payment_date", "LOGICAL_ERROR",
             "Payment before disbursement")
        r = t["repayments"]
        late = (r.payment_date - r.due_date).dt.days
        drop("repayments", (r.repayment_status == "PAID_ON_TIME") & (late > 0),
             "repayment_status", "STATUS_MISMATCH", "On-time status but paid late; "
             "dates take precedence", "WARNING")
        drop("repayments", r.amount_paid > 3 * r.amount_due, "amount_paid", "OUTLIER",
             "Paid > 3x due", "WARNING")
    if "delinquency" in t:
        d = t["delinquency"]
        drop("delinquency", (d.days_past_due > 0) & (d.overdue_amount <= 0), "overdue_amount",
             "INCONSISTENT_VALUES", "DPD > 0 with zero overdue", "WARNING")
    for table in ("exposure", "financials", "exception_evidence"):
        if table in t:
            d = t[table]
            drop(table, ~d.customer_id.isin(cust), "customer_id", "ORPHAN_REFERENCE",
                 "Unknown customer")
    if "exposure" in t:
        e = t["exposure"]
        if "bureau_score" in e:
            drop("exposure", e.bureau_score.notna() & ~e.bureau_score.between(300, 900),
                 "bureau_score", "OUT_OF_RANGE", "Bureau score outside 300-900; ignored",
                 "WARNING")
            t["exposure"].loc[~t["exposure"].bureau_score.between(300, 900),
                              "bureau_score"] = None
        e = t["exposure"]
        if "revolving_utilisation_pct" in e:
            drop("exposure", e.revolving_utilisation_pct < 0, "revolving_utilisation_pct",
                 "OUT_OF_RANGE", "Negative utilisation", "WARNING")
    if "financials" in t:
        fi = t["financials"]
        drop("financials", (fi.net_cash_flow - (fi.total_inflows - fi.total_outflows)).abs() > 1,
             "net_cash_flow", "INCONSISTENT_VALUES", "net != inflows - outflows; recomputed",
             "WARNING")
        t["financials"]["net_cash_flow"] = fi.total_inflows - fi.total_outflows
