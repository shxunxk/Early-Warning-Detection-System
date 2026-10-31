"""Turn a simulated Generator into the output tables (pandas DataFrames)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

import config as C
from simulate import (MONTH_ENDS, MONTHS, WINDOW_START, Customer, Facility, Generator,
                      month_index, month_start)

BAND_ORDER = ["Low", "Medium", "High", "Critical"]


def dpd_bucket(dpd: int) -> str:
    for lo, hi, name in C.DPD_BUCKETS:
        if lo <= dpd <= hi:
            return name
    return "180+"


def _remaining_principal(f: Facility, k: int) -> float:
    i = f.rate / 12
    n = f.tenure
    k = min(max(k, 0), n)
    return f.amount * ((1 + i) ** n - (1 + i) ** k) / ((1 + i) ** n - 1)


def _facility_active(f: Facility, m: int) -> bool:
    if f.disb_date > MONTH_ENDS[m]:
        return False
    return not (f.closed_date and month_index(f.closed_date) < m)


# ======================================================================================
# Facility-level month-end states
# ======================================================================================

def facility_month_states(f: Facility) -> dict[int, dict]:
    states = {}
    first_due = None if f.revolving else f.disb_date
    for m in MONTHS:
        if not _facility_active(f, m):
            continue
        d = MONTH_ENDS[m]
        overdue = 0.0
        n_over = 0
        oldest = None
        for inst in f.instalments:
            if inst.due_date >= d:  # due today is not yet overdue
                break
            gap = inst.amount_due - inst.paid_by(d)
            if gap > 0.5:
                overdue += gap
                n_over += 1
                oldest = oldest or inst.due_date
        dpd = (d - oldest).days if oldest else 0
        if f.revolving:
            outstanding = float(f.balance[m])
        else:
            k_due = (d.year * 12 + d.month) - (first_due.year * 12 + first_due.month)
            outstanding = _remaining_principal(f, k_due) + 0.85 * overdue
            if f.closed_date and f.closed_date <= d:
                outstanding = 0.0
        states[m] = dict(dpd=dpd, overdue=round(overdue, 2), n_over=n_over,
                         outstanding=round(max(outstanding, 0.0), 2))
    return states


# ======================================================================================
# Builders
# ======================================================================================

def build_tables(gen: Generator) -> dict[str, pd.DataFrame]:
    rng = gen.rng
    customers, facilities, repayments, delinquency = [], [], [], []
    exposure, financials, evidence, truth = [], [], [], []
    rp_seq = 0

    for c in sorted(gen.customers, key=lambda x: x.customer_id):
        fstates = {}
        for f in c.facilities:
            st = facility_month_states(f)
            fstates[f.facility_id] = st

            # ---------------------------------------------------------- facility row
            last = st.get(C.HISTORY_MONTHS - 1)
            status = "CLOSED" if f.closed_date else "ACTIVE"
            facilities.append(dict(
                facility_id=f.facility_id, customer_id=c.customer_id,
                product_code=f.code, product_name=f.product["name"],
                portfolio=f.product["portfolio"],
                facility_type=f.product["kind"].upper(),
                secured_flag="Y" if f.product["secured"] else "N",
                currency=C.CURRENCY,
                sanctioned_amount=f.amount,
                disbursement_date=f.disb_date,
                tenure_months=f.tenure,
                maturity_date=(f.disb_date.replace(day=1) if f.tenure is None else
                               _maturity(f)),
                interest_rate_pct=round(f.rate * 100, 2),
                instalment_amount=f.emi,
                repayment_frequency="MONTHLY",
                due_day=f.due_day,
                payment_channel=f.channel,
                outstanding_balance=0.0 if status == "CLOSED" else
                (last["outstanding"] if last else f.amount),
                facility_status=status,
                closure_date=f.closed_date,
            ))
            if f.revolving:
                facilities[-1]["maturity_date"] = None

            # ---------------------------------------------------------- repayments
            for inst in f.instalments:
                rp_seq += 1
                paid = round(inst.paid, 2)
                last_pay = max((d for d, _ in inst.allocations), default=None)
                if inst.settle_date:
                    late = max((inst.settle_date - inst.due_date).days, 0)
                    status = "PAID_ON_TIME" if late == 0 else "PAID_LATE"
                    pay_date = inst.settle_date
                elif paid > 0:
                    late = (C.AS_OF_DATE - inst.due_date).days
                    status, pay_date = "PARTIALLY_PAID", last_pay
                else:
                    late = (C.AS_OF_DATE - inst.due_date).days
                    status, pay_date = "UNPAID", None
                repayments.append(dict(
                    repayment_id=f"RP{rp_seq:08d}", facility_id=f.facility_id,
                    customer_id=c.customer_id, instalment_no=inst.no,
                    due_date=inst.due_date, amount_due=inst.amount_due,
                    payment_date=pay_date, amount_paid=paid, days_late=late,
                    repayment_status=status, payment_channel=f.channel))

            # ---------------------------------------------------------- delinquency
            hist = []
            for m, s in st.items():
                hist.append(s["dpd"])
                last12 = hist[-12:]
                delinquency.append(dict(
                    snapshot_date=MONTH_ENDS[m], facility_id=f.facility_id,
                    customer_id=c.customer_id, days_past_due=s["dpd"],
                    dpd_bucket=dpd_bucket(s["dpd"]), overdue_amount=s["overdue"],
                    overdue_instalments=s["n_over"],
                    max_dpd_12m=max(last12),
                    times_30plus_12m=sum(x >= 30 for x in last12),
                    ever_30plus_flag="Y" if max(hist) >= 30 else "N",
                    ever_60plus_flag="Y" if max(hist) >= 60 else "N",
                    ever_90plus_flag="Y" if max(hist) >= 90 else "N",
                    ifrs9_stage=3 if s["dpd"] > 90 else 2 if s["dpd"] >= 30 else 1,
                    npl_flag="Y" if s["dpd"] > 90 else "N"))

        onus_dpd = np.zeros(C.HISTORY_MONTHS)
        for st in fstates.values():
            for m, s in st.items():
                onus_dpd[m] = max(onus_dpd[m], s["dpd"])

        exp_rows, bureau_final = _exposure(c, fstates, onus_dpd, rng)
        exposure.extend(exp_rows)
        financials.extend(_financials(c, rng))
        evidence.extend(c.evidence)
        customers.append(_customer_row(c))
        truth.append(_truth_row(c, onus_dpd, exp_rows))

    ev = pd.DataFrame(evidence).sort_values("event_date").reset_index(drop=True)
    ev.insert(0, "evidence_id", [f"EV{i + 1:05d}" for i in range(len(ev))])
    ev["recorded_by"] = rng.choice(["ops.team", "collections.agent", "rm.desk"], len(ev))

    holidays = pd.DataFrame(
        [dict(holiday_date=d, holiday_name=n, holiday_type="FIXED", schedule_adjusted="Y")
         for d, n in C.FIXED_HOLIDAYS] +
        [dict(holiday_date=d, holiday_name=n, holiday_type="LUNAR", schedule_adjusted="N")
         for d, n in C.LUNAR_HOLIDAYS]).sort_values("holiday_date")

    return dict(
        customers=pd.DataFrame(customers),
        facilities=pd.DataFrame(facilities),
        repayments=pd.DataFrame(repayments),
        delinquency=pd.DataFrame(delinquency),
        exposure=pd.DataFrame(exposure),
        financials=pd.DataFrame(financials),
        exception_evidence=ev,
        bank_holidays=holidays.reset_index(drop=True),
        ground_truth=pd.DataFrame(truth),
    )


def _maturity(f: Facility) -> date:
    from simulate import add_months
    return add_months(f.disb_date, f.tenure, f.due_day)


def _customer_row(c: Customer) -> dict:
    a = c.attrs
    return dict(
        customer_id=c.customer_id, customer_name=a["customer_name"], customer_type=c.ctype,
        date_of_birth=a["date_of_birth"], employment_type=a["employment_type"],
        employment_or_business_category=a["employment_or_business_category"],
        customer_segment=a["customer_segment"], portfolio=a["portfolio"],
        emirate=a["emirate"], customer_since=month_start(c.onboard_month) +
        timedelta(days=int(c.customer_id[-2:]) % 27),
        onboarding_risk_grade=c.grade,
        salary_transfer_flag="Y" if c.salary_transfer else "N",
        declared_monthly_income=a["declared_monthly_income"],
        declared_annual_turnover=a["declared_annual_turnover"],
        years_in_business=a["years_in_business"], legal_form=a["legal_form"],
        relationship_manager_id=a["relationship_manager_id"])


# ======================================================================================
# Exposure: on-us aggregates + simulated credit bureau view
# ======================================================================================

def _exposure(c: Customer, fstates, onus_dpd, rng):
    n = C.HISTORY_MONTHS
    I = c.base_income
    ind = c.ctype == "INDIVIDUAL"
    thin = c.archetype.endswith("THIN_FILE")
    no_hit = thin and rng.random() < 0.45

    ext_count = np.full(n, float(rng.poisson(0.9 if ind else 1.2)))
    base_obl = ext_count[0] * I * rng.uniform(0.03, 0.10) if ind else \
        ext_count[0] * I * rng.uniform(0.02, 0.06)
    ext_obl = np.full(n, base_obl)
    ext_out = base_obl * rng.uniform(18, 36) * (1 - 0.012 * np.arange(n))
    card_lim = round(I * rng.uniform(0, 2.5), -3) if ind else 0.0
    card_u = np.clip(rng.uniform(0.05, 0.5) + 0.5 * c.util_stress
                     + rng.normal(0, 0.03, n), 0, 1.05)
    enquiries = rng.poisson(0.06, n).astype(float)
    for f in c.facilities:
        om = f.open_month
        if 0 <= om < n:
            enquiries[max(om - 1, 0)] += 1
    if c.archetype in ("OVER_LEVERAGED", "SME_OVER_LEVERAGED"):
        for _ in range(int(rng.integers(1, 4))):
            m = min(c.onset + int(rng.integers(0, 9)), n - 1)
            add = I * rng.uniform(0.05, 0.12) * (1 if ind else 0.5)
            ext_count[m:] += 1
            ext_obl[m:] += add
            ext_out[m:] += add * rng.uniform(20, 40)
            enquiries[max(m - 1, 0):m + 1] += rng.integers(1, 3)
        if ind:
            card_lim += round(I * rng.uniform(1, 2), -3)
    ext_dpd = np.zeros(n)
    leaders = ("REPAYMENT_DETERIORATION", "ROLL_FORWARD", "SME_CASHFLOW_STRESS",
               "SME_REPAYMENT_DETERIORATION", "LEGACY_NPL", "SME_LEGACY_NPL")
    if (c.archetype in leaders and rng.random() < 0.4) or \
            (c.subtype == "JOB_LOSS" and rng.random() < 0.3):
        # Off-us delinquency often shows up a month or two before ours does.
        start = c.onset - int(rng.integers(1, 3))
        k = rng.uniform(0.4, 0.9)
        ext_dpd = np.clip(np.where(np.arange(n) >= start,
                                   30 * k * (np.arange(n) - start + 1), 0), 0, 120).round()
    enq3 = np.array([enquiries[max(0, m - 2):m + 1].sum() for m in range(n)])
    base_score = {"A": 790, "B": 740, "C": 690, "D": 645, "E": 600}[c.grade] + \
        rng.normal(0, 15)

    rows = []
    for m in range(c.first_month, n):
        act = [(f, fstates[f.facility_id][m]) for f in c.facilities
               if m in fstates[f.facility_id]]
        rev = [(f, s) for f, s in act if f.revolving]
        rev_lim = sum(f.amount for f, _ in rev)
        rev_bal = sum(s["outstanding"] for _, s in rev)
        rev_util = rev_bal / rev_lim if rev_lim else None
        obligation = sum(
            f.emi if not f.revolving else
            (0.05 * f.amount if f.code == "CC" else s["outstanding"] * f.rate / 12)
            for f, s in act)
        new3 = sum(1 for f in c.facilities if m - 2 <= f.open_month <= m and f.open_month >= 0)
        score = (base_score - 5 * enq3[m] - 0.7 * max(onus_dpd[m], ext_dpd[m])
                 - 80 * max(0, card_u[m] - 0.7) - 40 * max(0, (rev_util or 0) - 0.8)
                 + rng.normal(0, 4))
        row = dict(
            snapshot_date=MONTH_ENDS[m], customer_id=c.customer_id,
            onus_active_facilities=len(act),
            onus_sanctioned_total=round(sum(f.amount for f, _ in act), 2),
            onus_outstanding_total=round(sum(s["outstanding"] for _, s in act), 2),
            onus_monthly_obligation=round(obligation, 2),
            revolving_limit_total=round(rev_lim, 2),
            revolving_balance_total=round(rev_bal, 2),
            revolving_utilisation_pct=None if rev_util is None else round(100 * rev_util, 1),
            new_onus_facilities_3m=new3,
            bureau_external_facilities=int(ext_count[m]),
            bureau_external_outstanding=round(float(ext_out[m]), 2),
            bureau_external_monthly_obligation=round(float(ext_obl[m]), 2),
            bureau_external_card_limit=card_lim,
            bureau_external_card_utilisation_pct=(round(100 * card_u[m], 1)
                                                  if card_lim else None),
            bureau_enquiries_3m=int(enq3[m]),
            bureau_external_max_dpd=int(ext_dpd[m]),
            bureau_score=int(np.clip(round(score), 300, 900)),
        )
        if no_hit:
            for k in list(row):
                if k.startswith("bureau_"):
                    row[k] = None
        rows.append(row)
    return rows, None


# ======================================================================================
# Account financials (monthly aggregates of the customer's operating/current account)
# ======================================================================================

def _financials(c: Customer, rng) -> list[dict]:
    n = C.HISTORY_MONTHS
    I = c.base_income
    pay_m = np.zeros(n)
    missed_dd = np.zeros(n)
    disb_m = np.zeros(n)
    for f in c.facilities:
        for when, amt in f.payments:
            m = month_index(when)
            if 0 <= m < n:
                pay_m[m] += amt
        if f.channel in ("DIRECT_DEBIT", "CHEQUE"):
            for inst in f.instalments:
                if inst.paid_by(inst.due_date + timedelta(days=5)) < inst.amount_due - 0.5:
                    missed_dd[inst.month] += 1
        om = f.open_month
        if 0 <= om < n and f.code in ("PF", "TL"):
            disb_m[om] += f.amount

    salary = c.income.copy()
    if c.event and c.event["kind"] == "SALARY_DELAY":
        e = c.event["month"]
        if e + 1 < n:
            salary[e + 1] += salary[e]
            salary[e] = 0.0

    gaps = set()
    if c.archetype == "SALARY_ELSEWHERE":
        gaps = set(rng.choice(range(c.first_month, n), int(rng.integers(1, 4)), replace=False))
    elif rng.random() < 0.03:
        gaps = {int(rng.integers(c.first_month, n))}

    ind = c.ctype == "INDIVIDUAL"
    spend_ratio = rng.uniform(0.55, 0.80) if ind else rng.uniform(0.84, 0.95)
    if ind:
        bal = I * (rng.uniform(0.3, 3.0) if c.salary_transfer else rng.uniform(0.05, 0.3))
    else:
        bal = I * rng.uniform(0.3, 1.5)
    ticket = rng.uniform(4000, 20000)
    savings_cap = rng.uniform(1.5, 5.0)

    rows = []
    for m in range(c.first_month, n):
        s = float(c.stress[m])
        opening = bal
        returned = int(rng.poisson(0.01 + 0.3 * s) + missed_dd[m])
        if ind:
            if c.salary_transfer:
                sal = float(salary[m])
                other = I * rng.lognormal(-2.6, 0.8)
                spend = spend_ratio * (0.75 * c.expected_income[m] + 0.25 * c.income[m]) \
                    * rng.normal(1, 0.06)
            else:
                sal = None
                other = pay_m[m] * rng.uniform(1.0, 1.15) + I * rng.uniform(0, 0.08)
                spend = (other - pay_m[m]) * rng.uniform(0.85, 1.05)
            receipts = None
            inflows = (sal or 0.0) + other + disb_m[m]
            in_cnt = int(rng.poisson(3)) + (1 if sal else 0)
            out_cnt = int(rng.poisson(30 * (1 + 0.3 * s)))
        else:
            sal = None
            receipts = float(c.income[m])
            other = I * rng.uniform(0.1, 0.3) if (s > 0.2 and rng.random() < 0.3) else \
                I * rng.lognormal(-4, 1)
            spend = spend_ratio * (0.6 * c.expected_income[m] + 0.4 * receipts) \
                * rng.normal(1, 0.04)
            inflows = receipts + other + disb_m[m]
            in_cnt = int(rng.poisson(max(receipts / ticket, 3)))
            out_cnt = int(rng.poisson(45 * (1 + 0.2 * s)))
            returned += int(rng.poisson(1.2 * s))
        # Stressed customers are servicing other debts / drawing cash, which is why they
        # miss our instalments: their accounts drain rather than accumulate.
        spend *= 1 + 0.8 * s
        outflows = spend + pay_m[m] + disb_m[m] * rng.uniform(0.5, 1.0)
        closing = opening + inflows - outflows
        cap = I * (savings_cap if ind else 2.0)
        if closing > cap and s == 0:
            # Surplus is swept to savings/investments held elsewhere.
            sweep = (closing - cap) * rng.uniform(0.5, 0.9)
            outflows += sweep
            closing -= sweep
        if closing < 0:
            # Account cannot go negative: some payments bounce instead.
            floor = I * rng.uniform(0.0, 0.02)
            outflows += closing - floor
            closing = floor
            returned += 1 + int(rng.poisson(0.5))
        bal = closing
        if m in gaps:
            continue
        rows.append(dict(
            month=month_start(m).strftime("%Y-%m"), month_end_date=MONTH_ENDS[m],
            customer_id=c.customer_id,
            salary_credit=None if sal is None else round(sal, 2),
            business_receipts=None if receipts is None else round(receipts, 2),
            other_inflows=round(other + disb_m[m], 2),
            total_inflows=round(inflows, 2), total_outflows=round(outflows, 2),
            net_cash_flow=round(inflows - outflows, 2),
            opening_balance=round(opening, 2), closing_balance=round(closing, 2),
            average_balance=round(max(0.5 * (opening + closing) * rng.uniform(0.9, 1.1), 0), 2),
            minimum_balance=round(min(opening, closing) * rng.uniform(0.2, 0.8), 2),
            inflow_txn_count=in_cnt, outflow_txn_count=out_cnt,
            returned_payments_count=returned))
    return rows


# ======================================================================================
# Ground truth (generator intent; NOT an input to the application)
# ======================================================================================

NOTES = {
    "STABLE": "Regular payer, stable salary; occasional 1-2 day slips only.",
    "ISOLATED_EXCEPTION": "One delayed/missed instalment with an explanation, then recovered.",
    "CHRONIC_LATE_PAYER": "Habitually pays 2-9 days late every month but always pays in full.",
    "REPAYMENT_DETERIORATION": "Lateness grows month on month, then partials and misses.",
    "INCOME_DECLINE": "Salary cut, job loss or gradual decline; repayments lag the income drop.",
    "OVER_LEVERAGED": "New on-us and off-us credit, rising utilisation and debt burden.",
    "ROLL_FORWARD": "Moves from current into 1-30 and on to 31-60/61-90 DPD.",
    "CURED": "Had a 2-3 month delinquency episode early in the window, fully cured since.",
    "THIN_FILE": "New-to-bank, only a few months of history; may be bureau no-hit.",
    "SALARY_ELSEWHERE": "Salary credited at another bank; sparse account data with feed gaps.",
    "SME_STABLE": "Stable receipts with normal seasonality.",
    "SME_SEASONAL": "Strong summer dip (3+ months falling) that recovers each autumn.",
    "SME_CASHFLOW_STRESS": "Sales receipts falling for months, WC limit maxed, cheque returns.",
    "SME_OVER_LEVERAGED": "Additional term/equipment debt on-us and off-us.",
    "SME_REPAYMENT_DETERIORATION": "Business repayments deteriorating, mild receipts decline.",
    "SME_THIN_FILE": "New business / new-to-bank, little history.",
    "LEGACY_NPL": "Defaulted early in (or before) the window; 90+ DPD, token payments only.",
    "SME_LEGACY_NPL": "Business in default since early in the window; collections stage.",
}


def _truth_row(c: Customer, onus_dpd, exp_rows) -> dict:
    a = c.archetype
    cfg = {**C.INDIVIDUAL_ARCHETYPES, **C.SME_ARCHETYPES}[a]
    band = cfg["band"]
    scen = list(cfg["scenarios"])
    if c.cluster_id:
        scen.append(8)
    final_dpd = int(onus_dpd[-1])
    peak_dpd = int(onus_dpd.max())
    if a == "INCOME_DECLINE" and c.subtype == "JOB_LOSS":
        band = "Medium" if c.event["rehired"] else "High"
    if a in ("OVER_LEVERAGED", "SME_OVER_LEVERAGED") and exp_rows:
        last = exp_rows[-1]
        dbr = (last["onus_monthly_obligation"]
               + (last["bureau_external_monthly_obligation"] or 0)) / c.base_income
        if dbr > (0.6 if a == "OVER_LEVERAGED" else 0.35):
            band = "High"
    if a == "SME_CASHFLOW_STRESS" and c.onset >= C.HISTORY_MONTHS - 3:
        band = "Medium"
    rank = BAND_ORDER.index(band)
    if final_dpd >= 61:
        rank = 3
    elif final_dpd >= 31:
        rank = max(rank, 2)
    elif final_dpd >= 1:
        rank = max(rank, 1)
    sufficiency = ("INSUFFICIENT" if a.endswith("THIN_FILE") else
                   "PARTIAL" if a == "SALARY_ELSEWHERE" or not c.salary_transfer and
                   c.ctype == "INDIVIDUAL" else "SUFFICIENT")
    return dict(
        customer_id=c.customer_id, customer_type=c.ctype, archetype=a,
        archetype_subtype=c.subtype, scenarios="|".join(f"S{s}" for s in scen),
        cluster_id=c.cluster_id,
        onset_month=None if c.onset is None else month_start(c.onset).strftime("%Y-%m"),
        expected_risk_band=BAND_ORDER[rank], expected_data_sufficiency=sufficiency,
        current_max_dpd=final_dpd, peak_dpd_in_window=peak_dpd, notes=NOTES[a])
