# Northern Plains Commercial Bank (synthetic)

Northern Plains Commercial Bank: MSME-heavy mid-market portfolio, stressed 2026 (freight costs, retail pay cuts, weak monsoon trading)

- Generated with: `uv run python datagen/generate.py --profile northern_sme --customers 1200 --seed 2026 --out data\northern_sme`
- As-of date: 2026-09-30 · history 24 months · currency INR
- Customers: 1200 (540 SME)
- Clusters: CLU-SME-LOGI-NORTH (Hauliers in Ludhiana/Kanpur squeezed by freight rates and diesel costs); CLU-RET-TRADE-NORTH (Retail-trade employees in Ludhiana/Jalandhar facing pay cuts); CLU-SME-REST-AGR (Agra/Meerut restaurants falling behind after a weak monsoon season)

See `data/README.md` for the table dictionary (same schema as the default sample).
