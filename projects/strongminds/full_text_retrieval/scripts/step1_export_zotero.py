"""
Step 1: Export the StrongMinds collection from Zotero and build the
reference <-> PDF inventory.

Output: logs/inventory_{timestamp}.csv with one row per top-level reference.

Key columns (the reference <-> PDF link is preserved via `zotero_key`):
  zotero_key            Unique Zotero item key (the anchor for every later step)
  item_type, title, authors, year, doi, url
  has_pdf               True if the item already has a PDF attached in Zotero
  pdf_attachment_key    Zotero key of the existing PDF attachment (if any)
  existing_pdf_filename Filename of the existing PDF attachment (if any)
  pdf_path              Local path once fetched (filled by step 2). Empty here.
  pdf_source            Where the PDF came from (filled by step 2). Empty here.

Run:
    python step1_export_zotero.py
"""

import sys
import time
from datetime import datetime

import pandas as pd
import requests

import config


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def zotero_get(endpoint: str, params: dict, api_key: str) -> requests.Response:
    url = f"{config.ZOTERO_API_BASE}/{config.LIBRARY_TYPE}/{config.LIBRARY_ID}{endpoint}"
    headers = {
        "Zotero-API-Key": api_key,
        "User-Agent": "GE-ftr PDF pipeline",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp


def fetch_all_items(api_key: str) -> list[dict]:
    """Fetch every item in the collection (top-level references AND child
    attachments/notes) with pagination."""
    endpoint = f"/collections/{config.COLLECTION_KEY}/items"
    items: list[dict] = []
    start = 0
    limit = 100
    while True:
        resp = zotero_get(endpoint, {"limit": limit, "start": start, "format": "json"}, api_key)
        batch = resp.json()
        if not batch:
            break
        items.extend(batch)
        total = int(resp.headers.get("Total-Results", len(items)))
        log(f"  fetched {len(items)}/{total} items")
        start += limit
        if len(items) >= total:
            break
        time.sleep(0.2)  # be gentle with the API
    return items


def is_pdf_attachment(data: dict) -> bool:
    if data.get("itemType") != "attachment":
        return False
    content_type = (data.get("contentType") or "").lower()
    filename = (data.get("filename") or "").lower()
    title = (data.get("title") or "").lower()
    return (
        content_type == "application/pdf"
        or filename.endswith(".pdf")
        or title.endswith(".pdf")
    )


def format_authors(creators: list[dict]) -> str:
    names = []
    for c in creators or []:
        if c.get("name"):
            names.append(c["name"])
        else:
            last = c.get("lastName", "")
            first = c.get("firstName", "")
            names.append(f"{last}, {first}".strip(", "))
    return "; ".join(n for n in names if n)


def extract_year(data: dict) -> str:
    date = data.get("date", "") or ""
    for token in date.replace("-", " ").replace("/", " ").split():
        if len(token) == 4 and token.isdigit():
            return token
    return ""


def main() -> None:
    api_key = config.get_zotero_api_key()
    log(f"Exporting collection {config.COLLECTION_KEY} from group {config.LIBRARY_ID}")

    items = fetch_all_items(api_key)
    log(f"Total items retrieved: {len(items)}")

    # Map parent item key -> list of PDF attachment (key, filename)
    pdf_attachments: dict[str, list[tuple[str, str]]] = {}
    for it in items:
        data = it.get("data", {})
        if is_pdf_attachment(data):
            parent = data.get("parentItem")
            if parent:
                fname = data.get("filename") or data.get("title") or ""
                pdf_attachments.setdefault(parent, []).append((data.get("key", ""), fname))

    # Build one row per top-level reference (skip attachments/notes and standalone attachments)
    rows = []
    skip_types = {"attachment", "note", "annotation"}
    for it in items:
        data = it.get("data", {})
        item_type = data.get("itemType", "")
        if item_type in skip_types:
            continue
        if data.get("parentItem"):
            continue  # safety: only top-level references

        key = data.get("key", "")
        attachments = pdf_attachments.get(key, [])
        has_pdf = len(attachments) > 0

        rows.append(
            {
                "zotero_key": key,
                "item_type": item_type,
                "title": data.get("title", ""),
                "authors": format_authors(data.get("creators", [])),
                "year": extract_year(data),
                "doi": (data.get("DOI", "") or "").strip(),
                "url": data.get("url", ""),
                "has_pdf": has_pdf,
                "pdf_attachment_key": attachments[0][0] if has_pdf else "",
                "existing_pdf_filename": attachments[0][1] if has_pdf else "",
                "pdf_path": "",   # filled by step 2
                "pdf_source": "",  # filled by step 2
            }
        )

    df = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = config.LOG_DIR / f"inventory_{ts}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    n_total = len(df)
    n_with = int(df["has_pdf"].sum())
    n_missing = n_total - n_with
    n_missing_with_doi = int(((~df["has_pdf"]) & (df["doi"] != "")).sum())

    log("-" * 60)
    log(f"References:                {n_total}")
    log(f"  already have a PDF:      {n_with}")
    log(f"  missing a PDF:           {n_missing}")
    log(f"    of those, have a DOI:  {n_missing_with_doi}")
    log(f"Inventory written to:      {out_path}")
    log("-" * 60)
    log("Next: python step2_fetch_missing_pdfs.py " + str(out_path.name))


if __name__ == "__main__":
    main()
