"""
Delete empty PDF attachment stubs (Zotero-Connector snapshots that are
linkMode=imported_url with NO stored file) from the collection.

SAFETY: before deleting each candidate it re-checks that the file endpoint does
NOT return a real PDF. Anything with a downloadable file is skipped.

Run:
    python delete_empty_attachments.py            # DRY RUN (lists what it would delete)
    python delete_empty_attachments.py --apply     # actually delete
"""

import sys
import time

import requests

import config

BASE = f"{config.ZOTERO_API_BASE}/{config.LIBRARY_TYPE}/{config.LIBRARY_ID}"
H = {"Zotero-API-Key": config.get_zotero_api_key(), "User-Agent": "GE-ftr cleanup"}


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


def has_real_file(key):
    try:
        r = requests.get(f"{BASE}/items/{key}/file", headers=H, timeout=40)
        return r.status_code == 200 and r.content[:5] == b"%PDF-"
    except Exception:
        return False  # treat unreachable as "no file" is unsafe; be conservative -> skip
    

def main():
    apply = "--apply" in sys.argv
    items = fetch_all()
    titles = {it["data"]["key"]: it["data"].get("title", "") for it in items}

    candidates = [it for it in items
                  if it["data"].get("itemType") == "attachment"
                  and is_pdfish(it["data"])
                  and it["data"].get("linkMode") == "imported_url"
                  and not it["data"].get("md5")]

    print(f"Empty imported_url PDF stubs (no md5): {len(candidates)}")
    print(f"Mode: {'APPLY (deleting)' if apply else 'DRY RUN'}\n")

    deleted, skipped, failed = 0, 0, 0
    for n, it in enumerate(candidates, start=1):
        d = it["data"]
        key = d["key"]
        parent = d.get("parentItem", "")
        # safety re-check: never delete something that actually serves a PDF
        try:
            fr = requests.get(f"{BASE}/items/{key}/file", headers=H, timeout=40)
            real = fr.status_code == 200 and fr.content[:5] == b"%PDF-"
        except Exception:
            real = None  # unknown -> skip to be safe
        if real is True:
            skipped += 1
            print(f"  [{n}] SKIP {key} (has real file!) parent={parent}")
            continue
        if real is None:
            skipped += 1
            print(f"  [{n}] SKIP {key} (file check failed, being cautious)")
            continue

        if not apply:
            deleted += 1  # would-delete count
            continue

        ver = it.get("version") or d.get("version")
        try:
            dr = requests.delete(f"{BASE}/items/{key}",
                                 headers={**H, "If-Unmodified-Since-Version": str(ver)}, timeout=30)
            if dr.status_code in (204, 200):
                deleted += 1
            else:
                failed += 1
                print(f"  [{n}] DELETE FAILED {key}: HTTP {dr.status_code}")
        except Exception as e:
            failed += 1
            print(f"  [{n}] DELETE ERROR {key}: {type(e).__name__}")
        time.sleep(0.15)

    print("-" * 60)
    if apply:
        print(f"Deleted: {deleted} | skipped (had file/unknown): {skipped} | failed: {failed}")
    else:
        print(f"Would delete: {deleted} | would skip: {skipped}")
        print("Re-run with --apply to perform the deletion.")


if __name__ == "__main__":
    main()
