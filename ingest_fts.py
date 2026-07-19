"""
ingest_fts.py — Full-text variant of ingest.py for the GE Zotero PDF set.

Reads the Zotero references CSV (GE_FTS/references_*.csv), extracts text from each PDF
in GE_FTS/pdfs/ via PyMuPDF (fitz), and writes the same records.jsonl format that
k5_runner.py consumes — with the full PDF text in the `abstract` field (so the existing
prompt/runner pipeline works unchanged).

Outputs:
    GE_FTS/data/records_<n>.jsonl   one record per PDF-bearing row, fields:
                                      record_id, year, title, abstract (full text),
                                      n_pages, n_chars, pdf_file
    GE_FTS/data/missing_pdf.jsonl   the rows with has_pdf=False (for separate follow-up)

Usage:
    python ingest_fts.py \
        --csv GE_FTS/references_20260718_204803.csv \
        --pdfs-dir GE_FTS/pdfs \
        --out-dir GE_FTS/data

Requires: PyMuPDF (fitz), pandas. `pip install pymupdf pandas`
"""
from __future__ import annotations
import argparse, json, math, re, sys
from pathlib import Path

import pandas as pd

# Force UTF-8 stdout so arrows and unicode don't crash on cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF not installed. Run: pip install pymupdf")


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _clean(v, default: str = "NA") -> str:
    if v is None or _is_nan(v):
        return default
    s = str(v).strip()
    return s if s else default


# Roughly 1 token ~= 4 chars for English text. GLM-5.2 context on OpenRouter is ~131k tokens.
# Leave headroom for the system prompt + response; cap at 400k chars (~100k tokens).
MAX_CHARS = 400_000

# Below this, the PDF is almost certainly a scanned image or extraction failed.
MIN_CHARS = 200


def extract_pdf_text(pdf_path: Path) -> tuple[str, int]:
    """Return (text, n_pages). Text is the concatenation of all pages, page-break separated."""
    with fitz.open(pdf_path) as doc:
        n_pages = doc.page_count
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text("text") or "")
        text = "\n\f\n".join(pages)  # form-feed page separators
    return text, n_pages


def truncate(text: str, limit: int = MAX_CHARS) -> tuple[str, bool]:
    """Hard-truncate to `limit` chars. Returns (text, was_truncated)."""
    if len(text) <= limit:
        return text, False
    # Try to cut on a paragraph/page boundary near the limit to avoid mid-sentence splits.
    cut = text.rfind("\n\f\n", 0, limit)
    if cut == -1 or cut < limit * 0.8:
        cut = text.rfind("\n\n", 0, limit)
    if cut == -1 or cut < limit * 0.8:
        cut = limit
    return text[:cut], True


def main():
    p = argparse.ArgumentParser(description="Ingest GE Zotero PDFs → records.jsonl for k5_runner.")
    p.add_argument("--csv", required=True, help="Path to the Zotero references CSV.")
    p.add_argument("--pdfs-dir", required=True, help="Directory containing the PDFs.")
    p.add_argument("--out-dir", default="GE_FTS/data", help="Output directory.")
    p.add_argument("--max-chars", type=int, default=MAX_CHARS,
                   help=f"Hard char cap per record (default {MAX_CHARS:,}).")
    p.add_argument("--min-chars", type=int, default=MIN_CHARS,
                   help=f"Below this, flag the PDF as a likely scan/extraction failure "
                        f"(default {MIN_CHARS}).")
    args = p.parse_args()

    csv_path = Path(args.csv)
    pdfs_dir = Path(args.pdfs_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8")
    print(f"Loaded {len(df)} rows from {csv_path.name}")
    print(f"Columns: {list(df.columns)}")

    # Column names from the Zotero export
    col_id = "zotero_key"
    col_year = "year"
    col_title = "title"
    col_pdf = "pdf_file"
    col_has_pdf = "has_pdf"
    for c in (col_id, col_year, col_title, col_pdf, col_has_pdf):
        if c not in df.columns:
            sys.exit(f"Column {c!r} not found in {list(df.columns)}")

    records: list[dict] = []
    missing: list[dict] = []
    low_text: list[dict] = []
    truncated: list[dict] = []
    errors: list[dict] = []

    for _, row in df.iterrows():
        rid = _clean(row[col_id])
        year = _clean(row[col_year])
        title = _clean(row[col_title], default="")
        has_pdf = str(row[col_has_pdf]).strip().lower() in ("true", "1", "yes")
        pdf_file = _clean(row[col_pdf], default="")

        if not has_pdf or pdf_file == "NA":
            missing.append({
                "record_id": rid, "year": year, "title": title,
                "pdf_file": pdf_file, "reason": "no_pdf_in_csv",
            })
            continue

        pdf_path = pdfs_dir / pdf_file
        if not pdf_path.exists():
            missing.append({
                "record_id": rid, "year": year, "title": title,
                "pdf_file": pdf_file, "reason": "pdf_file_missing_on_disk",
            })
            continue

        try:
            text, n_pages = extract_pdf_text(pdf_path)
        except Exception as e:
            errors.append({
                "record_id": rid, "year": year, "title": title,
                "pdf_file": pdf_file, "error": str(e),
            })
            continue

        n_chars = len(text)
        if n_chars < args.min_chars:
            low_text.append({
                "record_id": rid, "year": year, "title": title,
                "pdf_file": pdf_file, "n_pages": n_pages, "n_chars": n_chars,
                "reason": "low_text_likely_scanned",
            })

        text, was_truncated = truncate(text, args.max_chars)
        if was_truncated:
            truncated.append({
                "record_id": rid, "year": year, "title": title,
                "pdf_file": pdf_file, "n_chars_original": n_chars,
                "n_chars_truncated": len(text),
            })

        records.append({
            "record_id": rid,
            "year": year,
            "title": title,
            "abstract": text,  # field name kept for k5_runner compatibility
            "n_pages": n_pages,
            "n_chars": len(text),
            "pdf_file": pdf_file,
        })

    n = len(records)
    records_path = out_dir / f"records_{n}.jsonl"
    with records_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Sidecar logs
    def _write_jsonl(name: str, rows: list[dict]) -> Path | None:
        if not rows:
            return None
        path = out_dir / name
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return path

    missing_path = _write_jsonl("missing_pdf.jsonl", missing)
    low_path = _write_jsonl("low_text.jsonl", low_text)
    trunc_path = _write_jsonl("truncated.jsonl", truncated)
    err_path = _write_jsonl("extraction_errors.jsonl", errors)

    # Stats
    total = len(df)
    print(f"\n=== Ingest summary ===")
    print(f"  Total CSV rows:          {total}")
    print(f"  Records written:         {n}  → {records_path}")
    if missing_path:
        print(f"  Missing PDF (skipped):   {len(missing)}  → {missing_path}")
    if low_path:
        print(f"  Low-text (likely scan):  {len(low_text)}  → {low_path}")
    if trunc_path:
        print(f"  Truncated to cap:        {len(truncated)}  → {trunc_path}")
    if err_path:
        print(f"  Extraction errors:       {len(errors)}  → {err_path}")

    if records:
        n_pages_vals = [r["n_pages"] for r in records]
        n_chars_vals = [r["n_chars"] for r in records]
        print(f"\n  Pages: min={min(n_pages_vals)} max={max(n_pages_vals)} "
              f"mean={sum(n_pages_vals)/len(n_pages_vals):.1f}")
        print(f"  Chars: min={min(n_chars_vals):,} max={max(n_chars_vals):,} "
              f"mean={sum(n_chars_vals)/len(n_chars_vals):,.0f}")
        empty = sum(1 for r in records if r["n_chars"] < args.min_chars)
        print(f"  Records below {args.min_chars} chars (flagged): {empty}")

    print(f"\nNext step:")
    print(f"  python k5_runner.py \\")
    print(f"      --prompt prompts/prompts-screening-mhaa-fulltext-v1.md \\")
    print(f"      --records {records_path} \\")
    print(f"      --out GE_FTS/output/results_fts_glm_{n}.jsonl \\")
    print(f"      --k 1 --temperature 0 \\")
    print(f"      --models z-ai/glm-5.2 \\")
    print(f"      --workers 5")


if __name__ == "__main__":
    main()
