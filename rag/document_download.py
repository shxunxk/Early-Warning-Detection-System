#!/usr/bin/env python3
"""Download the EWS RAG corpus (public regulatory documents).

Usage:
    pip install requests
    python download_ews_corpus.py

Output:
    ews_corpus/OUT/*.pdf|*.html   downloaded files
    ews_corpus/manifest.csv       name, source, final url, type, size, status

Each document lists candidate URLs, tried in order. If a URL returns an HTML
page (RBI viewer pages, BIS publication pages), the script looks for a linked
PDF and downloads that; if none is found, the HTML is saved instead.
"""
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

OUT = Path("./rag/documents")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
}

RBI_DOCS = "https://website.rbi.org.in/documents/d/rbi/"
RBI_MD = "https://rbi.org.in/ScriptS/BS_ViewMasDirections.aspx?id="
RBI_CIRC = "https://rbi.org.in/SCRIPTs/BS_CircularIndexDisplay.aspx?Id="

# name -> (tier, candidate urls)
DOCS = {
    # ---- Tier 1: India ----
    "rbi_resolution_stressed_assets_2025": (1, [
        RBI_DOCS + "commercial-banks-resolution-of-stressed-assets-1-",
        RBI_MD + "13145",
    ]),
    "rbi_irac_2025": (1, [
        RBI_DOCS + "commercial-banks-income-recognition-asset-classification-and-provisioning-1-",
        RBI_MD + "13146",
    ]),
    "rbi_ecl_asset_classification_draft_oct2025": (1, [
        RBI_DOCS + "03scbassetclassificationdraft_07oct2025",
    ]),
    "rbi_ecl_final_press_release_apr2026": (1, [
        "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=62628",
    ]),
    "rbi_credit_risk_mgmt_2025": (1, [
        RBI_DOCS + "cbs-credit-risk-mgmt-27112025-2006-1-",
        RBI_MD + "13153",
    ]),
    "rbi_fraud_risk_mgmt_2026": (1, [RBI_MD + "13641"]),
    "rbi_digital_lending_2025": (1, [
        RBI_DOCS + "directionsondigitallending",
        "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12848",
    ]),
    "rbi_rbc_commercial_banks_4th_amend_2026": (1, [RBI_CIRC + "13665"]),
    "rbi_rbc_nbfc_recovery_amend_2026": (1, [RBI_CIRC + "13672"]),
    "rbi_rbc_aifi_2025": (1, [RBI_MD + "12968"]),
    "rbi_free_ai_report_2025": (1, [
        "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=61018",
    ]),
    # ---- Tier 2: international / best practice ----
    "bcbs_credit_risk_principles_2025": (2, [
        "https://www.bis.org/publications/202504-guidelines-principles-management-credit-risk",
    ]),
    "bcbs_basel_framework_cri10": (2, [
        "https://www.bis.org/committees/bcbs/basel-consolidated-guidelines/module/cri/10",
    ]),
    "eba_lom_overview_openriskmanual": (2, [
        "https://www.openriskmanual.org/wiki/EBA_Guidelines_on_Loan_Origination_and_Monitoring",
    ]),
    "occ_lending_loan_portfolio_risk_mgmt_2026": (2, [
        "https://occ.gov/publications-and-resources/publications/comptrollers-handbook/"
        "files/lending-loan-portfolio-risk-management/pub-ch-lending-loan-portfolio.pdf",
    ]),
    "occ_rating_credit_risk_2001": (2, [
        "https://www.occ.treas.gov/publications/publications-by-type/comptrollers-handbook/"
        "rating-credit-risk/pub-ch-rating-credit-risk.pdf",
    ]),
    "rbi_guidance_note_credit_risk_2002": (2, [
        "https://www.rbi.org.in/Scripts/bs_viewcontent.aspx?Id=4787",
    ]),
    # ---- Tier 3: optional ----
    "rbi_fraud_master_directions_2017_ews_annex": (3, [RBI_MD + "10477"]),
    "sample_bank_credit_policy": (3, [
        "https://www.mahaveerbank.bank.in/assets/images/pdf/Credit%20Policy.pdf",
    ]),
    "bdo_ecl_draft_summary": (3, [
        "https://www.bdo.in/getmedia/f2923b35-d223-4d96-9978-222d18fbad63/"
        "The-Standard-Stance_BDO-India_Vol-28.pdf",
    ]),
    "kpmg_free_ai_summary": (3, [
        "https://assets.kpmg.com/content/dam/kpmgsites/in/pdf/2025/09/"
        "rbis-free-ai-committee-report-in-the-financial-sector.pdf",
    ]),
}

PDF_LINK = re.compile(r"""href=["']([^"']+\.pdf[^"']*)["']""", re.I)


def is_pdf(resp: requests.Response) -> bool:
    return resp.content[:5] == b"%PDF-" or "pdf" in resp.headers.get("content-type", "").lower()


def get(session: requests.Session, url: str):
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
            if r.status_code == 200 and r.content:
                return r
            print(f"    HTTP {r.status_code} for {url}")
        except requests.RequestException as exc:
            print(f"    error ({exc.__class__.__name__}) for {url}")
        time.sleep(2 * (attempt + 1))
    return None


def fetch_doc(session, name, urls):
    for url in urls:
        r = get(session, url)
        if r is None:
            continue
        if is_pdf(r):
            return r.content, "pdf", r.url
        # HTML page: try to follow a linked PDF
        html = r.text
        links = [urljoin(r.url, m) for m in PDF_LINK.findall(html)]
        links = sorted(set(links), key=lambda u: ("rbidocs" not in u.lower(), len(u)))
        for link in links[:3]:
            pr = get(session, link)
            if pr is not None and is_pdf(pr):
                return pr.content, "pdf", pr.url
        return r.content, "html", r.url
    return None, None, None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with requests.Session() as session:
        for name, (tier, urls) in DOCS.items():
            print(f"[tier {tier}] {name}")
            data, kind, final_url = fetch_doc(session, name, urls)
            if data is None:
                print("    FAILED - download manually from:", urls[0])
                rows.append([name, tier, urls[0], "", "", 0, "failed"])
                continue
            path = OUT / f"{name}.{kind}"
            path.write_bytes(data)
            print(f"    saved {path} ({len(data):,} bytes, {kind})")
            rows.append([name, tier, urls[0], final_url, kind, len(data), "ok"])
            time.sleep(1)

    with open(OUT / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "tier", "source_url", "final_url", "type", "bytes", "status"])
        w.writerows(rows)

    ok = sum(1 for r in rows if r[-1] == "ok")
    print(f"\nDone: {ok}/{len(rows)} downloaded. See {OUT/'manifest.csv'}.")
    if ok < len(rows):
        print("For failures, open the URL in a browser and save the PDF into ews_corpus/OUT/.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())