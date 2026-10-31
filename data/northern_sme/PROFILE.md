# Northern Emirates Commercial Bank (synthetic)

Northern Emirates Commercial Bank: SME-heavy mid-market portfolio, stressed 2026 (freight costs, retail pay cuts, weak summer trading)

- Generated with: `uv run python datagen/generate.py --profile northern_sme --customers 1200 --seed 2026 --out data/northern_sme`
- As-of date: 2026-09-30 · history 24 months · currency AED
- Customers: 1200 (540 SME)
- Clusters: CLU-SME-LOGI-NORTH (Hauliers in Sharjah/RAK squeezed by freight rates and fuel costs); CLU-RET-TRADE-NORTH (Retail-trade employees in Sharjah/Ajman facing pay cuts); CLU-SME-REST-AJM (Ajman/Sharjah restaurants falling behind after a weak summer)

See `data/README.md` for the table dictionary (same schema as the default sample).
