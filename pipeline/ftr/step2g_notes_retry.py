"""
Step 2g: Retry fetching PDFs for references still missing one, working LIVE from
Zotero and RESPECTING the user's 'Exclude' notes.

- Queries the collection live (so it sees current DOIs/URLs and attachments).
- Skips items whose note says 'exclude' and obvious registry stubs (ISRCTN).
- For each remaining item: tries the OA/publisher chain by DOI, then the item's
  URL, then Sci-Hub. Attaches any PDF found straight to the Zotero item.

Run:
    python step2g_notes_retry.py
    python step2g_notes_retry.py --no-scihub
"""

import re
import sys
import time
from datetime import datetime

import requests

import config
from step2_fetch_missing_pdfs import make_scraper, resolve_and_download, target_filename
from step2b_no_doi import fetch_via_url
from step3_attach_to_zotero import create_attachment_item, upload_file

BASE = f"{config.ZOTERO_API_BASE}/{config.LIBRARY_TYPE}/{config.LIBRARY_ID}"
H = {"Zotero-API-Key": config.get_zotero_api_key(), "User-Agent": "GE-ftr retry"}


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}")
    sys.stdout.flush()


def fetch_all():
    items, start = [], 0
    while True:
        r = requests.get(f"{BASE}/collections/{config.COLLECTION_KEY}/items",
                         headers=H, params={"limit": 100, "start": start, "format": "json"}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        items.extend(batch)
        if len(items) >= int(r.headers.get("Total-Results", len(items))):
            break
        start += 100
        time.sleep(0.2)
    return items


def is_pdf_att(d):
    if d.get("itemType") != "attachment":
        return False
    ct = (d.get("contentType") or "").lower()
    fn = (d.get("filename") or d.get("title") or "").lower()
    return ct == "application/pdf" or fn.endswith(".pdf")


def main():
    use_scihub = "--no-scihub" not in sys.argv
    key = config.get_zotero_api_key()
    items = fetch_all()

    pdf_parents = {it["data"]["parentItem"] for it in items
                   if is_pdf_att(it.get("data", {})) and it["data"].get("parentItem")}
    notes = {}
    for it in items:
        d = it.get("data", {})
        if d.get("itemType") == "note" and d.get("parentItem"):
            notes.setdefault(d["parentItem"], []).append(d.get("note", ""))

    skip = {"attachment", "note", "annotation"}
    missing = [it for it in items if it["data"].get("itemType") not in skip
               and not it["data"].get("parentItem") and it["data"]["key"] not in pdf_parents]

    todo = []
    for it in missing:
        d = it["data"]
        ntxt = re.sub(r"<[^>]+>", " ", " ".join(notes.get(d["key"], []))).lower()
        if "exclude" in ntxt:
            continue
        doi = (d.get("DOI") or "").strip()
        if "ISRCTN" in doi.upper():
            continue
        todo.append(d)

    log(f"Missing: {len(missing)} | after excludes/stubs, retrying: {len(todo)}  (Sci-Hub {'on' if use_scihub else 'off'})")

    scraper = make_scraper()
    found = 0
    for n, d in enumerate(todo, start=1):
        zkey = d["key"]
        doi = (d.get("DOI") or "").strip()
        url = (d.get("url") or "").strip()
        title = d.get("title", "")
        out_path = config.PDF_DIR / target_filename(zkey, doi, title)

        source = None
        if doi:
            source = resolve_and_download(scraper, doi, out_path, use_scihub)
        if not source and url:
            src = fetch_via_url(scraper, url, out_path)
            source = src
        if not source:
            log(f"[{n}/{len(todo)}] {zkey}: not found (doi={doi or '-'})")
            continue

        # attach to Zotero
        try:
            akey = create_attachment_item(key, zkey, out_path.name)
            status = upload_file(key, akey, out_path)
            found += 1
            log(f"[{n}/{len(todo)}] {zkey}: OK via {source} -> attached ({status})")
        except Exception as e:
            log(f"[{n}/{len(todo)}] {zkey}: fetched via {source} but attach FAILED: {type(e).__name__}")
        time.sleep(0.4)

    log("-" * 60)
    log(f"Newly fetched & attached: {found}/{len(todo)}")


if __name__ == "__main__":
    main()
