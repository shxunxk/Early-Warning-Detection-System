"""Reference data and knobs for the synthetic Early Warning System dataset.

Everything here is synthetic. The market setting is an Indian bank (INR), so SMA/NPA
classification, NACH mandates and MSME turnover bands all line up with RBI practice.
Festival dates that depend on moon sighting (Eid, Muharram, Milad-un-Nabi) are announced
only a day or two ahead, as they are in real life, so mandates falling on them execute on
the next business day — a genuine, excusable delay the scoring engine must not punish.
"""

from datetime import date

SEED = 42
DEFAULT_CUSTOMERS = 600
SME_SHARE = 0.30
AS_OF_DATE = date(2026, 9, 30)
HISTORY_MONTHS = 24  # Oct 2024 .. Sep 2026
CURRENCY = "INR"
BANK_NAME = "Synthetic Bharat Commercial Bank"

# --------------------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------------------
REGIONS = {
    "Mumbai": 0.22,
    "Delhi NCR": 0.20,
    "Bengaluru": 0.16,
    "Hyderabad": 0.12,
    "Chennai": 0.10,
    "Pune": 0.09,
    "Ahmedabad": 0.07,
    "Kolkata": 0.04,
}
# Contractors cluster around the fast-building metros; this also seeds the risk cluster.
CONSTRUCTION_REGIONS = {
    "Delhi NCR": 0.30,
    "Pune": 0.20,
    "Mumbai": 0.20,
    "Hyderabad": 0.15,
    "Ahmedabad": 0.15,
}

# --------------------------------------------------------------------------------------
# Calendar. Weekend is Sat/Sun. Fixed-date and panchang-computed holidays (Republic Day,
# Holi, Diwali) are published in the RBI holiday calendar well in advance, so schedules
# already roll past them. Moon-sighting holidays are confirmed only a day or two ahead,
# so NACH mandates falling on them execute on the next business day.
# --------------------------------------------------------------------------------------
FIXED_HOLIDAYS = [
    (date(2024, 10, 31), "Diwali"),
    (date(2024, 12, 25), "Christmas"),
    (date(2025, 1, 1), "New Year's Day"),
    (date(2025, 1, 26), "Republic Day"),
    (date(2025, 3, 14), "Holi"),
    (date(2025, 8, 15), "Independence Day"),
    (date(2025, 10, 2), "Gandhi Jayanti"),
    (date(2025, 10, 20), "Diwali"),
    (date(2025, 12, 25), "Christmas"),
    (date(2026, 1, 1), "New Year's Day"),
    (date(2026, 1, 26), "Republic Day"),
    (date(2026, 3, 4), "Holi"),
    (date(2026, 8, 15), "Independence Day"),
    (date(2026, 10, 2), "Gandhi Jayanti"),
]
LUNAR_HOLIDAYS = [
    (date(2025, 3, 31), "Eid-ul-Fitr"),
    (date(2025, 6, 7), "Bakrid (Eid-ul-Adha)"),
    (date(2025, 7, 6), "Muharram"),
    (date(2025, 9, 5), "Milad-un-Nabi"),
    (date(2026, 3, 21), "Eid-ul-Fitr"),
    (date(2026, 5, 28), "Bakrid (Eid-ul-Adha)"),
    (date(2026, 6, 26), "Muharram"),
    (date(2026, 8, 26), "Milad-un-Nabi"),
]

# --------------------------------------------------------------------------------------
# Individuals: employer sector -> (weight, median monthly salary INR, lognormal sigma)
# Calibrated to urban salaried borrowers of a mid-size bank, not the national median.
# --------------------------------------------------------------------------------------
EMPLOYER_SECTORS = {
    "Government & PSU": (0.16, 62_000, 0.40),
    "Banking & Finance": (0.09, 85_000, 0.50),
    "Oil & Gas": (0.06, 105_000, 0.45),
    "Healthcare": (0.10, 55_000, 0.50),
    "Education": (0.08, 42_000, 0.40),
    "Retail Trade": (0.11, 26_000, 0.45),
    "Hospitality & Tourism": (0.13, 28_000, 0.50),
    "Construction": (0.07, 30_000, 0.55),
    "Aviation": (0.06, 68_000, 0.45),
    "IT & Telecom": (0.09, 95_000, 0.45),
    "Self-Employed Professional": (0.05, 90_000, 0.60),
}
# Monthly credit day of salary per sector (government and PSU payrolls run earlier).
SALARY_DAY = {"Government & PSU": 20, "Self-Employed Professional": None}
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
# Calendar-month multipliers (Jan..Dec). India: festive Oct-Nov peak (Dussehra, Diwali,
# wedding season), monsoon trough Jun-Aug, construction halts in the rains.
SEASONALITY = {
    "flat": [0.98, 0.96, 1.02, 1.00, 1.00, 0.95, 0.92, 0.93, 1.00, 1.10, 1.08, 1.02],
    "hospitality": [1.20, 1.15, 1.05, 0.95, 0.88, 0.75, 0.70, 0.72, 0.88, 1.10, 1.22, 1.28],
    "retail": [0.95, 0.92, 1.02, 1.00, 0.98, 0.90, 0.86, 0.90, 1.05, 1.30, 1.20, 1.02],
    "construction": [1.08, 1.10, 1.10, 1.05, 1.00, 0.85, 0.72, 0.74, 0.88, 1.05, 1.12, 1.10],
}
# SME size band by monthly receipts (INR) -> (weight, low, high). Annualised these sit
# inside the MSME turnover definitions: micro <= 5 cr, small <= 50 cr, medium <= 250 cr.
SME_SIZE = {
    "Micro": (0.45, 500_000, 2_500_000),
    "Small": (0.40, 2_500_000, 15_000_000),
    "Medium": (0.15, 15_000_000, 60_000_000),
}

# --------------------------------------------------------------------------------------
# Products. Rates are nominal annual, in line with Indian pricing (cards ~36-42% p.a.).
# --------------------------------------------------------------------------------------
PRODUCTS = {
    "PF": dict(name="Personal Loan", kind="instalment", portfolio="Retail Unsecured",
               rate=(0.105, 0.18), tenure=(24, 60), secured=False),
    "AF": dict(name="Auto Loan", kind="instalment", portfolio="Retail Secured",
               rate=(0.0875, 0.12), tenure=(36, 84), secured=True),
    "HF": dict(name="Home Loan", kind="instalment", portfolio="Retail Secured",
               rate=(0.0835, 0.0975), tenure=(180, 300), secured=True),
    "CC": dict(name="Credit Card", kind="revolving", portfolio="Retail Unsecured",
               rate=(0.0299 * 12, 0.0349 * 12), tenure=None, secured=False),
    "TL": dict(name="Business Term Loan", kind="instalment", portfolio="MSME Lending",
               rate=(0.11, 0.17), tenure=(24, 60), secured=False),
    "WC": dict(name="Cash Credit / Working Capital", kind="revolving",
               portfolio="MSME Lending", rate=(0.115, 0.18), tenure=None, secured=False),
    "EF": dict(name="Equipment Finance", kind="instalment", portfolio="MSME Lending",
               rate=(0.095, 0.14), tenure=(36, 60), secured=True),
}

PAYMENT_CHANNELS = ["NACH_DEBIT", "STANDING_INSTRUCTION", "UPI_AUTOPAY", "MANUAL_TRANSFER",
                    "CHEQUE"]

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
    dict(cluster_id="CLU-SME-CONSTR-NCR", customer_type="SME",
         industry="Construction & Contracting", regions=["Delhi NCR", "Pune"],
         share=0.6, archetype="SME_CASHFLOW_STRESS", onset_months=(17, 19),
         note="Contractors in Delhi NCR/Pune hit by delayed main-contractor payments"),
    dict(cluster_id="CLU-RET-HOSP-BLR", customer_type="INDIVIDUAL",
         sector="Hospitality & Tourism", regions=["Bengaluru"],
         share=0.45, archetype="INCOME_DECLINE", onset_months=(18, 20),
         note="Hospitality employees in Bengaluru facing pay cuts and layoffs"),
]

DPD_BUCKETS = [(0, 0, "Current"), (1, 30, "1-30"), (31, 60, "31-60"), (61, 90, "61-90"),
               (91, 180, "91-180"), (181, 10**6, "180+")]

# Names are drawn as (given, family) from one pool at a time, so a customer does not end
# up with a Punjabi given name and a Bengali surname. Weights approximate an urban
# all-India customer book rather than any one state.
NAME_POOLS = [
    (0.34,  # North Indian Hindu
     ["Aditya", "Arjun", "Rohan", "Nikhil", "Rahul", "Karan", "Vikram", "Suresh", "Anil",
      "Manoj", "Deepak", "Sanjay", "Rajesh", "Amit", "Priya", "Ananya", "Meera", "Kavya",
      "Sneha", "Pooja", "Anjali", "Sunita", "Rekha", "Divya", "Shruti", "Neha"],
     ["Sharma", "Verma", "Gupta", "Agarwal", "Jain", "Singh", "Kumar", "Yadav", "Mishra",
      "Tiwari", "Pandey", "Chauhan", "Rathore", "Malhotra", "Kapoor", "Chopra", "Bhatia"]),
    (0.20,  # South Indian
     ["Karthik", "Aravind", "Hari", "Ganesh", "Srinivas", "Vignesh", "Suresh", "Ramesh",
      "Lakshmi", "Deepa", "Divya", "Anitha", "Kavitha", "Shalini", "Meenakshi", "Revathi"],
     ["Nair", "Menon", "Iyer", "Pillai", "Krishnan", "Subramaniam", "Reddy", "Rao",
      "Naidu", "Varma", "Mathew", "Thomas", "George", "Varghese"]),
    (0.18,  # Western India (Gujarati / Marathi)
     ["Nilesh", "Jignesh", "Bhavesh", "Kunal", "Omkar", "Sachin", "Prasad", "Mandar",
      "Hetal", "Falguni", "Sejal", "Snehal", "Ketaki", "Aarti", "Manisha", "Vaishali"],
     ["Patel", "Shah", "Desai", "Mehta", "Joshi", "Trivedi", "Patil", "Deshmukh",
      "Kulkarni", "Jadhav", "Shinde", "Gokhale"]),
    (0.10,  # Eastern India (Bengali)
     ["Abhijit", "Sourav", "Subhash", "Tapan", "Arindam", "Debashish",
      "Rituparna", "Moushumi", "Sohini", "Paromita", "Ananya", "Indrani"],
     ["Banerjee", "Chatterjee", "Mukherjee", "Ghosh", "Das", "Bose", "Dutta", "Sen"]),
    (0.11,  # Muslim
     ["Imran", "Faisal", "Bilal", "Farhan", "Arif", "Shahid", "Zeeshan", "Asif",
      "Ayesha", "Zainab", "Sana", "Nazia", "Rukhsar", "Farah", "Heena", "Shabana"],
     ["Khan", "Qureshi", "Siddiqui", "Ansari", "Sheikh", "Rizvi", "Hussain", "Shaikh"]),
    (0.05,  # Punjabi Sikh
     ["Jaspreet", "Harpreet", "Gurpreet", "Manpreet", "Amrit", "Simran", "Ravneet",
      "Navjot"],
     ["Singh", "Kaur", "Gill", "Sidhu", "Dhillon", "Grewal", "Bedi", "Sandhu"]),
    (0.02,  # Goan / Anglo-Indian Christian
     ["Joseph", "Maria", "Thomas", "Grace", "Rowan", "Melissa", "Ryan", "Clara"],
     ["Fernandes", "D'Souza", "Pereira", "Rodrigues", "Pinto", "Lobo"]),
]
SME_NAME_PARTS = (
    ["Shree", "Balaji", "Ganesh", "Sai", "Krishna", "Lakshmi", "Bharat", "Sunrise",
     "Siddhi", "Omkar", "Ashoka", "Narmada", "Godavari", "Deccan", "Konark", "Vindhya",
     "Rajdhani", "Aravalli", "Nilgiri", "Trident"],
    {
        "General Trading": ["Trading Co.", "Enterprises", "Traders Pvt Ltd",
                            "Impex Pvt Ltd"],
        "Construction & Contracting": ["Constructions Pvt Ltd", "Infra Projects Pvt Ltd",
                                       "Builders & Contractors", "Civil Works LLP"],
        "Restaurants & Hospitality": ["Restaurant Pvt Ltd", "Caterers LLP",
                                      "Hospitality Pvt Ltd"],
        "Retail": ["Retail Pvt Ltd", "Stores", "Garments Pvt Ltd", "Electronics Pvt Ltd"],
        "Logistics & Transport": ["Logistics Pvt Ltd", "Roadlines", "Transport Co."],
        "Healthcare Services": ["Medical Centre Pvt Ltd", "Medicals", "Dental Clinic LLP"],
        "Real Estate Services": ["Realtors Pvt Ltd", "Property Services LLP"],
        "Manufacturing": ["Industries Pvt Ltd", "Polymers Pvt Ltd", "Engineering Works"],
        "IT & Professional Services": ["Technologies Pvt Ltd", "Consultancy LLP",
                                       "Softech Pvt Ltd"],
    },
)
# Indian business constitutions, in rough order of prevalence among MSME borrowers.
LEGAL_FORMS = ["Proprietorship", "Partnership", "Private Limited", "LLP"]
LEGAL_FORM_WEIGHTS = [0.42, 0.26, 0.22, 0.10]

# --------------------------------------------------------------------------------------
# Portfolio profiles: alternative institutions generated with the same engine.
# Each profile overrides module-level constants above (applied by generate.py --profile).
# --------------------------------------------------------------------------------------
ID_START = 100001

PROFILES = {
    "default": dict(
        label="Synthetic Bharat Commercial Bank: diversified retail-led portfolio",
    ),
    "northern_sme": dict(
        label="Northern Plains Commercial Bank: MSME-heavy mid-market portfolio, "
              "stressed 2026 (freight costs, retail pay cuts, weak monsoon trading)",
        BANK_NAME="Northern Plains Commercial Bank (synthetic)",
        DEFAULT_CUSTOMERS=1200,
        SEED=2026,
        ID_START=500001,
        SME_SHARE=0.45,
        REGIONS={"Ludhiana": 0.26, "Jalandhar": 0.14, "Kanpur": 0.14, "Delhi NCR": 0.20,
                 "Lucknow": 0.10, "Agra": 0.09, "Meerut": 0.07},
        CONSTRUCTION_REGIONS={"Ludhiana": 0.30, "Kanpur": 0.22, "Delhi NCR": 0.22,
                              "Agra": 0.15, "Meerut": 0.11},
        # Mid-market: lower salaries, more retail/hospitality/construction employees.
        EMPLOYER_SECTORS={
            "Government & PSU": (0.12, 48_000, 0.40),
            "Banking & Finance": (0.05, 60_000, 0.50),
            "Oil & Gas": (0.03, 82_000, 0.45),
            "Healthcare": (0.10, 40_000, 0.50),
            "Education": (0.09, 32_000, 0.40),
            "Retail Trade": (0.17, 20_000, 0.45),
            "Hospitality & Tourism": (0.12, 22_000, 0.50),
            "Construction": (0.12, 24_000, 0.55),
            "Aviation": (0.03, 52_000, 0.45),
            "IT & Telecom": (0.06, 58_000, 0.45),
            "Self-Employed Professional": (0.11, 55_000, 0.60),
        },
        SME_INDUSTRIES={
            "General Trading": (0.20, "flat", 0.12),
            "Construction & Contracting": (0.16, "construction", 0.22),
            "Restaurants & Hospitality": (0.12, "hospitality", 0.08),
            "Retail": (0.12, "retail", 0.08),
            "Logistics & Transport": (0.16, "flat", 0.12),
            "Healthcare Services": (0.06, "flat", 0.07),
            "Real Estate Services": (0.05, "flat", 0.18),
            "Manufacturing": (0.10, "flat", 0.10),
            "IT & Professional Services": (0.03, "flat", 0.14),
        },
        SME_SIZE={"Micro": (0.55, 400_000, 2_000_000),
                  "Small": (0.37, 2_000_000, 12_000_000),
                  "Medium": (0.08, 12_000_000, 40_000_000)},
        INDIVIDUAL_ARCHETYPES={
            "STABLE": dict(weight=0.41, scenarios=[], band="Low"),
            "LEGACY_NPL": dict(weight=0.03, scenarios=[5], band="Critical"),
            "ISOLATED_EXCEPTION": dict(weight=0.09, scenarios=[4], band="Low"),
            "CHRONIC_LATE_PAYER": dict(weight=0.05, scenarios=[1], band="Medium"),
            "REPAYMENT_DETERIORATION": dict(weight=0.08, scenarios=[1, 5], band="High"),
            "INCOME_DECLINE": dict(weight=0.07, scenarios=[2], band="Medium"),
            "OVER_LEVERAGED": dict(weight=0.07, scenarios=[3], band="Medium"),
            "ROLL_FORWARD": dict(weight=0.05, scenarios=[5], band="High"),
            "CURED": dict(weight=0.05, scenarios=[5], band="Low"),
            "THIN_FILE": dict(weight=0.05, scenarios=[7], band="Low"),
            "SALARY_ELSEWHERE": dict(weight=0.05, scenarios=[7], band="Low"),
        },
        SME_ARCHETYPES={
            "SME_STABLE": dict(weight=0.33, scenarios=[], band="Low"),
            "SME_LEGACY_NPL": dict(weight=0.07, scenarios=[5, 6], band="Critical"),
            "SME_SEASONAL": dict(weight=0.13, scenarios=[6], band="Low"),
            "SME_CASHFLOW_STRESS": dict(weight=0.17, scenarios=[2, 6], band="High"),
            "SME_OVER_LEVERAGED": dict(weight=0.11, scenarios=[3, 6], band="Medium"),
            "SME_REPAYMENT_DETERIORATION": dict(weight=0.10, scenarios=[1, 5, 6], band="High"),
            "SME_THIN_FILE": dict(weight=0.09, scenarios=[7], band="Low"),
        },
        CLUSTERS=[
            dict(cluster_id="CLU-SME-LOGI-NORTH", customer_type="SME",
                 industry="Logistics & Transport", regions=["Ludhiana", "Kanpur"],
                 share=0.55, archetype="SME_CASHFLOW_STRESS", onset_months=(15, 18),
                 note="Hauliers in Ludhiana/Kanpur squeezed by freight rates and diesel costs"),
            dict(cluster_id="CLU-RET-TRADE-NORTH", customer_type="INDIVIDUAL",
                 sector="Retail Trade", regions=["Ludhiana", "Jalandhar"],
                 share=0.40, archetype="INCOME_DECLINE", onset_months=(17, 20),
                 note="Retail-trade employees in Ludhiana/Jalandhar facing pay cuts"),
            dict(cluster_id="CLU-SME-REST-AGR", customer_type="SME",
                 industry="Restaurants & Hospitality", regions=["Agra", "Meerut"],
                 share=0.50, archetype="SME_REPAYMENT_DETERIORATION", onset_months=(18, 20),
                 note="Agra/Meerut restaurants falling behind after a weak monsoon season"),
        ],
    ),
}
