"""
Match manually-downloaded PDFs (with arbitrary publisher filenames) in pdfs/ to
their Zotero items, by reading the DOI from each PDF (metadata + first pages),
falling back to fuzzy title matching. Sets pdf_path/pdf_source in the inventory
so step3_attach_to_zotero.py can upload them.

Run:
    python match_manual_pdfs.py inventory_{timestamp}.csv
    python step3_attach_to_zotero.py inventory_{timestamp}.csv
"""

import os
import re
import sys
from difflib import SequenceMatcher

import fitz  # PyMuPDF
import pandas as pd

import config

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]}]+", re.I)


def clean_doi(doi: str) -> str:
    return doi.strip().rstrip(".,;:)]}").lower()


def pdf_dois_and_title(path):
    dois, title = [], ""
    try:
        doc = fitz.open(path)
    except Exception:
        return dois, title
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip()
        blob = " ".join(str(v) for v in meta.values())
        dois += DOI_RE.findall(blob)
        for pg in range(min(3, doc.page_count)):
            txt = doc.load_page(pg).get_text()
            dois += DOI_RE.findall(txt)
            if pg == 0 and not title:
                # first non-trivial line as a rough title
                for line in txt.splitlines():
                    if len(line.strip()) > 25:
                        title = line.strip()
                        break
    finally:
        doc.close()
    # dedupe, cleaned
    seen, out = set(), []
    for d in dois:
        c = clean_doi(d)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out, title


def norm_title(t):
    t = re.sub(r"<[^>]+>", " ", (t or "").lower())
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python match_manual_pdfs.py <inventory_csv>")
    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    missing = df[(~has_pdf) & (df["pdf_path"].str.strip() == "")]
    # lookups
    doi_to_idx = {clean_doi(r["doi"]): idx for idx, r in missing.iterrows() if r["doi"].strip()}
    title_idx = [(idx, norm_title(r["title"])) for idx, r in missing.iterrows() if r["title"].strip()]

    tracked = set(os.path.basename(p) for p in df["pdf_path"] if p.strip())
    manual = [f for f in sorted(config.PDF_DIR.glob("*.pdf")) if f.name not in tracked]
    print(f"Untracked PDFs to match: {len(manual)}")

    used_idx = set()
    matched = 0
    unmatched = []
    for f in manual:
        dois, title = pdf_dois_and_title(f)
        hit = None
        for d in dois:
            if d in doi_to_idx and doi_to_idx[d] not in used_idx:
                hit = doi_to_idx[d]
                break
        if hit is None and title:
            nt = norm_title(title)
            best, bi = 0.0, None
            for idx, t in title_idx:
                if idx in used_idx:
                    continue
                s = SequenceMatcher(None, nt, t).ratio()
                if s > best:
                    best, bi = s, idx
            if bi is not None and best >= 0.80:
                hit = bi
        if hit is not None:
            used_idx.add(hit)
            df.at[hit, "pdf_path"] = str(f.relative_to(config.ROOT))
            df.at[hit, "pdf_source"] = "manual_oxford"
            matched += 1
            print(f"  OK  {df.at[hit,'zotero_key']}  <-  {f.name[:60]}")
        else:
            unmatched.append(f.name)

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nMatched: {matched}/{len(manual)}")
    if unmatched:
        print(f"Unmatched ({len(unmatched)}) - need manual mapping:")
        for u in unmatched:
            print("   ", u)
    print(f"\nNext: python step3_attach_to_zotero.py {csv_path.name}")


if __name__ == "__main__":
    main()
