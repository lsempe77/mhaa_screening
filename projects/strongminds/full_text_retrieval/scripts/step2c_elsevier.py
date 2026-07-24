"""
Step 2c: Fetch PDFs for Elsevier (ScienceDirect) papers via the official
Article Retrieval API, using an institutional API key + insttoken.

This is the legitimate, subscription-based route for paywalled Elsevier content
(DOI prefix 10.1016 and other Elsevier-hosted DOIs). It requires an entitlement
through your institution (e.g. Oxford).

Credentials are read from environment variables ONLY (never hard-code them):
    ELSEVIER_API_KEY     your Elsevier developer API key
    ELSEVIER_INSTTOKEN   your institutional token (entitles subscribed content)

Set them in PowerShell before running:
    $env:ELSEVIER_API_KEY  = "<key>"
    $env:ELSEVIER_INSTTOKEN = "<insttoken>"

Run (when other steps are NOT running - shared CSV):
    python step2c_elsevier.py inventory_{timestamp}.csv
    python step2c_elsevier.py inventory_{timestamp}.csv --all   # try every missing DOI, not just 10.1016
"""

import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

import config
from step2_fetch_missing_pdfs import is_pdf_bytes, target_filename

API_URL = "https://api.elsevier.com/content/article/doi/{doi}"
CHECKPOINT_EVERY = 10


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def fetch_elsevier(doi: str, api_key: str, insttoken: str, out_path) -> bool:
    headers = {
        "X-ELS-APIKey": api_key,
        "X-ELS-Insttoken": insttoken,
        "Accept": "application/pdf",
    }
    try:
        r = requests.get(API_URL.format(doi=doi), headers=headers, timeout=60)
    except Exception:
        return False
    if r.status_code == 200 and is_pdf_bytes(r.content):
        with open(out_path, "wb") as f:
            f.write(r.content)
        return True
    return False


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python step2c_elsevier.py <inventory_csv> [--all]")

    api_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
    insttoken = os.environ.get("ELSEVIER_INSTTOKEN", "").strip()
    if not api_key:
        raise SystemExit(
            "Set your Elsevier credentials first (PowerShell):\n"
            '    $env:ELSEVIER_API_KEY   = "<key>"\n'
            '    $env:ELSEVIER_INSTTOKEN = "<insttoken>"'
        )
    if not insttoken:
        log("WARNING: ELSEVIER_INSTTOKEN not set - only open-access Elsevier content will return a PDF.")

    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    try_all = "--all" in sys.argv

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    missing = (~has_pdf) & (df["pdf_path"].str.strip() == "") & (df["doi"].str.strip() != "")
    if not try_all:
        missing &= df["doi"].str.strip().str.startswith("10.1016")
    todo = df[missing].copy()
    log(f"Elsevier candidates to fetch: {len(todo)}  ({'all missing DOIs' if try_all else 'prefix 10.1016 only'})")

    found = 0
    for n, (idx, row) in enumerate(todo.iterrows(), start=1):
        doi = row["doi"].strip()
        out_path = config.PDF_DIR / target_filename(row["zotero_key"], doi, row["title"])
        if fetch_elsevier(doi, api_key, insttoken, out_path):
            df.at[idx, "pdf_path"] = str(out_path.relative_to(config.ROOT))
            df.at[idx, "pdf_source"] = "elsevier_api"
            found += 1
            log(f"[{n}/{len(todo)}] {row['zotero_key']}: OK ({doi})")
        else:
            log(f"[{n}/{len(todo)}] {row['zotero_key']}: not entitled / not found ({doi})")

        if n % CHECKPOINT_EVERY == 0:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"  ...checkpoint saved ({found} found so far)")
        time.sleep(0.3)

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"Elsevier PDFs fetched: {found}/{len(todo)}")
    log(f"Updated inventory:     {csv_path}")


if __name__ == "__main__":
    main()
