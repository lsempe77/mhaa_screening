"""
Step 2d: Browser-based fetch for open-access papers hidden behind JavaScript /
Akamai / Cloudflare bot challenges that defeat plain HTTP (cloudscraper,
curl_cffi). Uses a real Chrome via Playwright.

Primary target: MDPI (10.3390), which is fully OA but serves an Akamai
interstitial. Also handles other OA publishers by reading the citation_pdf_url
meta tag from the rendered page. Paywalled publishers are skipped by default
(a browser only sees their abstract), unless you pass --all.

Setup (one time):
    pip install playwright
    python -m playwright install chromium   # or use installed Chrome via channel

Run (when other steps are NOT running - shared CSV):
    python step2d_browser.py inventory_{timestamp}.csv
    python step2d_browser.py inventory_{timestamp}.csv --all       # try every missing DOI
    python step2d_browser.py inventory_{timestamp}.csv --headless  # no visible window
"""

import re
import sys
import time
from datetime import datetime

import pandas as pd
from playwright.sync_api import sync_playwright

import config
from step2_fetch_missing_pdfs import is_pdf_bytes, target_filename

# OA publishers worth a browser visit (fully OA, but JS/bot-challenged)
OA_BROWSER_PREFIXES = ("10.3390", "10.1186", "10.3389", "10.1371", "10.1155", "10.1093/oodh")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHECKPOINT_EVERY = 5


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def citation_pdf_url(html: str) -> str | None:
    for pat in (
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def grab_pdf(page, pdf_url: str, out_path) -> bool:
    """Download a PDF either via the browser download event or an inline
    navigation response."""
    # 1) download event (MDPI serves /pdf as an attachment)
    try:
        with page.expect_download(timeout=25000) as dl:
            try:
                page.goto(pdf_url, timeout=25000)
            except Exception:
                pass  # goto raises "Download is starting" - expected
        d = dl.value
        d.save_as(str(out_path))
        with open(out_path, "rb") as f:
            if f.read(5) == b"%PDF-":
                return True
    except Exception:
        pass
    # 2) inline navigation response
    try:
        resp = page.goto(pdf_url, wait_until="commit", timeout=25000)
        body = resp.body()
        if is_pdf_bytes(body):
            with open(out_path, "wb") as f:
                f.write(body)
            return True
    except Exception:
        pass
    return False


def resolve_and_grab(page, doi: str, out_path) -> str | None:
    try:
        page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return None
    page.wait_for_timeout(5000)  # let any bot-challenge interstitial resolve
    final = page.url
    html = ""
    try:
        html = page.content()
    except Exception:
        pass

    # Determine candidate PDF URL(s)
    candidates = []
    if "mdpi.com" in final:
        candidates.append(final.split("?")[0].rstrip("/") + "/pdf")
    meta = citation_pdf_url(html)
    if meta:
        if meta.startswith("/"):
            meta = "/".join(final.split("/")[:3]) + meta
        candidates.append(meta)

    for url in candidates:
        if grab_pdf(page, url, out_path):
            host = final.split("/")[2].replace("www.", "") if "//" in final else "browser"
            return f"browser:{host}"
    return None


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python step2d_browser.py <inventory_csv> [--all] [--headless]")

    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    try_all = "--all" in sys.argv
    headless = "--headless" in sys.argv

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    missing = (~has_pdf) & (df["pdf_path"].str.strip() == "") & (df["doi"].str.strip() != "")
    if not try_all:
        missing &= df["doi"].str.strip().str.startswith(OA_BROWSER_PREFIXES)
    todo = df[missing].copy()
    log(f"Browser candidates to fetch: {len(todo)}  ({'all missing DOIs' if try_all else 'OA publishers only'})")
    if len(todo) == 0:
        return

    found = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, accept_downloads=True)

        for n, (idx, row) in enumerate(todo.iterrows(), start=1):
            doi = row["doi"].strip()
            out_path = config.PDF_DIR / target_filename(row["zotero_key"], doi, row["title"])
            page = ctx.new_page()
            try:
                source = resolve_and_grab(page, doi, out_path)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            if source:
                df.at[idx, "pdf_path"] = str(out_path.relative_to(config.ROOT))
                df.at[idx, "pdf_source"] = source
                found += 1
                log(f"[{n}/{len(todo)}] {row['zotero_key']}: OK via {source}")
            else:
                log(f"[{n}/{len(todo)}] {row['zotero_key']}: not found ({doi})")

            if n % CHECKPOINT_EVERY == 0:
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                log(f"  ...checkpoint saved ({found} found so far)")

        browser.close()

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"Browser PDFs fetched: {found}/{len(todo)}")
    log(f"Updated inventory:    {csv_path}")


if __name__ == "__main__":
    main()
