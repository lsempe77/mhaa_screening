"""
Step 0: Build the reference <-> PDF inventory from the RIS file of INCLUDEs.

This replaces step1_export_zotero.py for the StrongMinds ULCM project. Instead of
exporting from a Zotero collection, it reads the RIS file produced by export_ris.py
(the 4,125 INCLUDEs from TAS screening) and builds the same inventory CSV format
that steps 2-3 expect.

The RIS file uses EPPI IDs as record_id (U1 field), not Zotero keys. If you want
to attach PDFs back to Zotero later (step 3), you'll need to import the RIS into
Zotero first and then run step1_export_zotero.py to get the Zotero keys. For now,
this step builds a DOI-based inventory that steps 2-3 can use for retrieval.

Output: logs/inventory_{timestamp}.csv with one row per included reference.

Run:
    python projects/strongminds/full_text_retrieval/scripts/step0_build_inventory.py
"""

import sys
import re
import csv
from datetime import datetime
from pathlib import Path

import config

TAG_RE = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")


def parse_ris(path: Path) -> list[dict]:
    """Parse RIS file -> list of dicts. Each dict maps tag -> list of values."""
    recs = []
    cur = {}
    last_tag = None
    for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = raw.rstrip("\r")
        m = TAG_RE.match(line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "ER":
                if cur:
                    recs.append(cur)
                cur = {}
                last_tag = None
            else:
                cur.setdefault(tag, []).append(val)
                last_tag = tag
        elif line.strip() and last_tag:
            cur[last_tag][-1] = (cur[last_tag][-1] + " " + line.strip()).strip()
    if cur:
        recs.append(cur)
    return recs


def first(d, *tags):
    for t in tags:
        if d.get(t) and d[t][0].strip():
            return d[t][0].strip()
    return ""


def main():
    ris_path = config.RIS_FILE
    if not ris_path.exists():
        sys.exit(f"RIS file not found: {ris_path}")

    print(f"Reading RIS: {ris_path}")
    recs = parse_ris(ris_path)
    print(f"  {len(recs)} records")

    rows = []
    for d in recs:
        rid = first(d, "U1")
        doi = first(d, "DO").lower()
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        title = first(d, "T1", "TI")
        year = first(d, "PY", "Y1")
        abstract = first(d, "AB", "N2")
        rtype = first(d, "TY")

        rows.append({
            "zotero_key": rid,  # EPPI ID used as the anchor (not a Zotero key yet)
            "item_type": rtype,
            "title": title,
            "authors": "",
            "year": year,
            "doi": doi,
            "url": "",
            "has_pdf": False,
            "pdf_attachment_key": "",
            "existing_pdf_filename": "",
            "pdf_path": "",
            "pdf_source": "",
            "abstract": abstract[:500],
        })

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = config.LOG_DIR / f"inventory_{ts}.csv"

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[
            "zotero_key", "item_type", "title", "authors", "year", "doi", "url",
            "has_pdf", "pdf_attachment_key", "existing_pdf_filename",
            "pdf_path", "pdf_source", "abstract",
        ])
        w.writeheader()
        w.writerows(rows)

    n_total = len(rows)
    n_with_doi = sum(1 for r in rows if r["doi"])
    n_no_doi = n_total - n_with_doi

    print("-" * 60)
    print(f"References:              {n_total}")
    print(f"  with DOI:              {n_with_doi} ({n_with_doi/n_total*100:.1f}%)")
    print(f"  without DOI:            {n_no_doi}")
    print(f"Inventory written to:    {out_path}")
    print("-" * 60)
    print(f"Next: python scripts/step1b_find_dois.py {out_path.name}")
    print(f"      (recover DOIs for the {n_no_doi} without, via CrossRef)")
    print(f"Then: python scripts/step2_fetch_missing_pdfs.py {out_path.name}")


if __name__ == "__main__":
    main()
