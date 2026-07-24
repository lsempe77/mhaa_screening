"""Export a CSV of the references in the StrongMinds collection that currently
have NO PDF attached (queried live from Zotero)."""
import time
from datetime import datetime

import pandas as pd
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


def authors(creators):
    names = []
    for c in creators or []:
        if c.get("name"):
            names.append(c["name"])
        else:
            names.append(f"{c.get('lastName','')}, {c.get('firstName','')}".strip(", "))
    return "; ".join(n for n in names if n)


items = fetch_all()
pdf_parents = {it["data"]["parentItem"] for it in items
               if is_pdf_att(it.get("data", {})) and it["data"].get("parentItem")}

skip = {"attachment", "note", "annotation"}
rows = []
for it in items:
    d = it.get("data", {})
    if d.get("itemType") in skip or d.get("parentItem"):
        continue
    if d["key"] in pdf_parents:
        continue
    rows.append({
        "zotero_key": d.get("key", ""),
        "item_type": d.get("itemType", ""),
        "title": d.get("title", ""),
        "authors": authors(d.get("creators", [])),
        "year": (d.get("date", "") or "")[:4] if any(ch.isdigit() for ch in (d.get("date", "") or "")[:4]) else d.get("date", ""),
        "doi": d.get("DOI", ""),
        "url": d.get("url", ""),
        "publication": d.get("publicationTitle", "") or d.get("publisher", ""),
        "zotero_link": f"https://www.zotero.org/groups/{config.LIBRARY_ID}/items/{d.get('key','')}",
    })

df = pd.DataFrame(rows).sort_values(["item_type", "title"])
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = config.ROOT / f"missing_pdfs_{ts}.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")
print(f"Wrote {out}")
print(f"Missing references: {len(df)}")
print(df["item_type"].value_counts().to_string())
