"""Per-customer metrics at an as-of date (the heavy, cacheable stage).

Column names match the `metric` field of the rules file. NaN = not evaluable (missing data),
which the scoring stage treats as "not evaluated", never as zero.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MONTH = pd.offsets.MonthEnd


def _month_end(ts) -> pd.Timestamp:
    return pd.Timestamp(ts) + MONTH(0)


def compute(t: dict[str, pd.DataFrame], as_of, params: dict) -> pd.DataFrame:
    as_of = pd.Timestamp(as_of)
    grace = int(params.get("grace_days", 3))
    cu = t["customers"].set_index("customer_id")
    m = pd.DataFrame(index=cu.index)

    _repayment_metrics(t, as_of, grace, params, m)
    _delinquency_metrics(t, as_of, m)
    income = _cashflow_metrics(t, cu, as_of, m)
    _exposure_metrics(t, cu, as_of, income, m)
    _completeness(t, cu, as_of, m)
    return m


# ======================================================================================
def _repayment_metrics(t, as_of, grace, params, m):
    rp = t["repayments"]
    rp = rp[(rp.due_date < as_of) & (rp.due_date > as_of - pd.DateOffset(months=12))].copy()
    paid_by = rp.payment_date.notna() & (rp.payment_date <= as_of)
    settled = paid_by & rp.repayment_status.isin(["PAID_ON_TIME", "PAID_LATE"])
    late = np.where(settled, (rp.payment_date - rp.due_date).dt.days,
                    (as_of - rp.due_date).dt.days)
    rp["late_days"] = late
    excused = settled & (rp.late_days <= grace)
    if params.get("excuse_lunar_holiday_delays", True) and "bank_holidays" in t:
        hol = t["bank_holidays"]
        lunar = hol[hol.holiday_type == "LUNAR"].holiday_date.sort_values().values
        if len(lunar):
            # A lunar holiday between due date and payment date (delay <= 5 days) is excused.
            i = np.searchsorted(lunar, rp.due_date.values)
            nxt = lunar[np.minimum(i, len(lunar) - 1)]
            hol_hit = (nxt >= rp.due_date.values) & (nxt <= rp.payment_date.fillna(as_of).values)
            excused |= settled & hol_hit & (rp.late_days <= 5)
    pending = ~settled & ((as_of - rp.due_date).dt.days <= grace)
    rp["irregular"] = ~(excused | (settled & (rp.late_days <= 0)) | pending)
    rp["partial"] = (rp.repayment_status == "PARTIALLY_PAID") & paid_by
    rp["month"] = rp.due_date.dt.to_period("M")
    mon = rp.groupby(["customer_id", "month"]).agg(irregular=("irregular", "any"),
                                                   partial=("partial", "any"))
    mon = mon.reset_index()
    cut6 = (as_of - pd.DateOffset(months=6)).to_period("M")
    last6 = mon[mon.month > cut6]
    m["late_or_missed_instalments"] = last6.groupby("customer_id").irregular.sum()
    m["partial_payments"] = last6.groupby("customer_id").partial.sum()
    g12 = mon.groupby("customer_id")
    n_irr = g12.irregular.sum()
    n_months = g12.size()
    m["_months_with_dues"] = n_months
    m["_irregular_months_12"] = n_irr
    # Isolated: exactly one irregular month in 12, >= 6 regular months, now fully current.
    still_open = rp[~settled].groupby("customer_id").size()
    isolated = (n_irr == 1) & (n_months - n_irr >= 6)
    isolated &= ~isolated.index.isin(still_open.index)
    m["isolated_delay_recovered"] = isolated.astype(float)
    m.loc[m.late_or_missed_instalments.isna() & m._months_with_dues.isna(),
          ["late_or_missed_instalments", "partial_payments"]] = np.nan
    m["late_or_missed_instalments"] = m.late_or_missed_instalments.fillna(
        0).where(m._months_with_dues.notna())
    m["partial_payments"] = m.partial_payments.fillna(0).where(m._months_with_dues.notna())
    m["isolated_delay_recovered"] = m.isolated_delay_recovered.fillna(0)

    # Consecutive missed months (for hard trigger: 2+ consecutive → ≥ Amber).
    def _consec_missed(grp):
        months = grp.sort_values("month")
        vals = months.irregular.values
        run = 0
        for v in vals[::-1]:
            if v:
                run += 1
            else:
                break
        return run
    cm = mon.groupby("customer_id").apply(_consec_missed)
    m["_consecutive_missed_months"] = cm.reindex(m.index).fillna(0)

    # Evidence near the irregular month (verified documents strengthen the mitigation).
    ev = t.get("exception_evidence")
    if ev is not None and len(ev):
        irr = rp[rp.irregular].groupby("customer_id").due_date.max()
        e = ev.merge(irr.rename("irr_date"), left_on="customer_id", right_index=True)
        near = e[(e.event_date - e.irr_date).dt.days.abs() <= 45]
        m["_evidence_verified"] = near.groupby("customer_id").verified_flag.apply(
            lambda s: (s == "Y").any())
        m["_evidence_type"] = near.groupby("customer_id").exception_type.first()


def _delinquency_metrics(t, as_of, m):
    dl = t["delinquency"]
    dl = dl[dl.snapshot_date <= as_of]
    per = dl.groupby(["customer_id", "snapshot_date"]).days_past_due.max().reset_index()
    per = per.sort_values(["customer_id", "snapshot_date"])
    last = per.groupby("customer_id").tail(1).set_index("customer_id")
    m["current_dpd"] = last.days_past_due
    m["_dpd_asof"] = last.snapshot_date

    def rises(s):
        v = s.values[-4:]
        n = 0
        for a, b in zip(v[::-1][1:], v[::-1][:-1]):
            if b > a:
                n += 1
            else:
                break
        return n
    m["dpd_increase_month_ends"] = per.groupby("customer_id").days_past_due.apply(rises)

    def since_cure(s):
        v = s.values
        if v[-1] > 0 or not (v > 0).any():
            return np.nan
        last_bad = np.where(v > 0)[0][-1]
        return len(v) - 1 - last_bad
    m["months_since_cure"] = per.groupby("customer_id").days_past_due.apply(since_cure)
    m["_max_dpd_12m"] = per[per.snapshot_date > as_of - pd.DateOffset(months=12)].groupby(
        "customer_id").days_past_due.max()
    m["_dpd_history"] = per.groupby("customer_id").days_past_due.apply(
        lambda s: s.tail(12).tolist())


def _cashflow_metrics(t, cu, as_of, m):
    fi = t["financials"]
    fi = fi[fi.month_end_date <= as_of].copy()
    fi["p"] = fi.month_end_date.dt.to_period("M")
    now = _month_end(as_of).to_period("M")
    is_sme = cu.customer_type == "SME"
    sal_vis = (cu.customer_type == "INDIVIDUAL") & (cu.salary_transfer_flag == "Y")
    fi["inc"] = np.where(fi.customer_id.map(is_sme), fi.business_receipts,
                         np.where(fi.customer_id.map(sal_vis), fi.salary_credit, np.nan))
    fi["inc"] = pd.to_numeric(fi["inc"], errors="coerce")
    wide = fi.pivot_table(index="customer_id", columns="p", values="inc", aggfunc="sum")
    bal = fi.pivot_table(index="customer_id", columns="p", values="average_balance",
                         aggfunc="mean")
    ret = fi.pivot_table(index="customer_id", columns="p", values="returned_payments_count",
                         aggfunc="sum")
    months = [now - k for k in range(24)]  # newest first

    def window(df, ks):
        cols = [months[k] for k in ks if months[k] in df.columns]
        return df[cols] if cols else pd.DataFrame(index=df.index)

    def mean_ok(df, ks, need=2):
        w = window(df, ks)
        return w.mean(axis=1).where(w.notna().sum(axis=1) >= need)

    last3, prev3 = mean_ok(wide, [0, 1, 2]), mean_ok(wide, [3, 4, 5])
    yoy3 = mean_ok(wide, [12, 13, 14])
    chg_ind = (last3 / prev3 - 1) * 100
    chg_sme = (last3 / yoy3 - 1) * 100
    sme_idx = wide.index.map(is_sme).fillna(False).astype(bool)
    m["inflow_change_3m_vs_prior_3m_pct"] = pd.Series(
        np.where(sme_idx, chg_sme, chg_ind), index=wide.index).replace([np.inf, -np.inf], np.nan)

    def consec(row):
        v = row.dropna().values  # newest first
        n = 0
        for a, b in zip(v[:-1], v[1:]):
            if a < b * 0.98:
                n += 1
            else:
                break
        return n
    recent = window(wide, range(7))
    ratio = None
    if sme_idx.any():  # year-on-year ratio removes seasonality for businesses
        lag = window(wide, range(12, 19))
        lag.columns = recent.columns[: lag.shape[1]]
        ratio = recent / lag
    cons = recent.apply(consec, axis=1)
    if ratio is not None:
        cons = cons.where(~sme_idx, ratio.apply(consec, axis=1))
    m["consecutive_inflow_declines"] = cons.where(recent.notna().sum(axis=1) >= 4)

    sal3 = window(wide, [0, 1, 2])
    no_sal = (sal3.fillna(0) <= 0).sum(axis=1)
    m["months_without_salary"] = no_sal.where(wide.index.map(sal_vis).fillna(False))
    m["avg_balance_change_pct"] = ((mean_ok(bal, [0, 1, 2]) / mean_ok(bal, [3, 4, 5]) - 1)
                                   * 100).replace([np.inf, -np.inf], np.nan)
    m["returned_payments"] = window(ret, [0, 1, 2]).sum(axis=1, min_count=1)

    income_now = last3
    income_then = mean_ok(wide, [6, 7, 8])
    declared = cu.declared_monthly_income.astype(float)
    m["_income_now"] = income_now
    m["_income_then"] = income_then
    m["_income_visible"] = (sal_vis | is_sme).astype(float)
    m["_inflow_series"] = window(wide, range(12)).apply(lambda r: r.tolist()[::-1], axis=1)
    m["_balance_series"] = window(bal, range(12)).apply(lambda r: r.tolist()[::-1], axis=1)
    # DBR income: visible salary if available, else declared income.
    inc = pd.Series(np.where(sal_vis.reindex(m.index), income_now.reindex(m.index),
                             declared.reindex(m.index)), index=m.index)
    return inc


def _exposure_metrics(t, cu, as_of, income, m):
    ex = t["exposure"]
    ex = ex[ex.snapshot_date <= as_of]
    ex = ex.sort_values(["customer_id", "snapshot_date"])
    now = ex.groupby("customer_id").tail(1).set_index("customer_id")
    then = ex[ex.snapshot_date <= as_of - pd.DateOffset(months=6)].groupby(
        "customer_id").tail(1).set_index("customer_id")
    mid = ex[ex.snapshot_date <= as_of - pd.DateOffset(months=3)].groupby(
        "customer_id").tail(1).set_index("customer_id")
    num = lambda df, c: pd.to_numeric(df[c], errors="coerce") if c in df else np.nan  # noqa

    m["revolving_utilisation_pct"] = num(now, "revolving_utilisation_pct")
    m["utilisation_change_pp"] = num(now, "revolving_utilisation_pct") - num(
        then, "revolving_utilisation_pct")
    oblig = num(now, "onus_monthly_obligation") + num(
        now, "bureau_external_monthly_obligation").fillna(0)
    dbr = (oblig / income.replace(0, np.nan) * 100).clip(upper=999)
    dbr = dbr.where(income.notna())
    dbr[income == 0] = 999
    m["debt_burden_ratio_pct"] = dbr.where(cu.customer_type == "INDIVIDUAL")
    newc = (num(now, "new_onus_facilities_3m").fillna(0) + num(mid, "new_onus_facilities_3m")
            .reindex(now.index).fillna(0)
            + (num(now, "bureau_external_facilities") - num(then, "bureau_external_facilities")
               .reindex(now.index)).clip(lower=0).fillna(0)
            + num(now, "bureau_enquiries_3m").fillna(0)
            + num(mid, "bureau_enquiries_3m").reindex(now.index).fillna(0))
    m["new_facilities_or_enquiries"] = newc
    tot_now = num(now, "onus_outstanding_total") + num(now, "bureau_external_outstanding").fillna(0)
    tot_then = num(then, "onus_outstanding_total") + num(
        then, "bureau_external_outstanding").fillna(0)
    exp_g = (tot_now / tot_then.reindex(tot_now.index) - 1) * 100
    inc_g = (m["_income_now"] / m["_income_then"] - 1) * 100
    m["exposure_growth_minus_income_growth_pp"] = (exp_g - inc_g.reindex(exp_g.index).fillna(0)
                                                   ).replace([np.inf, -np.inf], np.nan)
    m["bureau_external_max_dpd"] = num(now, "bureau_external_max_dpd")
    m["bureau_score_change"] = num(now, "bureau_score") - num(then, "bureau_score")
    m["bureau_no_hit"] = num(now, "bureau_score").reindex(m.index).isna().astype(float)
    m["_bureau_score"] = num(now, "bureau_score")
    m["_dbr_income"] = income
    m["_outstanding"] = num(now, "onus_outstanding_total")
    m["_util_series"] = ex[ex.snapshot_date > as_of - pd.DateOffset(months=12)].groupby(
        "customer_id").revolving_utilisation_pct.apply(lambda s: s.tolist())


def _completeness(t, cu, as_of, m):
    since = pd.to_datetime(cu.customer_since)
    hist = ((as_of.year - since.dt.year) * 12 + (as_of.month - since.dt.month)).clip(lower=0)
    m["months_of_history"] = hist
    fi = t["financials"]
    present = fi[(fi.month_end_date <= as_of) & (fi.month_end_date > as_of - pd.DateOffset(
        months=12))].groupby("customer_id").size()
    first_data = t["financials"].month_end_date.min()
    start = pd.concat([since, pd.Series(first_data, index=since.index)], axis=1).max(axis=1)
    expected = (((as_of.year - start.dt.year) * 12 + (as_of.month - start.dt.month)) + 1).clip(
        0, 12)
    m["missing_financial_months"] = (expected - present.reindex(m.index).fillna(0)).clip(lower=0)
    coverage = (present.reindex(m.index).fillna(0) / expected.replace(0, np.nan)).clip(0, 1)
    dcs = 100 * (0.35 * (hist / 12).clip(0, 1) + 0.30 * coverage.fillna(0)
                 + 0.15 * (1 - m.bureau_no_hit) + 0.20 * m._income_visible.reindex(m.index))
    m["data_completeness_score"] = dcs.round(0)
