"""
Export the whole StrongMinds collection locally:
  * library_export/references_{ts}.csv  — all top-level references + metadata
  * library_export/pdfs/                 — every attached PDF, downloaded from Zotero

PDF files are named {zotero_key}_{firstauthor}_{year}.pdf and referenced by the
`pdf_file` column, so the CSV and the folder stay linked.

Run:
    python export_library.py
"""

import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

import config

BASE = f"{config.ZOTERO_API_BASE}/{config.LIBRARY_TYPE}/{config.LIBRARY_ID}"
H = {"Zotero-API-Key": config.get_zotero_api_key(), "User-Agent": "GE-ftr export"}

OUT = config.ROOT / "library_export"
PDFS = OUT / "pdfs"
PDFS.mkdir(parents=True, exist_ok=True)


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


def is_pdf_att(d):
    if d.get("itemType") != "attachment":
        return False
    ct = (d.get("contentType") or "").lower()
    fn = (d.get("filename") or d.get("title") or "").lower()
    return ct == "application/pdf" or fn.endswith(".pdf")


def authors_str(d):
    out = []
    for c in d.get("creators", []) or []:
        out.append(c.get("name") or f"{c.get('lastName','')}, {c.get('firstName','')}".strip(", "))
    return "; ".join(n for n in out if n)


def first_author(d):
    for c in d.get("creators", []) or []:
        return (c.get("lastName") or c.get("name") or "").split()[-1] if (c.get("lastName") or c.get("name")) else ""
    return ""


def year_of(d):
    date = d.get("date", "") or ""
    for tok in re.split(r"[^0-9]", date):
        if len(tok) == 4:
            return tok
    return ""


def safe(s, n=40):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "")).strip("-")[:n] or "x"


def download_file(att_key, dest) -> bool:
    try:
        r = requests.get(f"{BASE}/items/{att_key}/file", headers=H, timeout=90)
        if r.status_code == 200 and r.content[:5] == b"%PDF-":
            dest.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False


def main():
    items = fetch_all()
    children = {}
    for it in items:
        d = it["data"]
        if d.get("parentItem"):
            children.setdefault(d["parentItem"], []).append(d)

    skip = {"attachment", "note", "annotation"}
    tops = [it["data"] for it in items if it["data"].get("itemType") not in skip
            and not it["data"].get("parentItem")]
    log(f"Top-level references: {len(tops)}")

    rows = []
    got = 0
    for n, d in enumerate(tops, start=1):
        key = d["key"]
        # choose the best PDF attachment: prefer imported_file with md5, else any pdf with md5
        atts = [k for k in children.get(key, []) if is_pdf_att(k)]
        atts.sort(key=lambda a: (a.get("linkMode") != "imported_file", not a.get("md5")))
        pdf_file = ""
        for a in atts:
            fname = f"{key}_{safe(first_author(d))}_{year_of(d)}.pdf"
            dest = PDFS / fname
            if dest.exists() and dest.stat().st_size > 1000:
                pdf_file = fname
                got += 1
                break
            if a.get("md5") and download_file(a["key"], dest):
                pdf_file = fname
                got += 1
                break
        rows.append({
            "zotero_key": key,
            "item_type": d.get("itemType", ""),
            "title": d.get("title", ""),
            "authors": authors_str(d),
            "year": year_of(d),
            "doi": d.get("DOI", ""),
            "url": d.get("url", ""),
            "publication": d.get("publicationTitle", "") or d.get("publisher", ""),
            "has_pdf": bool(pdf_file),
            "pdf_file": pdf_file,
        })
        if n % 50 == 0:
            log(f"  processed {n}/{len(tops)} ({got} PDFs)")

    df = pd.DataFrame(rows).sort_values(["item_type", "authors", "year"])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT / f"references_{ts}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    log("-" * 60)
    log(f"References written:  {len(df)}  -> {csv_path}")
    log(f"PDFs downloaded:     {got}  -> {PDFS}")
    log(f"References with PDF:  {int(df['has_pdf'].sum())}  |  without: {int((~df['has_pdf']).sum())}")


if __name__ == "__main__":
    main()
