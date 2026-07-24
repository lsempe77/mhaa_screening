"""
Step 2b: Fetch PDFs for references that have NO DOI.

These are mostly grey literature (reports, theses, trial/registry records).
Rather than blindly searching, this step uses what each Zotero record already
provides, cheapest route first:

  1. URL-first: if the record has a `url`, fetch it and pull the PDF via the
     citation_pdf_url meta tag or any .pdf link on the page. This covers WHO
     publication pages and UK gov.uk reports (which attach PDFs), plus any
     landing page that exposes a direct PDF.
  2. Title search: for records with no usable URL (or where the URL yields no
     PDF), search OpenAlex and Semantic Scholar by title and accept a hit only
     when the returned title closely matches (guards false positives), then
     download its open-access PDF.

Records that are database/registry stubs (Cochrane CENTRAL CN-, Epistemonikos)
typically have no downloadable full text and will simply report "not found".

Filenames keep the reference<->PDF link: {zotero_key}_{safe_title}.pdf, and the
`pdf_path` / `pdf_source` columns are filled in the inventory.

Run (when step 2 is NOT running - shared CSV):
    python step2b_no_doi.py inventory_{timestamp}.csv
"""

import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

import config
from step2_fetch_missing_pdfs import (
    make_scraper, is_pdf_bytes, citation_pdf_url, download_pdf, target_filename,
)

TITLE_MATCH_THRESHOLD = 0.82
CHECKPOINT_EVERY = 10
UA_REFERER = "https://www.google.com/"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def normalise_title(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", (text or "").lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similar(a: str, b: str) -> float:
    na, nb = normalise_title(a), normalise_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


# --- Tier 1: URL-first ------------------------------------------------------

def fetch_via_url(scraper, url: str, out_path) -> str | None:
    try:
        r = scraper.get(url, timeout=40, allow_redirects=True, headers={"Referer": UA_REFERER})
    except Exception:
        return None

    host = urlparse(r.url).netloc.replace("www.", "")

    # The URL itself is already a PDF
    if r.status_code == 200 and is_pdf_bytes(r.content):
        with open(out_path, "wb") as f:
            f.write(r.content)
        return f"url:{host}"

    if r.status_code != 200 or not r.text:
        return None

    # Collect candidate PDF links from the page
    candidates: list[str] = []
    meta = citation_pdf_url(r.text)
    if meta:
        candidates.append(meta)
    # quoted href/src/content ending in .pdf
    candidates += re.findall(r'(?:href|content|src)\s*=\s*["\']([^"\']+\.pdf[^"\']*)', r.text, re.I)
    # unquoted attribute values ending in .pdf (some CMS emit bare hrefs)
    candidates += re.findall(r'(?:href|content|src)\s*=\s*([^\s"\'>]+\.pdf[^\s"\'>]*)', r.text, re.I)
    # WHO IRIS download endpoints have no .pdf extension; try all bitstreams and
    # let %PDF validation reject the cover-image bitstream.
    candidates += re.findall(r'https?://iris\.who\.int/server/api/core/bitstreams/[0-9a-fA-F-]+/content', r.text, re.I)

    seen = set()
    for c in candidates:
        full = urljoin(r.url, c.replace("&amp;", "&"))
        if full in seen:
            continue
        seen.add(full)
        if download_pdf(scraper, full, out_path, referer=r.url):
            return f"url:{host}"
    return None


# --- Tier 2: title search ---------------------------------------------------

def openalex_by_title(title: str) -> list[str]:
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={"filter": f"title.search:{title}", "per_page": 5},
            headers={"User-Agent": f"mailto:{config.USER_EMAIL}"}, timeout=20,
        )
        for w in r.json().get("results", []):
            if similar(title, w.get("title") or "") >= TITLE_MATCH_THRESHOLD:
                urls = []
                oa = (w.get("open_access") or {}).get("oa_url")
                if oa:
                    urls.append(oa)
                p = (w.get("primary_location") or {}).get("pdf_url")
                if p:
                    urls.append(p)
                for loc in w.get("locations", []) or []:
                    if loc.get("pdf_url"):
                        urls.append(loc["pdf_url"])
                if urls:
                    return urls
    except Exception:
        pass
    return []


def semantic_scholar_by_title(title: str) -> list[str]:
    try:
        headers = {}
        if config.SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = config.SEMANTIC_SCHOLAR_API_KEY
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "fields": "title,openAccessPdf", "limit": 5},
            headers=headers, timeout=20,
        )
        for w in r.json().get("data", []):
            if similar(title, w.get("title") or "") >= TITLE_MATCH_THRESHOLD:
                oa = (w.get("openAccessPdf") or {}).get("url")
                if oa:
                    return [oa]
    except Exception:
        pass
    return []


def fetch_via_title(scraper, title: str, out_path) -> str | None:
    for url in openalex_by_title(title):
        if download_pdf(scraper, url, out_path, referer=UA_REFERER):
            return "openalex_title"
    for url in semantic_scholar_by_title(title):
        if download_pdf(scraper, url, out_path, referer=UA_REFERER):
            return "s2_title"
    return None


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python step2b_no_doi.py <inventory_csv>")

    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    todo = df[(~has_pdf) & (df["pdf_path"].str.strip() == "") & (df["doi"].str.strip() == "")].copy()
    log(f"No-DOI references to process: {len(todo)}")

    scraper = make_scraper()
    found = 0
    for n, (idx, row) in enumerate(todo.iterrows(), start=1):
        zkey = row["zotero_key"]
        title = row["title"]
        url = (row["url"] or "").strip()
        out_path = config.PDF_DIR / target_filename(zkey, "", title)

        source = None
        if url:
            source = fetch_via_url(scraper, url, out_path)
        if not source and title:
            source = fetch_via_title(scraper, title, out_path)

        if source:
            df.at[idx, "pdf_path"] = str(out_path.relative_to(config.ROOT))
            df.at[idx, "pdf_source"] = source
            found += 1
            log(f"[{n}/{len(todo)}] {zkey}: OK via {source}")
        else:
            log(f"[{n}/{len(todo)}] {zkey}: not found")

        if n % CHECKPOINT_EVERY == 0:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"  ...checkpoint saved ({found} found so far)")
        time.sleep(0.4)

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"Newly fetched (no-DOI): {found}/{len(todo)}")
    log(f"Updated inventory:      {csv_path}")


if __name__ == "__main__":
    main()
