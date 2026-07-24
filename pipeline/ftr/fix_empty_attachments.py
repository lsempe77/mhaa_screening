"""
Fix items whose only PDF attachment(s) are empty Zotero-Connector snapshots
(imported_url with no stored file). Fetches a real PDF and attaches it as a
proper imported_file.

Live from Zotero. Does NOT delete the empty stubs (a separate, confirmable step).

Run:
    python fix_empty_attachments.py            # fetch + attach real PDFs
    python fix_empty_attachments.py --list      # just list the affected items
"""

import sys
import time
from datetime import datetime

import requests

import config
from step2_fetch_missing_pdfs import make_scraper, resolve_and_download, target_filename
from step2b_no_doi import fetch_via_url
from step3_attach_to_zotero import create_attachment_item, upload_file

BASE = f"{config.ZOTERO_API_BASE}/{config.LIBRARY_TYPE}/{config.LIBRARY_ID}"
H = {"Zotero-API-Key": config.get_zotero_api_key(), "User-Agent": "GE-ftr fixempty"}


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}")
    sys.stdout.flush()


def fetch_all():
    items, start = [], 0
    while True:
        r = requests.get(f"{BASE}/collections/{config.COLLECTION_KEY}/items",
                         headers=H, params={"limit": 100, "start": start, "format": "json"}, timeout=30)
        batch = r.json()
        if not batch:
            break
        items.extend(batch)
        if len(items) >= int(r.headers.get("Total-Results", len(items))):
            break
        start += 100
        time.sleep(0.2)
    return items


def is_pdfish(d):
    return (d.get("contentType") or "").lower() == "application/pdf" or \
        (d.get("filename") or d.get("title") or "").lower().endswith(".pdf")


def main():
    list_only = "--list" in sys.argv
    key = config.get_zotero_api_key()
    items = fetch_all()
    by_key = {it["data"]["key"]: it["data"] for it in items}
    children = {}
    for it in items:
        d = it["data"]
        if d.get("parentItem"):
            children.setdefault(d["parentItem"], []).append(d)

    # parents whose PDF attachments ALL lack a stored file
    broken = []
    for pkey, kids in children.items():
        pdfk = [k for k in kids if is_pdfish(k)]
        if pdfk and all(not k.get("md5") and k.get("linkMode") in ("imported_file", "imported_url") for k in pdfk):
            if pkey in by_key and by_key[pkey].get("itemType") not in {"attachment", "note", "annotation"}:
                broken.append(pkey)

    log(f"Items with only empty PDF attachments: {len(broken)}")
    if list_only:
        for p in broken:
            d = by_key[p]
            print(f"  {p} | doi={d.get('DOI','') or '-':30} | {d.get('title','')[:60]}")
        return

    scraper = make_scraper()
    fixed = 0
    for n, pkey in enumerate(broken, start=1):
        d = by_key[pkey]
        doi = (d.get("DOI") or "").strip()
        url = (d.get("url") or "").strip()
        title = d.get("title", "")
        out_path = config.PDF_DIR / target_filename(pkey, doi, title)

        source = None
        if doi:
            source = resolve_and_download(scraper, doi, out_path, use_scihub=True)
        if not source and url:
            source = fetch_via_url(scraper, url, out_path)
        if not source:
            log(f"[{n}/{len(broken)}] {pkey}: no PDF found (doi={doi or '-'})")
            continue
        try:
            akey = create_attachment_item(key, pkey, out_path.name)
            status = upload_file(key, akey, out_path)
            fixed += 1
            log(f"[{n}/{len(broken)}] {pkey}: OK via {source} -> attached ({status})")
        except Exception as e:
            log(f"[{n}/{len(broken)}] {pkey}: fetched but attach FAILED: {type(e).__name__}")
        time.sleep(0.4)

    log("-" * 60)
    log(f"Real PDFs attached: {fixed}/{len(broken)}")
    log("Empty stubs left in place (run a cleanup step separately if wanted).")


if __name__ == "__main__":
    main()
