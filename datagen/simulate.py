"""Simulation engine for the synthetic EWS portfolio.

Flow per customer:
  attributes + archetype -> monthly income & stress paths -> facilities ->
  payment decisions per instalment -> FIFO allocation of payments to instalments ->
  month-end delinquency snapshots -> exposure (on-us + bureau) -> account financials.

Month index m runs 0..HISTORY_MONTHS-1 (0 = first month of the window). Negative indices
mean "before the window" (e.g. a loan disbursed three years ago).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

import config as C

# ======================================================================================
# Calendar helpers
# ======================================================================================

_first = C.AS_OF_DATE.replace(day=1)
_y, _m = divmod(_first.year * 12 + _first.month - 1 - (C.HISTORY_MONTHS - 1), 12)
WINDOW_START = date(_y, _m + 1, 1)
FIXED_HOLIDAYS = {d for d, _ in C.FIXED_HOLIDAYS}
LUNAR_HOLIDAYS = {d for d, _ in C.LUNAR_HOLIDAYS}


def add_months(d: date, n: int, day: int | None = None) -> date:
    y, mo = divmod(d.year * 12 + d.month - 1 + n, 12)
    mo += 1
    last = calendar.monthrange(y, mo)[1]
    return date(y, mo, min(day or d.day, last))


def month_start(m: int) -> date:
    return add_months(WINDOW_START, m, 1)


def month_end(m: int) -> date:
    s = month_start(m)
    return s.replace(day=calendar.monthrange(s.year, s.month)[1])


def month_index(d: date) -> int:
    return (d.year - WINDOW_START.year) * 12 + d.month - WINDOW_START.month


def is_business_day(d: date, lunar: bool) -> bool:
    if d.weekday() >= 5 or d in FIXED_HOLIDAYS:
        return False
    return not (lunar and d in LUNAR_HOLIDAYS)


def next_business_day(d: date, lunar: bool) -> date:
    while not is_business_day(d, lunar):
        d += timedelta(days=1)
    return d


MONTHS = range(C.HISTORY_MONTHS)
MONTH_ENDS = [month_end(m) for m in MONTHS]
LUNAR_HOLIDAY_MONTHS = sorted({month_index(d) for d in LUNAR_HOLIDAYS})

# ======================================================================================
# Domain objects
# ======================================================================================


@dataclass
class Instalment:
    no: int
    due_date: date
    amount_due: float
    month: int
    allocations: list = field(default_factory=list)  # [(date, amount)]
    settle_date: date | None = None

    @property
    def paid(self) -> float:
        return sum(a for _, a in self.allocations)

    def paid_by(self, d: date) -> float:
        return sum(a for when, a in self.allocations if when <= d)


@dataclass
class Facility:
    facility_id: str
    customer_id: str
    code: str
    amount: float          # sanctioned amount or limit
    rate: float            # annual
    tenure: int | None
    disb_date: date
    due_day: int
    channel: str
    is_primary: bool = False
    emi: float | None = None
    u0: float = 0.0        # baseline utilisation for revolving
    balance: np.ndarray | None = None
    instalments: list = field(default_factory=list)
    payments: list = field(default_factory=list)  # [(date, amount)]
    closed_date: date | None = None

    @property
    def product(self) -> dict:
        return C.PRODUCTS[self.code]

    @property
    def revolving(self) -> bool:
        return self.product["kind"] == "revolving"

    @property
    def open_month(self) -> int:
        return month_index(self.disb_date)


@dataclass
class Customer:
    customer_id: str
    ctype: str
    archetype: str
    attrs: dict
    base_income: float
    onboard_month: int
    salary_transfer: bool
    grade: str
    onset: int | None = None
    subtype: str | None = None
    cluster_id: str | None = None
    event: dict | None = None
    income: np.ndarray | None = None
    stress: np.ndarray | None = None
    util_stress: np.ndarray | None = None
    expected_income: np.ndarray | None = None
    facilities: list = field(default_factory=list)
    evidence: list = field(default_factory=list)

    @property
    def first_month(self) -> int:
        return max(0, self.onboard_month)


# ======================================================================================
# Generator
# ======================================================================================


class Generator:
    def __init__(self, n_customers: int, seed: int):
        self.n = n_customers
        self.rng = np.random.default_rng(seed)
        self.customers: list[Customer] = []
        self._fac_seq = 0
        self._rp_seq = 0

    # ---------------------------------------------------------------- small utilities
    def pick(self, weights: dict):
        keys = list(weights)
        w = np.array([v[0] if isinstance(v, tuple) else (v["weight"] if isinstance(v, dict)
                      else v) for v in weights.values()], dtype=float)
        return keys[self.rng.choice(len(keys), p=w / w.sum())]

    def person_name(self) -> str:
        """Given and family name from the same pool, so the pairing stays plausible."""
        w = np.array([p[0] for p in C.NAME_POOLS], dtype=float)
        _, given, family = C.NAME_POOLS[self.rng.choice(len(C.NAME_POOLS), p=w / w.sum())]
        return f"{self.rng.choice(given)} {self.rng.choice(family)}"

    def u(self, lo, hi):
        return float(self.rng.uniform(lo, hi))

    def ri(self, lo, hi):  # inclusive
        return int(self.rng.integers(lo, hi + 1))

    def ramp(self, onset, start, step, cap):
        m = np.arange(C.HISTORY_MONTHS)
        r = np.clip(start + step * (m - onset), 0, cap)
        return np.where(m >= onset, r, 0.0)

    # ================================================================ customers
    def build(self):
        n_sme = int(round(self.n * C.SME_SHARE))
        for i in range(self.n):
            ctype = "SME" if i >= self.n - n_sme else "INDIVIDUAL"
            self.customers.append(self._new_customer(f"CIF{C.ID_START + i}", ctype))
        self.rng.shuffle(self.customers)
        self._apply_clusters()
        for c in self.customers:
            self._plan_behaviour(c)
            self._income_path(c)
            self._facilities(c)
            for f in c.facilities:
                self._simulate_facility(c, f)
            self._evidence(c)
        return self

    def _new_customer(self, cid: str, ctype: str) -> Customer:
        rng = self.rng
        if ctype == "INDIVIDUAL":
            archetype = self.pick(C.INDIVIDUAL_ARCHETYPES)
            sector = self.pick(C.EMPLOYER_SECTORS)
            _, median, sigma = C.EMPLOYER_SECTORS[sector]
            income = float(np.clip(rng.lognormal(np.log(median), sigma), 12_000, 600_000))
            income = round(income, -2)
            region = self.pick(C.REGIONS)
            segment = ("Mass" if income < 40_000 else
                       "Mass Affluent" if income < 100_000 else "Affluent")
            age = self.ri(23, 62)
            dob = date(C.AS_OF_DATE.year - age, self.ri(1, 12), self.ri(1, 28))
            attrs = dict(
                customer_name=self.person_name(),
                date_of_birth=dob,
                employment_type="SELF_EMPLOYED" if sector.startswith("Self") else "SALARIED",
                employment_or_business_category=sector,
                customer_segment=segment,
                portfolio="Consumer Banking",
                region=region,
                declared_monthly_income=round(income * self.u(1.0, 1.12), -2),
                declared_annual_turnover=None,
                years_in_business=None,
                legal_form=None,
                relationship_manager_id=(f"RM-{self.ri(1, 12):02d}"
                                         if segment != "Mass" else None),
            )
            salary_transfer = archetype != "SALARY_ELSEWHERE" and (
                sector.startswith("Self") is False and rng.random() < 0.88)
            if archetype == "SALARY_ELSEWHERE":
                salary_transfer = False
        else:
            archetype = self.pick(C.SME_ARCHETYPES)
            if archetype == "SME_SEASONAL":
                industry = str(rng.choice(["Restaurants & Hospitality", "Retail"]))
            else:
                industry = self.pick(C.SME_INDUSTRIES)
            size = self.pick(C.SME_SIZE)
            _, lo, hi = C.SME_SIZE[size]
            income = round(float(np.exp(self.u(np.log(lo), np.log(hi)))), -3)
            region = (self.pick(C.CONSTRUCTION_REGIONS)
                      if industry == "Construction & Contracting" else self.pick(C.REGIONS))
            prefix = str(rng.choice(C.SME_NAME_PARTS[0]))
            suffix = str(rng.choice(C.SME_NAME_PARTS[1][industry]))
            attrs = dict(
                customer_name=f"{prefix} {suffix}",
                date_of_birth=None,
                employment_type="BUSINESS",
                employment_or_business_category=industry,
                customer_segment=size,
                portfolio="Business Banking",
                region=region,
                declared_monthly_income=None,
                declared_annual_turnover=round(income * 12 * self.u(0.95, 1.25), -3),
                years_in_business=self.ri(1, 25),
                legal_form=str(rng.choice(C.LEGAL_FORMS, p=C.LEGAL_FORM_WEIGHTS)),
                relationship_manager_id=f"RM-{self.ri(13, 24):02d}",
            )
            salary_transfer = False

        thin = archetype in ("THIN_FILE", "SME_THIN_FILE")
        onboard_month = self.ri(19, 22) if thin else self.ri(-120, -2)
        if ctype == "SME" and not thin:
            attrs["years_in_business"] = max(attrs["years_in_business"],
                                             int(np.ceil(-onboard_month / 12)))
        if thin and ctype == "SME":
            attrs["years_in_business"] = self.ri(0, 2)

        risky = archetype in ("REPAYMENT_DETERIORATION", "ROLL_FORWARD", "OVER_LEVERAGED",
                              "CHRONIC_LATE_PAYER", "SME_REPAYMENT_DETERIORATION",
                              "SME_OVER_LEVERAGED")
        grade_w = [0.15, 0.30, 0.30, 0.18, 0.07] if risky else [0.30, 0.35, 0.22, 0.10, 0.03]
        grade = str(rng.choice(list("ABCDE"), p=grade_w))
        return Customer(cid, ctype, archetype, attrs, income, onboard_month,
                        salary_transfer, grade)

    def _apply_clusters(self):
        for cl in C.CLUSTERS:
            for c in self.customers:
                if c.ctype != cl["customer_type"] or c.archetype.endswith("THIN_FILE"):
                    continue
                cat = c.attrs["employment_or_business_category"]
                if cat != cl.get("industry", cl.get("sector")):
                    continue
                if c.attrs["region"] not in cl["regions"] or self.rng.random() > cl["share"]:
                    continue
                c.archetype = cl["archetype"]
                c.cluster_id = cl["cluster_id"]
                c.onset = self.ri(*cl["onset_months"])

    # ================================================================ behaviour plan
    def _plan_behaviour(self, c: Customer):
        n = C.HISTORY_MONTHS
        s = np.zeros(n)
        us = np.clip(self.rng.normal(0, 0.05, n).cumsum() * 0.3, -0.1, 0.1)
        a = c.archetype
        onset = c.onset

        if a == "ISOLATED_EXCEPTION":
            c.subtype = str(self.rng.choice(
                ["SALARY_DELAY", "PROCESSING_DELAY", "TEMPORARY_HARDSHIP", "HOLIDAY_PERIOD"],
                p=[0.40, 0.25, 0.20, 0.15]))
            if c.subtype == "HOLIDAY_PERIOD":
                e = int(self.rng.choice([m for m in LUNAR_HOLIDAY_MONTHS if 8 <= m <= 21]))
            else:
                e = self.ri(8, 21)
            c.onset = e
            c.event = dict(month=e, kind=c.subtype)
        elif a in ("REPAYMENT_DETERIORATION", "SME_REPAYMENT_DETERIORATION"):
            onset = onset if onset is not None else self.ri(11, 19)
            s = self.ramp(onset, 0.12, self.u(0.06, 0.12), self.u(0.6, 0.9))
            us = us + self.ramp(onset, 0.1, 0.1, 0.9)
        elif a == "INCOME_DECLINE":
            onset = onset if onset is not None else self.ri(13, 20)
            c.subtype = str(self.rng.choice(["PAY_CUT", "JOB_LOSS", "GRADUAL"],
                                            p=[0.40, 0.30, 0.30]))
            if c.subtype == "JOB_LOSS":
                gap, rehired = self.ri(2, 5), self.rng.random() < 0.6
                c.event = dict(kind="JOB_LOSS", month=onset, gap=gap, rehired=rehired,
                               new_pay=self.u(0.6, 0.85))
                # Savings carry the first months; arrears build once they are gone.
                s = self.ramp(onset + 2, 0.1, 0.12, 0.75)
                if rehired:
                    s[onset + gap + 1:] *= 0.4
            else:
                s = self.ramp(onset + 2, 0.05, 0.05, 0.35)
            us = us + self.ramp(onset, 0.15, 0.12, 0.85)
        elif a in ("OVER_LEVERAGED", "SME_OVER_LEVERAGED"):
            onset = onset if onset is not None else self.ri(8, 17)
            s = self.ramp(onset + 6, 0.05, 0.03, 0.25)
            us = us + self.ramp(onset, 0.1, 0.1 if a == "OVER_LEVERAGED" else 0.08, 0.95)
        elif a == "ROLL_FORWARD":
            onset = onset if onset is not None else self.ri(17, 21)
            s = self.ramp(onset, 0.35, 0.0, 0.35)  # non-primary facilities
            us = us + self.ramp(onset - 2, 0.2, 0.2, 0.9)
        elif a == "CURED":
            e, d = self.ri(4, 11), self.ri(2, 3)
            c.onset = e
            c.event = dict(month=e, duration=d, kind="DELINQUENCY_EPISODE")
            us = us + np.where((np.arange(n) >= e - 1) & (np.arange(n) < e + d + 2), 0.4, 0)
        elif a == "SME_CASHFLOW_STRESS":
            onset = onset if onset is not None else self.ri(14, 20)
            s = self.ramp(onset + 2, 0.08, 0.08, 0.55)
            us = us + self.ramp(onset, 0.2, 0.2, 1.0)
        elif a in ("LEGACY_NPL", "SME_LEGACY_NPL"):
            # Defaulted in the past year and never cured: the NPL stock still in collections
            # (older unsecured defaults would normally have been written off at ~180 DPD).
            onset = onset if onset is not None else (
                self.ri(14, 19) if a == "LEGACY_NPL" else self.ri(8, 16))
            s = self.ramp(onset, 0.6, 0.1, 0.95)
            us = us + self.ramp(onset - 2, 0.4, 0.2, 1.0)
        if a not in ("ISOLATED_EXCEPTION", "CURED"):
            c.onset = onset
        c.stress = s
        c.util_stress = np.clip(us, 0, 1)

    def _income_path(self, c: Customer):
        n = C.HISTORY_MONTHS
        rng = self.rng
        m = np.arange(n)
        cal_month = np.array([month_start(i).month for i in m])
        if c.ctype == "INDIVIDUAL":
            self_emp = c.attrs["employment_type"] == "SELF_EMPLOYED"
            raise_mult = np.ones(n)
            for i in m:
                if cal_month[i] == 1 and rng.random() < 0.5:
                    raise_mult[i:] *= self.u(1.02, 1.06)
            noise = rng.normal(1, 0.25 if self_emp else 0.02, n).clip(0.4, 1.6)
            inc = c.base_income * raise_mult * noise
            if c.archetype == "INCOME_DECLINE":
                o = c.onset
                if c.subtype == "PAY_CUT":
                    inc[o:] *= self.u(0.55, 0.75)
                elif c.subtype == "JOB_LOSS":
                    ev = c.event
                    inc[o:o + ev["gap"]] = 0
                    inc[o + ev["gap"]:] *= ev["new_pay"] if ev["rehired"] else 0
                else:
                    inc[o:] *= (1 - self.u(0.05, 0.09)) ** (m[o:] - o + 1)
            elif c.archetype == "REPAYMENT_DETERIORATION":
                inc[c.onset:] *= 0.99 ** (m[c.onset:] - c.onset + 1)
            elif c.archetype == "LEGACY_NPL":
                inc[c.onset:] *= self.u(0.0, 0.5)  # job lost or income collapsed
        else:
            industry = c.attrs["employment_or_business_category"]
            _, profile, vol = C.SME_INDUSTRIES[industry]
            season = np.array([C.SEASONALITY[profile][cm - 1] for cm in cal_month])
            if c.archetype == "SME_SEASONAL":
                season = 1 + (season - 1) * 1.6
            growth = (1 + self.u(-0.02, 0.10) / 12) ** m
            inc = c.base_income * season * growth * rng.lognormal(0, vol, n)
            o = c.onset
            if c.archetype == "SME_CASHFLOW_STRESS":
                g = self.u(0.05, 0.12)
                inc[o:] *= np.maximum((1 - g) ** (m[o:] - o + 1), 0.35)
            elif c.archetype == "SME_REPAYMENT_DETERIORATION":
                inc[o:] *= 0.98 ** (m[o:] - o + 1)
            elif c.archetype == "SME_LEGACY_NPL":
                inc[o:] *= self.u(0.15, 0.45)
        inc = np.round(inc, 2)
        inc[: c.first_month] = np.nan
        c.income = inc
        # What income would have been without the archetype shock (used for sticky spend).
        if c.ctype == "SME":
            c.expected_income = c.base_income * season * growth
        else:
            c.expected_income = c.base_income * raise_mult

    # ================================================================ facilities
    def _new_facility(self, c: Customer, code: str, amount: float, open_m: int,
                      channel: str | None = None) -> Facility:
        p = C.PRODUCTS[code]
        self._fac_seq += 1
        rate = round(self.u(*p["rate"]), 4)
        tenure = int(self.rng.choice([t for t in (12, 24, 36, 48, 60, 180, 240, 300)
                                      if p["tenure"][0] <= t <= p["tenure"][1]])) \
            if p["tenure"] else None
        if p["kind"] == "instalment":
            # Keep the loan alive in the window: disbursed no earlier than tenure-6 months ago.
            open_m = max(open_m, -(tenure - 6))
        disb = add_months(WINDOW_START, open_m, self.ri(1, 28))
        due_day = int(self.rng.choice([1, 2, 5, 10, 15, 20, 25, 28],
                                      p=[0.16, 0.06, 0.14, 0.14, 0.12, 0.12, 0.12, 0.14]))
        if channel is None:
            if c.archetype == "CHRONIC_LATE_PAYER":
                channel = str(self.rng.choice(["MANUAL_TRANSFER", "CHEQUE"]))
            elif c.ctype == "SME":
                channel = str(self.rng.choice(["CHEQUE", "NACH_DEBIT", "MANUAL_TRANSFER"],
                                              p=[0.45, 0.25, 0.30]))
            elif c.salary_transfer:
                channel = str(self.rng.choice(["NACH_DEBIT", "STANDING_INSTRUCTION",
                                               "UPI_AUTOPAY", "MANUAL_TRANSFER"],
                                              p=[0.6, 0.15, 0.10, 0.15]))
            else:
                channel = str(self.rng.choice(["MANUAL_TRANSFER", "CHEQUE",
                                               "STANDING_INSTRUCTION"], p=[0.5, 0.35, 0.15]))
        f = Facility(f"{code}{400000 + self._fac_seq:07d}", c.customer_id, code,
                     round(amount, -3), rate, tenure, disb, due_day, channel)
        if p["kind"] == "instalment":
            i = rate / 12
            f.emi = round(f.amount * i / (1 - (1 + i) ** -tenure), 2)
        else:
            f.u0 = self.u(0.05, 0.45) if code == "CC" else self.u(0.40, 0.75)
        c.facilities.append(f)
        return f

    def _facilities(self, c: Customer):
        I = c.base_income
        rng = self.rng
        thin = c.archetype.endswith("THIN_FILE")

        def open_month(kind):
            if thin:
                return c.onboard_month + self.ri(0, 1)
            lo = max(c.onboard_month, -96 if kind == "HF" else -60)
            return self.ri(lo, 3)

        if c.ctype == "INDIVIDUAL":
            plan = []
            if rng.random() < 0.55:
                plan.append(("PF", I * self.u(6, 18)))
            if rng.random() < 0.65:
                plan.append(("CC", float(np.clip(I * self.u(1.5, 3), 5000, 150000))))
            if rng.random() < 0.25:
                plan.append(("AF", min(I * self.u(4, 10), 300000)))
            if rng.random() < (0.12 if I > 20000 else 0.02):
                plan.append(("HF", I * self.u(60, 150)))
            if not plan:
                plan.append(("PF", I * self.u(6, 14)) if rng.random() < 0.6
                            else ("CC", max(I * 2, 5000)))
            if c.archetype == "ROLL_FORWARD" and not any(k in ("PF", "CC") for k, _ in plan):
                plan.append(("PF", I * self.u(8, 16)))
        else:
            plan = []
            if rng.random() < 0.70:
                plan.append(("TL", I * self.u(1.5, 5)))
            if rng.random() < 0.65 or c.archetype == "SME_CASHFLOW_STRESS":
                plan.append(("WC", I * self.u(0.8, 2)))
            if rng.random() < 0.25:
                plan.append(("EF", I * self.u(0.5, 2)))
            if not plan:
                plan.append(("TL", I * self.u(1.5, 4)))

        for code, amt in plan:
            self._new_facility(c, code, amt, open_month(code))

        # Keep the starting debt burden realistic (UAE cap is 50% of salary).
        if c.ctype == "INDIVIDUAL":
            obligation = sum(f.emi or 0.05 * f.amount for f in c.facilities)
            if obligation > 0.45 * I:
                k = 0.45 * I / obligation
                for f in c.facilities:
                    f.amount = round(f.amount * k, -3)
                    if f.emi:
                        i = f.rate / 12
                        f.emi = round(f.amount * i / (1 - (1 + i) ** -f.tenure), 2)

        # Over-leveraged customers take on new on-us credit after onset.
        if c.archetype in ("OVER_LEVERAGED", "SME_OVER_LEVERAGED"):
            new = (["PF", "CC"] if c.ctype == "INDIVIDUAL" else ["TL", "EF"])
            for code in new[: self.ri(1, 2)]:
                m = min(c.onset + self.ri(1, 6), C.HISTORY_MONTHS - 2)
                amt = I * (self.u(8, 14) if code == "PF" else self.u(1.5, 2.5) if code == "CC"
                           else self.u(1.5, 3.5) if code == "TL" else self.u(0.6, 1.5))
                f = self._new_facility(c, code, amt, m)
                f.u0 = max(f.u0, 0.5)

        # Primary facility = largest unsecured exposure (drives targeted behaviours).
        unsecured = [f for f in c.facilities if not f.product["secured"]] or c.facilities
        max(unsecured, key=lambda f: f.amount).is_primary = True

    # ================================================================ payments
    def _schedule(self, f: Facility):
        """Instalments falling inside the window (plus revolving minimum dues)."""
        if not f.revolving:
            first_due = add_months(f.disb_date, 1, f.due_day)
            for k in range(f.tenure):
                nominal = add_months(first_due, k, f.due_day)
                due = next_business_day(nominal, lunar=False)
                if due > C.AS_OF_DATE:
                    break
                if due >= WINDOW_START:
                    f.instalments.append(Instalment(k + 1, due, f.emi, month_index(due)))
            return

        # Revolving: month-end balances follow a target utilisation path.
        n = C.HISTORY_MONTHS
        bal = np.zeros(n)
        c = self._owner
        cap = 0.97 if f.code == "CC" else 1.04
        open_m = f.open_month
        for m in MONTHS:
            if m < open_m:
                continue
            target = f.u0 + (cap - f.u0) * c.util_stress[m]
            if m == open_m:
                target *= 0.5
            bal[m] = float(np.clip(f.amount * target * self.rng.normal(1, 0.08),
                                   0, f.amount * 1.07))
        f.balance = np.round(bal, 2)
        pre = f.amount * f.u0
        for m in MONTHS:
            if m <= open_m:
                continue
            stmt = pre if m == 0 else bal[m - 1]
            if stmt < 1:
                continue
            if f.code == "CC":
                due_amt = max(0.05 * stmt, min(100.0, stmt))
            else:
                due_amt = stmt * (f.rate / 12 + 0.02)
            due = next_business_day(add_months(month_start(m), 0, f.due_day), lunar=False)
            if due > C.AS_OF_DATE:
                break
            f.instalments.append(Instalment(len(f.instalments) + 1, due, round(due_amt, 2), m))

    def _decide(self, c: Customer, f: Facility, inst: Instalment):
        """Return (kind, delay_days, fraction, catch_up)."""
        rng = self.rng
        m = inst.month
        s = float(c.stress[m]) if m >= 0 else 0.0
        a = c.archetype
        ev = c.event

        if ev and ev["kind"] == "DELINQUENCY_EPISODE":
            e, d = ev["month"], ev["duration"]
            if e <= m < e + d:
                return ("MISS", 0, 0, False) if rng.random() < 0.6 else \
                    ("PARTIAL", self.ri(0, 5), self.u(0.3, 0.6), False)
            if m == e + d:
                return ("ON_TIME", 0, 1, True)
            s = 0.0
        elif ev and a == "ISOLATED_EXCEPTION":
            e, kind = ev["month"], ev["kind"]
            applies = f.is_primary or kind in ("SALARY_DELAY", "HOLIDAY_PERIOD")
            if m == e and applies:
                if kind == "SALARY_DELAY":
                    return ("LATE", self.ri(8, 25), 1, True)
                if kind == "PROCESSING_DELAY":
                    return ("LATE", self.ri(3, 7), 1, True)
                if kind == "HOLIDAY_PERIOD":
                    return ("LATE", self.ri(6, 12), 1, True)
                return ("MISS", 0, 0, False)  # TEMPORARY_HARDSHIP
            if m == e + 1 and kind == "TEMPORARY_HARDSHIP" and applies:
                return ("ON_TIME", 0, 1, True)

        if a in ("LEGACY_NPL", "SME_LEGACY_NPL") and m >= c.onset:
            # Occasional token payments under collections pressure; arrears never cleared.
            if rng.random() < 0.75:
                return ("MISS", 0, 0, False)
            return ("PARTIAL", self.ri(0, 20), self.u(0.1, 0.4), False)

        if a == "CHRONIC_LATE_PAYER":
            if rng.random() < 0.72:
                return ("LATE", self.ri(2, 9), 1, True)
            return ("ON_TIME", 0, 1, True)

        if a == "ROLL_FORWARD" and f.is_primary and m >= c.onset:
            r = rng.random()
            if r < 0.5:
                return ("MISS", 0, 0, False)
            if r < 0.85:
                return ("ON_TIME", self.ri(0, 6), 1, False)  # pays current only
            return ("PARTIAL", self.ri(0, 8), self.u(0.3, 0.7), False)

        p_miss = 0.0008 + 0.30 * s ** 1.5
        p_partial = 0.0015 + 0.15 * s
        p_late = 0.015 + 0.40 * s
        r = rng.random()
        catch_up = rng.random() < 0.95 * (1 - s) ** 2
        if r < p_miss:
            return ("MISS", 0, 0, False)
        if r < p_miss + p_partial:
            return ("PARTIAL", self.ri(0, 6), self.u(0.3, 0.8), False)
        if r < p_miss + p_partial + p_late:
            delay = min(1 + int(rng.geometric(1 / (2 + 18 * s))), 40)
            return ("LATE", delay, 1, catch_up)
        return ("ON_TIME", 0, 1, catch_up)

    def _pay_date(self, f: Facility, due: date, kind: str, delay: int) -> date:
        if kind == "LATE" or delay:
            return next_business_day(due + timedelta(days=delay), lunar=True)
        if f.channel == "MANUAL_TRANSFER":
            return due - timedelta(days=self.ri(0, 4))
        # Direct debits, standing instructions and PDCs execute on the due date, or on the
        # next business day when a lunar holiday was declared after the schedule was set.
        return next_business_day(due, lunar=True)

    def _simulate_facility(self, c: Customer, f: Facility):
        self._owner = c
        self._schedule(f)
        due_so_far = paid_so_far = 0.0
        for inst in f.instalments:
            arrears = max(due_so_far - paid_so_far, 0.0)
            due_so_far += inst.amount_due
            kind, delay, frac, catch_up = self._decide(c, f, inst)
            if kind == "MISS":
                continue
            amt = inst.amount_due * frac + (arrears if catch_up else 0.0)
            when = self._pay_date(f, inst.due_date, kind, delay)
            if when > C.AS_OF_DATE or amt <= 0:
                continue
            f.payments.append((when, round(amt, 2)))
            paid_so_far += amt

        # Loans that matured with arrears keep getting chased until settled.
        if not f.revolving and f.instalments and f.instalments[-1].no == f.tenure:
            m = f.instalments[-1].month + 1
            while due_so_far - paid_so_far > 0.5 and m < C.HISTORY_MONTHS:
                due = next_business_day(month_start(m).replace(day=f.due_day), lunar=False)
                if due > C.AS_OF_DATE:
                    break
                kind, delay, frac, catch_up = self._decide(c, f, Instalment(0, due, 0.0, m))
                arrears = due_so_far - paid_so_far
                amt = arrears if catch_up and kind in ("ON_TIME", "LATE") else \
                    arrears * frac if kind == "PARTIAL" else 0.0
                when = self._pay_date(f, due, kind, delay)
                if amt > 0 and when <= C.AS_OF_DATE:
                    f.payments.append((when, round(amt, 2)))
                    paid_so_far += amt
                m += 1

        # FIFO allocation: every payment clears the oldest outstanding amount first.
        pending = list(f.instalments)
        idx = 0
        for when, amt in sorted(f.payments):
            while amt > 0.005 and idx < len(pending):
                inst = pending[idx]
                need = inst.amount_due - inst.paid
                take = min(need, amt)
                inst.allocations.append((when, round(take, 2)))
                amt -= take
                if inst.amount_due - inst.paid <= 0.01:
                    inst.settle_date = when
                    idx += 1

        if not f.revolving and f.instalments and f.instalments[-1].no == f.tenure \
                and all(i.settle_date for i in f.instalments):
            f.closed_date = f.instalments[-1].settle_date

    # ================================================================ evidence
    def _evidence(self, c: Customer):
        ev = c.event
        prim = next(f for f in c.facilities if f.is_primary)
        if ev and c.archetype == "ISOLATED_EXCEPTION":
            m = ev["month"]
            kind = ev["kind"]
            target = next((i for i in prim.instalments if i.month == m), None)
            when = target.due_date if target else month_start(m)
            spec = {
                "SALARY_DELAY": ("Employer letter confirming payroll delay",
                                 "EMPLOYER_LETTER", 0.85),
                "PROCESSING_DELAY": ("Cheque clearing / payment gateway incident",
                                     "BANK_OPERATIONS_LOG", 1.0),
                "TEMPORARY_HARDSHIP": ("Hospitalisation; medical certificate provided",
                                       "MEDICAL_CERTIFICATE", 0.7),
                "HOLIDAY_PERIOD": ("Customer travelling during Eid holidays",
                                   "CUSTOMER_CALL", 0.3),
            }[kind]
            c.evidence.append(dict(
                customer_id=c.customer_id, facility_id=prim.facility_id, event_date=when,
                exception_type=kind, description=spec[0], evidence_source=spec[1],
                verified_flag="Y" if self.rng.random() < spec[2] else "N",
                recorded_date=when + timedelta(days=self.ri(1, 10))))
        # Deteriorating customers sometimes claim a one-off excuse that is not verified.
        if c.archetype in ("REPAYMENT_DETERIORATION", "ROLL_FORWARD",
                           "SME_REPAYMENT_DETERIORATION") and self.rng.random() < 0.3:
            m = min(c.onset + 1, C.HISTORY_MONTHS - 1)
            when = month_start(m) + timedelta(days=self.ri(3, 20))
            kind = "SALARY_DELAY" if c.ctype == "INDIVIDUAL" else "CUSTOMER_PAYMENT_DELAY"
            c.evidence.append(dict(
                customer_id=c.customer_id, facility_id=prim.facility_id, event_date=when,
                exception_type=kind,
                description="Customer states payment was delayed and will be regularised",
                evidence_source="CUSTOMER_CALL", verified_flag="N",
                recorded_date=when + timedelta(days=self.ri(0, 3))))
