"""Query the StrongMinds Zotero collection LIVE and count how many top-level
references currently have (or lack) a PDF attachment."""
import time

import requests

import config

KEY = config.get_zotero_api_key()
BASE = f"{config.ZOTERO_API_BASE}/{config.LIBRARY_TYPE}/{config.LIBRARY_ID}"
H = {"Zotero-API-Key": KEY, "User-Agent": "GE-ftr audit"}


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
        total = int(r.headers.get("Total-Results", len(items)))
        if len(items) >= total:
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


items = fetch_all()
pdf_parents = set()
for it in items:
    d = it.get("data", {})
    if is_pdf_att(d) and d.get("parentItem"):
        pdf_parents.add(d["parentItem"])

skip = {"attachment", "note", "annotation"}
tops = [it for it in items if it["data"].get("itemType") not in skip and not it["data"].get("parentItem")]
have = [it for it in tops if it["data"]["key"] in pdf_parents]
missing = [it for it in tops if it["data"]["key"] not in pdf_parents]

print(f"Top-level references in collection: {len(tops)}")
print(f"  WITH a PDF attached:  {len(have)}  ({100*len(have)/len(tops):.0f}%)")
print(f"  MISSING a PDF:        {len(missing)}  ({100*len(missing)/len(tops):.0f}%)")
print("\nMissing items:")
for it in missing:
    d = it["data"]
    print(f"  {d['key']}  [{d.get('itemType')}]  {(d.get('title') or '')[:75]}")
