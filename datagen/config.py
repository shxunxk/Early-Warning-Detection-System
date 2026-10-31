"""Reference data and knobs for the synthetic Early Warning System dataset.

Everything here is synthetic. The market setting is a UAE bank (AED), chosen because
the challenge pack uses GCC terminology (personal finance, profit rates). Lunar holiday
dates are approximate, as they are in real life until moon sighting is announced.
"""

from datetime import date

SEED = 42
DEFAULT_CUSTOMERS = 600
SME_SHARE = 0.30
AS_OF_DATE = date(2026, 9, 30)
HISTORY_MONTHS = 24  # Oct 2024 .. Sep 2026
CURRENCY = "AED"
BANK_NAME = "Synthetic Gulf Bank"

# --------------------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------------------
EMIRATES = {
    "Dubai": 0.45,
    "Abu Dhabi": 0.25,
    "Sharjah": 0.15,
    "Ajman": 0.06,
    "Ras Al Khaimah": 0.05,
    "Fujairah": 0.02,
    "Umm Al Quwain": 0.02,
}
# Contractors cluster in the northern emirates; this also seeds the risk cluster.
CONSTRUCTION_EMIRATES = {
    "Sharjah": 0.30,
    "Ajman": 0.15,
    "Dubai": 0.30,
    "Abu Dhabi": 0.15,
    "Ras Al Khaimah": 0.10,
}

# --------------------------------------------------------------------------------------
# Calendar. Weekend is Sat/Sun (UAE since 2022). Fixed-date holidays are known in advance,
# so schedules already roll past them. Lunar holidays are announced late, so direct debits
# falling on them execute on the next business day (a genuine, excusable delay).
# --------------------------------------------------------------------------------------
FIXED_HOLIDAYS = [
    (date(2024, 12, 2), "National Day"),
    (date(2024, 12, 3), "National Day"),
    (date(2025, 1, 1), "New Year's Day"),
    (date(2025, 12, 2), "National Day"),
    (date(2025, 12, 3), "National Day"),
    (date(2026, 1, 1), "New Year's Day"),
]
LUNAR_HOLIDAYS = [
    (date(2025, 3, 30), "Eid Al Fitr"),
    (date(2025, 3, 31), "Eid Al Fitr"),
    (date(2025, 4, 1), "Eid Al Fitr"),
    (date(2025, 6, 5), "Arafat Day"),
    (date(2025, 6, 6), "Eid Al Adha"),
    (date(2025, 6, 7), "Eid Al Adha"),
    (date(2025, 6, 8), "Eid Al Adha"),
    (date(2025, 6, 27), "Islamic New Year"),
    (date(2025, 9, 5), "Prophet's Birthday"),
    (date(2026, 3, 19), "Eid Al Fitr"),
    (date(2026, 3, 20), "Eid Al Fitr"),
    (date(2026, 3, 21), "Eid Al Fitr"),
    (date(2026, 5, 26), "Arafat Day"),
    (date(2026, 5, 27), "Eid Al Adha"),
    (date(2026, 5, 28), "Eid Al Adha"),
    (date(2026, 5, 29), "Eid Al Adha"),
    (date(2026, 6, 16), "Islamic New Year"),
    (date(2026, 8, 25), "Prophet's Birthday"),
]

# --------------------------------------------------------------------------------------
# Individuals: employer sector -> (weight, median monthly salary AED, lognormal sigma)
# --------------------------------------------------------------------------------------
EMPLOYER_SECTORS = {
    "Government": (0.16, 24000, 0.40),
    "Banking & Finance": (0.09, 26000, 0.50),
    "Oil & Gas": (0.06, 32000, 0.45),
    "Healthcare": (0.10, 17000, 0.50),
    "Education": (0.08, 14000, 0.40),
    "Retail Trade": (0.11, 8500, 0.45),
    "Hospitality & Tourism": (0.13, 9500, 0.50),
    "Construction": (0.07, 10000, 0.55),
    "Aviation": (0.06, 19000, 0.45),
    "IT & Telecom": (0.09, 22000, 0.45),
    "Self-Employed Professional": (0.05, 28000, 0.60),
}
# Monthly credit day of salary per sector (roughly; government pays earlier).
SALARY_DAY = {"Government": 20, "Self-Employed Professional": None}
DEFAULT_SALARY_DAY = 26

# --------------------------------------------------------------------------------------
# SMEs: industry -> (weight, seasonality profile, monthly receipts volatility)
# --------------------------------------------------------------------------------------
SME_INDUSTRIES = {
    "General Trading": (0.20, "flat", 0.12),
    "Construction & Contracting": (0.17, "construction", 0.22),
    "Restaurants & Hospitality": (0.14, "hospitality", 0.08),
    "Retail": (0.12, "retail", 0.08),
    "Logistics & Transport": (0.10, "flat", 0.10),
    "Healthcare Services": (0.08, "flat", 0.07),
    "Real Estate Services": (0.07, "flat", 0.18),
    "Manufacturing": (0.07, "flat", 0.10),
    "IT & Professional Services": (0.05, "flat", 0.14),
}
# Calendar-month multipliers (Jan..Dec). UAE: winter tourist season, summer slowdown.
SEASONALITY = {
    "flat": [1.00, 0.98, 1.02, 1.00, 1.00, 0.97, 0.94, 0.94, 0.99, 1.02, 1.04, 1.10],
    "hospitality": [1.18, 1.12, 1.00, 0.98, 0.88, 0.78, 0.70, 0.68, 0.82, 1.02, 1.15, 1.30],
    "retail": [1.12, 1.00, 1.10, 1.02, 0.95, 0.90, 0.85, 0.86, 0.95, 1.00, 1.08, 1.22],
    "construction": [1.02, 1.02, 1.04, 1.02, 1.00, 0.95, 0.88, 0.88, 0.98, 1.04, 1.04, 1.08],
}
# SME size band by monthly receipts (AED) -> (weight, low, high)
SME_SIZE = {
    "Micro": (0.45, 60_000, 250_000),
    "Small": (0.40, 250_000, 1_500_000),
    "Medium": (0.15, 1_500_000, 6_000_000),
}

# --------------------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------------------
PRODUCTS = {
    "PF": dict(name="Personal Finance", kind="instalment", portfolio="Retail Unsecured",
               rate=(0.065, 0.115), tenure=(24, 48), secured=False),
    "AF": dict(name="Auto Finance", kind="instalment", portfolio="Retail Secured",
               rate=(0.029, 0.055), tenure=(36, 60), secured=True),
    "HF": dict(name="Home Finance", kind="instalment", portfolio="Retail Secured",
               rate=(0.039, 0.055), tenure=(180, 300), secured=True),
    "CC": dict(name="Credit Card", kind="revolving", portfolio="Retail Unsecured",
               rate=(0.0299 * 12, 0.0349 * 12), tenure=None, secured=False),
    "TL": dict(name="Business Term Loan", kind="instalment", portfolio="SME Lending",
               rate=(0.075, 0.115), tenure=(24, 60), secured=False),
    "WC": dict(name="Working Capital Facility", kind="revolving", portfolio="SME Lending",
               rate=(0.085, 0.13), tenure=None, secured=False),
    "EF": dict(name="Equipment Finance", kind="instalment", portfolio="SME Lending",
               rate=(0.06, 0.09), tenure=(36, 60), secured=True),
}

PAYMENT_CHANNELS = ["DIRECT_DEBIT", "STANDING_INSTRUCTION", "MANUAL_TRANSFER", "CHEQUE"]

# --------------------------------------------------------------------------------------
# Behaviour archetypes. Weights are within the customer type. `band` is the intended
# risk band at the as-of date before severity adjustment (see ground truth).
# Scenario numbers refer to the 8 scenarios in problem statement 4.
# --------------------------------------------------------------------------------------
INDIVIDUAL_ARCHETYPES = {
    "STABLE": dict(weight=0.50, scenarios=[], band="Low"),
    "ISOLATED_EXCEPTION": dict(weight=0.09, scenarios=[4], band="Low"),
    "CHRONIC_LATE_PAYER": dict(weight=0.04, scenarios=[1], band="Medium"),
    "REPAYMENT_DETERIORATION": dict(weight=0.07, scenarios=[1, 5], band="High"),
    "INCOME_DECLINE": dict(weight=0.06, scenarios=[2], band="Medium"),
    "OVER_LEVERAGED": dict(weight=0.06, scenarios=[3], band="Medium"),
    "ROLL_FORWARD": dict(weight=0.04, scenarios=[5], band="High"),
    "CURED": dict(weight=0.05, scenarios=[5], band="Low"),
    "THIN_FILE": dict(weight=0.05, scenarios=[7], band="Low"),
    "SALARY_ELSEWHERE": dict(weight=0.04, scenarios=[7], band="Low"),
}
SME_ARCHETYPES = {
    "SME_STABLE": dict(weight=0.48, scenarios=[], band="Low"),
    "SME_SEASONAL": dict(weight=0.15, scenarios=[6], band="Low"),
    "SME_CASHFLOW_STRESS": dict(weight=0.14, scenarios=[2, 6], band="High"),
    "SME_OVER_LEVERAGED": dict(weight=0.08, scenarios=[3, 6], band="Medium"),
    "SME_REPAYMENT_DETERIORATION": dict(weight=0.07, scenarios=[1, 5, 6], band="High"),
    "SME_THIN_FILE": dict(weight=0.08, scenarios=[7], band="Low"),
}

# Emerging risk clusters (scenario 8): a segment where a share of customers is switched to
# a stress archetype with onsets bunched in the same few months.
CLUSTERS = [
    dict(cluster_id="CLU-SME-CONSTR-NORTH", customer_type="SME",
         industry="Construction & Contracting", emirates=["Sharjah", "Ajman"],
         share=0.6, archetype="SME_CASHFLOW_STRESS", onset_months=(17, 19),
         note="Contractors in Sharjah/Ajman hit by delayed main-contractor payments"),
    dict(cluster_id="CLU-RET-HOSP-DXB", customer_type="INDIVIDUAL",
         sector="Hospitality & Tourism", emirates=["Dubai"],
         share=0.45, archetype="INCOME_DECLINE", onset_months=(18, 20),
         note="Hospitality employees in Dubai facing pay cuts and layoffs"),
]

DPD_BUCKETS = [(0, 0, "Current"), (1, 30, "1-30"), (31, 60, "31-60"), (61, 90, "61-90"),
               (91, 180, "91-180"), (181, 10**6, "180+")]

FIRST_NAMES = [
    "Ahmed", "Mohammed", "Fatima", "Aisha", "Omar", "Khalid", "Mariam", "Hessa", "Saeed",
    "Rashid", "Noura", "Layla", "Yousef", "Huda", "Rahul", "Priya", "Arjun", "Anjali",
    "Imran", "Sana", "Bilal", "Ayesha", "Faisal", "Zainab", "Joseph", "Maria", "John",
    "Grace", "Michael", "Sarah", "David", "Emma", "Ravi", "Deepa", "Kiran", "Nikhil",
    "Rohan", "Meera", "Tariq", "Hana", "Karim", "Rania", "Samir", "Dina", "Jose", "Ana",
    "Mark", "Liza", "Hassan", "Amira",
]
LAST_NAMES = [
    "Al Mansoori", "Al Nuaimi", "Al Hashimi", "Al Ketbi", "Al Shamsi", "Al Suwaidi",
    "Haddad", "Khoury", "Nasser", "Saleh", "Hamdan", "Farouk", "Sharma", "Nair", "Menon",
    "Iyer", "Patel", "Reddy", "Khan", "Qureshi", "Siddiqui", "Malik", "Santos", "Reyes",
    "Cruz", "Fernandes", "D'Souza", "Pereira", "Smith", "Brown", "Wilson", "Taylor",
    "Rahman", "Hossain", "Perera", "Silva", "Mathew", "Thomas", "George", "Varghese",
]
SME_NAME_PARTS = (
    ["Al Noor", "Golden Sands", "Desert Rose", "Gulf Star", "Blue Coast", "Palm Crest",
     "Falcon", "Oasis", "Pearl Bay", "Horizon", "Crescent", "Silver Dune", "Emirates Link",
     "Marina", "Summit", "Red Sea", "Arabian Gate", "Al Bustan", "Dune Ridge", "Coral"],
    {
        "General Trading": ["General Trading LLC", "Trading Co. LLC", "Import & Export LLC"],
        "Construction & Contracting": ["Building Contracting LLC", "Contracting Co.",
                                       "Civil Works LLC", "Technical Services LLC"],
        "Restaurants & Hospitality": ["Restaurant LLC", "Cafe & Bistro", "Hospitality LLC"],
        "Retail": ["Retail LLC", "Stores LLC", "Fashion LLC", "Electronics LLC"],
        "Logistics & Transport": ["Logistics LLC", "Cargo Services LLC", "Transport LLC"],
        "Healthcare Services": ["Medical Centre LLC", "Pharmacy LLC", "Dental Clinic"],
        "Real Estate Services": ["Real Estate Brokers", "Property Management LLC"],
        "Manufacturing": ["Industries LLC", "Plastics Factory LLC", "Metal Works LLC"],
        "IT & Professional Services": ["IT Solutions FZE", "Consultancy FZ-LLC",
                                       "Digital Services FZE"],
    },
)
