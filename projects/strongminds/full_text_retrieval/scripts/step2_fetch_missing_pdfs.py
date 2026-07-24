"""
Step 2: Fetch PDFs only for references that do NOT already have one in Zotero.

The reference <-> PDF link is preserved two ways:
  1. Filename:  {zotero_key}_{safe_doi_or_title}.pdf  (zotero_key = anchor)
  2. CSV:       the `pdf_path` and `pdf_source` columns are filled in.

Retrieval strategy (open-access first, Sci-Hub last resort):
  * Direct OA PDF links from Unpaywall / OpenAlex / Semantic Scholar
  * Publisher landing page -> citation_pdf_url meta tag
  * Publisher-specific patterns (JMIR /PDF, MDPI /pdf)
  * Sci-Hub (optional, --no-scihub to disable)

Uses cloudscraper to get past the Cloudflare JS challenge that many OA
publishers (JMIR, MDPI, ...) put in front of their pages.

Run:
    python step2_fetch_missing_pdfs.py inventory_{timestamp}.csv
    python step2_fetch_missing_pdfs.py inventory_{timestamp}.csv --no-scihub
"""

import re
import sys
import time
from datetime import datetime

import cloudscraper
import pandas as pd
import requests

import config

CHECKPOINT_EVERY = 20
# Mirrors that currently serve article pages (verified 2026-07). Others
# (sci-hub.se, .st, .ren) are dead or behind DDoS-Guard/Cloudflare from many
# networks. The actual PDF is usually hosted on the sci.bban.top storage CDN.
SCIHUB_MIRRORS = [
    "https://sci-hub.ee",
    "https://sci-hub.al",
    "https://sci-hub.usualwant.com",
    "https://sci-hub.ru",
    "https://sci-hub.st",
]
SCIHUB_STORAGE = "https://sci.bban.top/pdf/{doi}.pdf?download=true"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def make_scraper():
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )


def safe_component(text: str, maxlen: int = 80) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    return text.strip("_")[:maxlen] or "untitled"


def target_filename(zotero_key: str, doi: str, title: str) -> str:
    tail = safe_component(doi) if doi else safe_component(title)
    return f"{zotero_key}_{tail}.pdf"


def is_pdf_bytes(content: bytes) -> bool:
    return content[:5].startswith(b"%PDF")


def citation_pdf_url(html: str) -> str | None:
    for pat in (
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def download_pdf(scraper, url: str, path, timeout: int = 60, referer: str | None = None) -> bool:
    try:
        headers = {"Referer": referer} if referer else None
        r = scraper.get(url, timeout=timeout, allow_redirects=True, headers=headers)
        if r.status_code != 200 or not is_pdf_bytes(r.content):
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    except Exception:
        return False


# --- API resolvers ---------------------------------------------------------

def unpaywall_urls(doi: str) -> tuple[list[str], list[str]]:
    """Return (direct_pdf_urls, landing_urls) from Unpaywall."""
    pdfs, landings = [], []
    try:
        d = requests.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": config.USER_EMAIL}, timeout=15,
        ).json()
        for loc in d.get("oa_locations", []) or []:
            if loc.get("url_for_pdf"):
                pdfs.append(loc["url_for_pdf"])
            for k in ("url", "url_for_landing_page"):
                if loc.get(k):
                    landings.append(loc[k])
    except Exception:
        pass
    return pdfs, landings


def openalex_urls(doi: str) -> list[str]:
    urls = []
    try:
        d = requests.get(
            f"https://api.openalex.org/works/doi:{doi}",
            headers={"User-Agent": f"mailto:{config.USER_EMAIL}"}, timeout=15,
        ).json()
        primary = (d.get("primary_location") or {}).get("pdf_url")
        if primary:
            urls.append(primary)
        for loc in d.get("locations", []) or []:
            if loc.get("pdf_url"):
                urls.append(loc["pdf_url"])
        oa = (d.get("open_access") or {}).get("oa_url")
        if oa:
            urls.append(oa)
    except Exception:
        pass
    return urls


def semantic_scholar_urls(doi: str) -> list[str]:
    try:
        headers = {}
        if config.SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = config.SEMANTIC_SCHOLAR_API_KEY
        d = requests.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "openAccessPdf"}, headers=headers, timeout=15,
        ).json()
        oa = (d.get("openAccessPdf") or {}).get("url")
        if oa:
            return [oa]
    except Exception:
        pass
    return []


def publisher_pattern_urls(final_url: str, html: str) -> list[str]:
    """Publisher-specific direct PDF URLs derived from the landing page."""
    urls = []
    meta = citation_pdf_url(html)
    if meta:
        urls.append(meta)
    if "jmir.org" in final_url:
        urls.append(final_url.rstrip("/") + "/PDF")
    if "mdpi.com" in final_url:
        urls.append(final_url.rstrip("/") + "/pdf")
    return urls


def _normalise_scihub_url(src: str, mirror: str) -> str:
    src = src.replace("\\/", "/").strip()          # un-escape JSON-style slashes
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return mirror + src
    return src


def scihub_candidates(scraper, doi: str) -> list[tuple[str, str | None]]:
    """Return ordered (pdf_url, referer) candidates from Sci-Hub.

    Fast path: the sci.bban.top storage CDN, which serves the PDF directly
    without the mirror CAPTCHA. Fallback: scrape each live mirror page for the
    embedded PDF link (usually also on sci.bban.top).
    """
    candidates: list[tuple[str, str | None]] = [
        (SCIHUB_STORAGE.format(doi=doi), "https://sci-hub.ee/"),
    ]
    for mirror in SCIHUB_MIRRORS:
        try:
            r = scraper.get(
                f"{mirror}/{doi}", timeout=20, allow_redirects=True,
                headers={"Referer": "https://www.google.com/"},
            )
            if r.status_code != 200 or not r.text:
                continue
            found = []
            for pat in (
                r'<(?:embed|iframe)[^>]+src\s*=\s*["\']([^"\']+)',
                r'["\'](https?:\\?/\\?/sci\.bban\.top/[^"\']+\.pdf[^"\']*)',
                r'location\.href\s*=\s*[\'"]([^\'"]+\.pdf[^\'"]*)',
                r'<a[^>]+href\s*=\s*["\']([^"\']+\.pdf[^"\']*)',
            ):
                found += re.findall(pat, r.text, re.I)
            for src in found:
                url = _normalise_scihub_url(src, mirror)
                if url.lower().split("?")[0].endswith(".pdf") or "sci.bban.top" in url:
                    candidates.append((url, mirror + "/"))
            if len(candidates) > 1:
                break  # got links from a live mirror; no need to try more
        except Exception:
            continue
    # de-duplicate, preserve order
    seen = set()
    out = []
    for url, ref in candidates:
        if url not in seen:
            seen.add(url)
            out.append((url, ref))
    return out


def resolve_and_download(scraper, doi: str, out_path, use_scihub: bool, scihub_only: bool = False) -> str | None:
    """Try every source in priority order. Returns the source name on success."""
    if not scihub_only:
        # 1) Direct OA PDF links
        up_pdfs, up_landings = unpaywall_urls(doi)
        ordered = (
            [(u, "unpaywall") for u in up_pdfs]
            + [(u, "openalex") for u in openalex_urls(doi)]
            + [(u, "semantic_scholar") for u in semantic_scholar_urls(doi)]
        )
        for url, source in ordered:
            if download_pdf(scraper, url, out_path):
                return source

        # 2) Publisher landing pages (DOI resolver + Unpaywall landing urls)
        seen = set()
        for landing in [f"https://doi.org/{doi}"] + up_landings:
            if landing in seen:
                continue
            seen.add(landing)
            try:
                r = scraper.get(landing, timeout=40, allow_redirects=True)
            except Exception:
                continue
            if r.status_code == 200 and is_pdf_bytes(r.content):
                with open(out_path, "wb") as f:
                    f.write(r.content)
                return "publisher_direct"
            for url in publisher_pattern_urls(r.url, r.text):
                if download_pdf(scraper, url, out_path):
                    return "publisher"

    # 3) Sci-Hub last resort
    if use_scihub:
        for url, referer in scihub_candidates(scraper, doi):
            if download_pdf(scraper, url, out_path, referer=referer):
                return "scihub"

    return None


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python step2_fetch_missing_pdfs.py <inventory_csv> [--no-scihub] [--scihub-only]")

    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    scihub_only = "--scihub-only" in sys.argv
    use_scihub = scihub_only or "--no-scihub" not in sys.argv

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""

    df["_has_pdf"] = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    todo = df[(~df["_has_pdf"]) & (df["pdf_path"] == "")].copy()
    mode = "Sci-Hub only" if scihub_only else ("Sci-Hub: on" if use_scihub else "Sci-Hub: off")
    log(f"References missing a PDF to process: {len(todo)}  ({mode})")

    scraper = make_scraper()
    found = 0
    processed = 0
    for idx, row in todo.iterrows():
        processed += 1
        zkey = row["zotero_key"]
        doi = (row["doi"] or "").strip()
        title = row["title"]

        if not doi:
            log(f"[{processed}/{len(todo)}] {zkey}: no DOI, skipping")
            continue

        out_path = config.PDF_DIR / target_filename(zkey, doi, title)
        if out_path.exists() and out_path.stat().st_size > 1000:
            df.at[idx, "pdf_path"] = str(out_path.relative_to(config.ROOT))
            df.at[idx, "pdf_source"] = df.at[idx, "pdf_source"] or "already_local"
            found += 1
            continue

        source = resolve_and_download(scraper, doi, out_path, use_scihub, scihub_only=scihub_only)
        if source:
            df.at[idx, "pdf_path"] = str(out_path.relative_to(config.ROOT))
            df.at[idx, "pdf_source"] = source
            found += 1
            log(f"[{processed}/{len(todo)}] {zkey}: OK via {source}")
        else:
            log(f"[{processed}/{len(todo)}] {zkey}: not found")

        if processed % CHECKPOINT_EVERY == 0:
            df.drop(columns=["_has_pdf"]).to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"  ...checkpoint saved ({found} found so far)")
        time.sleep(0.4)

    df.drop(columns=["_has_pdf"]).to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"Newly fetched PDFs: {found}/{len(todo)}")
    log(f"Updated inventory:  {csv_path}")
    log(f"PDFs saved in:      {config.PDF_DIR}")


if __name__ == "__main__":
    main()
