"""inggest_fts_strongminds.py — Extract text from retrieved PDFs for full-text screening.

Reads the FTR inventory CSV, extracts text from each PDF with a pdf_path,
and writes a records JSONL for the orchestrator/k5_runner to screen on full text.

Usage:
    python pipeline/ingest_fts_strongminds.py
        --csv projects/strongminds/full_text_retrieval/logs/inventory_merged.csv
        --pdfs-dir projects/strongminds/full_text_retrieval/pdfs
        --out-dir projects/strongminds/data/fts
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path

try:
    import csv
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF not installed. Run: pip install pymupdf")

MAX_CHARS = 400_000
MIN_CHARS = 200


def extract_pdf_text(pdf_path: Path) -> tuple[str, int]:
    with fitz.open(pdf_path) as doc:
        n_pages = doc.page_count
        pages = []
        for page in doc:
            pages.append(page.get_text("text") or "")
        text = "\n\f\n".join(pages)
    return text, n_pages


def truncate(text: str, limit: int = MAX_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n\f\n", 0, limit)
    if cut == -1 or cut < limit * 0.8:
        cut = text.rfind("\n\n", 0, limit)
    if cut == -1 or cut < limit * 0.8:
        cut = limit
    return text[:cut], True


def main():
    p = argparse.ArgumentParser(description="Extract PDF text for StrongMinds full-text screening.")
    p.add_argument("--csv", required=True, help="FTR inventory CSV")
    p.add_argument("--pdfs-dir", required=True, help="Directory containing the PDFs")
    p.add_argument("--out-dir", default="projects/strongminds/data/fts", help="Output directory")
    p.add_argument("--max-chars", type=int, default=MAX_CHARS)
    p.add_argument("--min-chars", type=int, default=MIN_CHARS)
    args = p.parse_args()

    pdfs_dir = Path(args.pdfs_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read inventory CSV
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig")))
    print(f"Loaded {len(rows)} rows from inventory")

    # Filter to records with PDFs
    pdf_rows = [r for r in rows if r.get("pdf_path", "").strip()]
    print(f"Records with PDF path: {len(pdf_rows)}")

    records = []
    missing = []
    low_text = []
    truncated = []

    for n, r in enumerate(pdf_rows, start=1):
        zkey = r.get("zotero_key", "")
        title = r.get("title", "NA")
        year = r.get("year", "NA")
        doi = r.get("doi", "")
        rel_path = r.get("pdf_path", "")
        pdf_path = pdfs_dir / Path(rel_path).name

        if not pdf_path.exists():
            # Try the full relative path
            pdf_path = pdfs_dir.parent / rel_path
            if not pdf_path.exists():
                missing.append({"record_id": zkey, "title": title, "pdf_path": rel_path})
                continue

        try:
            text, n_pages = extract_pdf_text(pdf_path)
        except Exception as e:
            print(f"  [{n}/{len(pdf_rows)}] {zkey}: ERROR {e}")
            missing.append({"record_id": zkey, "title": title, "pdf_path": rel_path, "error": str(e)})
            continue

        text, was_truncated = truncate(text, args.max_chars)

        if len(text) < args.min_chars:
            low_text.append({"record_id": zkey, "title": title, "n_chars": len(text), "n_pages": n_pages})
            # Still include it — the model can flag it

        if was_truncated:
            truncated.append({"record_id": zkey, "title": title, "n_chars": len(text), "n_pages": n_pages})

        records.append({
            "record_id": zkey,
            "year": year,
            "title": title,
            "abstract": text,  # full text in abstract field (same as GE FTS)
            "doi": doi,
            "screening_level": "full_text",
            "n_pages": n_pages,
            "n_chars": len(text),
            "pdf_file": pdf_path.name,
        })

        if n % 100 == 0:
            print(f"  [{n}/{len(pdf_rows)}] processed...")

    # Write records
    rec_path = out_dir / f"records_fts_{len(records)}.jsonl"
    with open(rec_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Write audit logs
    if missing:
        m_path = out_dir / "missing_pdf.jsonl"
        with open(m_path, "w", encoding="utf-8") as f:
            for m in missing:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    if low_text:
        lt_path = out_dir / "low_text.jsonl"
        with open(lt_path, "w", encoding="utf-8") as f:
            for lt in low_text:
                f.write(json.dumps(lt, ensure_ascii=False) + "\n")

    if truncated:
        tr_path = out_dir / "truncated.jsonl"
        with open(tr_path, "w", encoding="utf-8") as f:
            for tr in truncated:
                f.write(json.dumps(tr, ensure_ascii=False) + "\n")

    print(f"\n=== FTS Ingestion Summary ===")
    print(f"Records with PDF path:     {len(pdf_rows)}")
    print(f"Successfully extracted:    {len(records)}")
    print(f"Missing/broken PDFs:       {len(missing)}")
    print(f"Low text (< {MIN_CHARS} chars): {len(low_text)}")
    print(f"Truncated (> {MAX_CHARS:,} chars): {len(truncated)}")
    print(f"\nRecords written to: {rec_path}")


if __name__ == "__main__":
    main()
