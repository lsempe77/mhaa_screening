"""ingest_extraction_strongminds.py — Build the ULCM extraction input from the FTS includes.

The FTS *screening* records (records_fts_2721.jsonl) are unfit for extraction:
  - 42 of the 1,769 includes were truncated at the 400k-char screening cap, losing the
    paper tail (Results tables, cost, follow-up) that extraction needs;
  - they carry no authors / item_type (those live in the FTR inventory);
  - the text is raw page-concatenation, not section-segmented (the protocol wants
    `segmented_full_text`).

This ingest fixes all three. For every INCLUDE in the tiebreak results it:
  1. re-extracts the PDF text with PyMuPDF, UNCAPPED;
  2. segments it into labelled sections (Abstract / Methods / Results / Tables /
     Discussion / Conclusion / Cost / References / …);
  3. applies a generous char cap with SECTION-AWARE pruning (drop references /
     appendices / acknowledgements first, never Results/Methods) so even a 967-page
     umbrella review fits a sane token budget;
  4. joins bibliographic metadata (authors, item_type, doi, year) from the inventory;
  5. carries the screening route hint (`_router.routes`) as rq_tags_hint.

Output: records_extract_<n>.jsonl — one record per included study, ready for the
extraction harness (unit_of_extraction, segmented_full_text, metadata, provenance).

Usage:
    python pipeline/extraction/ingest_extraction_strongminds.py \
        --results   projects/strongminds/data/output/results_fts_v19_tiebreak.jsonl \
        --fts-records projects/strongminds/data/fts/records_fts_2721.jsonl \
        --inventory projects/strongminds/full_text_retrieval/logs/inventory_merged.csv \
        --pdfs-dir  projects/strongminds/full_text_retrieval/pdfs \
        --out-dir   projects/strongminds/data/extraction \
        [--cap 1200000] [--limit N]
"""
from __future__ import annotations
import argparse, csv, json, re, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF not installed. Run: pip install pymupdf")

CAP_DEFAULT = 1_200_000   # ~300k tokens; safety ceiling for monster reviews
MIN_CHARS = 200

# Canonical sections, ordered by KEEP priority (earliest = most protected from pruning).
KEEP_ORDER = [
    "abstract", "methods", "results", "tables", "cost", "conclusion",
    "discussion", "background", "other",
    # low-value — dropped first when over cap:
    "acknowledgements", "funding", "coi", "appendix", "references",
]
LOW_VALUE = {"acknowledgements", "funding", "coi", "appendix", "references"}

# Header line -> canonical section. Matched on short standalone lines only.
HEADER_MAP = [
    (r"abstract|summary", "abstract"),
    (r"background|introduction", "background"),
    (r"materials? and methods|methodology|methods?|search strategy|data extraction|study selection|eligibility", "methods"),
    (r"results|findings", "results"),
    (r"discussion", "discussion"),
    (r"conclusions?|implications", "conclusion"),
    (r"cost|economic evaluation|cost-effectiveness", "cost"),
    (r"references|bibliography", "references"),
    (r"appendix|appendices|supplementary|supplemental", "appendix"),
    (r"acknowledge?ments?", "acknowledgements"),
    (r"funding|financial support", "funding"),
    (r"conflicts? of interest|competing interests|declaration of interest", "coi"),
]
HEADER_RE = re.compile(
    r"^\s*(?:\d+\.?\d*\.?\s+)?(" + "|".join(p for p, _ in HEADER_MAP) + r")\s*:?\s*$",
    re.IGNORECASE,
)
TABLE_RE = re.compile(r"^\s*(table|fig(?:ure)?)\s+\d+", re.IGNORECASE)

REVIEW_HINTS = re.compile(
    r"systematic review|meta-analysis|meta analysis|umbrella review|scoping review|"
    r"cochrane|network meta|overview of reviews|evidence synthesis|rapid review",
    re.IGNORECASE,
)


def canon_for_header(line: str) -> str | None:
    m = HEADER_RE.match(line)
    if not m:
        return None
    h = m.group(1).lower()
    for pat, canon in HEADER_MAP:
        if re.fullmatch(pat, h, re.IGNORECASE) or re.search(pat, h, re.IGNORECASE):
            return canon
    return None


def segment(text: str) -> dict[str, str]:
    """Heuristic split of raw PDF text into canonical labelled sections."""
    sections: dict[str, list[str]] = {}
    current = "abstract"   # everything before the first real header is front-matter/abstract
    for raw in text.split("\n"):
        line = raw.strip()
        if line and len(line) < 60:
            canon = canon_for_header(line)
            if canon:
                current = canon
                continue
            if TABLE_RE.match(line):
                current = "tables"
        sections.setdefault(current, []).append(raw)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "".join(v).strip()}


def prune_to_cap(sections: dict[str, str], cap: int) -> tuple[dict[str, str], list[str]]:
    """Keep sections within `cap` chars, dropping low-value sections first, then
    truncating the least-important remaining sections. Returns (kept, notes)."""
    total = sum(len(v) for v in sections.values())
    notes: list[str] = []
    if total <= cap:
        return sections, notes
    kept = dict(sections)
    # 1. Drop low-value sections whole, in reverse keep-priority.
    for sec in reversed(KEEP_ORDER):
        if total <= cap:
            break
        if sec in LOW_VALUE and sec in kept:
            total -= len(kept[sec]); notes.append(f"dropped:{sec}"); del kept[sec]
    # 2. Still over: truncate lowest-priority remaining sections (keep the head).
    for sec in reversed([s for s in KEEP_ORDER if s not in LOW_VALUE]):
        if total <= cap:
            break
        if sec in kept:
            over = total - cap
            new_len = max(0, len(kept[sec]) - over)
            if new_len < len(kept[sec]):
                total -= (len(kept[sec]) - new_len)
                kept[sec] = kept[sec][:new_len] + "\n[...truncated]"
                notes.append(f"truncated:{sec}")
    return kept, notes


def render(sections: dict[str, str]) -> str:
    """Render kept sections into a single labelled string in reading order."""
    order = [s for s in KEEP_ORDER if s in sections]
    return "\n\n".join(f"## {s.upper()}\n{sections[s]}" for s in order)


def build_citation(authors: str, year, title: str) -> str:
    a = (authors or "").strip()
    if a:
        first = a.split(";")[0].split(",")[0].strip()
        lead = f"{first} et al." if (";" in a or " and " in a.lower()) else a
    else:
        lead = "Anon."
    return f"{lead} ({year}). {title}".strip()


def main():
    p = argparse.ArgumentParser(description="Build ULCM extraction input from FTS includes.")
    p.add_argument("--results", required=True, help="FTS tiebreak results JSONL")
    p.add_argument("--fts-records", required=True, help="records_fts_*.jsonl (for pdf_file/title/doi)")
    p.add_argument("--inventory", required=True, help="FTR inventory_merged.csv (authors/item_type)")
    p.add_argument("--pdfs-dir", required=True)
    p.add_argument("--out-dir", default="projects/strongminds/data/extraction")
    p.add_argument("--cap", type=int, default=CAP_DEFAULT)
    p.add_argument("--decision", default="INCLUDE")
    p.add_argument("--limit", type=int, default=0, help="cap #records (testing)")
    args = p.parse_args()

    pdfs_dir = Path(args.pdfs_dir)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    # Include set + route hint from screening.
    includes: dict[str, list[str]] = {}
    for l in open(args.results, encoding="utf-8"):
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("screening_decision") == args.decision:
            routes = (r.get("_router") or {}).get("routes") or []
            includes[str(r["record_id"])] = routes
    print(f"Includes ({args.decision}): {len(includes)}")

    fts = {str(json.loads(l)["record_id"]): json.loads(l)
           for l in open(args.fts_records, encoding="utf-8") if l.strip()}
    inv = {r["zotero_key"]: r
           for r in csv.DictReader(open(args.inventory, encoding="utf-8-sig"))}

    ids = list(includes)
    if args.limit:
        ids = ids[: args.limit]

    records, missing, low_text, pruned = [], [], [], []
    for n, rid in enumerate(ids, 1):
        frec = fts.get(rid, {})
        ivrec = inv.get(rid, {})
        pf = frec.get("pdf_file", "")
        pdf_path = pdfs_dir / Path(pf).name if pf else None
        if not pdf_path or not pdf_path.exists():
            cand = list(pdfs_dir.glob(f"{rid}*"))
            pdf_path = cand[0] if cand else None
        if not pdf_path or not pdf_path.exists():
            missing.append({"record_id": rid, "pdf_file": pf}); continue

        try:
            with fitz.open(str(pdf_path)) as doc:
                n_pages = doc.page_count
                text = "\n".join(pg.get_text("text") or "" for pg in doc)
        except Exception as e:
            missing.append({"record_id": rid, "pdf_file": pf, "error": str(e)}); continue

        n_full = len(text)
        sections = segment(text)
        kept, prune_notes = prune_to_cap(sections, args.cap)
        seg_text = render(kept)
        n_kept = len(seg_text)

        title = frec.get("title") or ivrec.get("title") or "NA"
        year = frec.get("year") or ivrec.get("year") or "NA"
        authors = ivrec.get("authors", "")
        item_type = ivrec.get("item_type", "")
        doi = frec.get("doi") or ivrec.get("doi") or ""
        is_review = bool(REVIEW_HINTS.search(title) or REVIEW_HINTS.search(text[:20000]))

        if n_kept < MIN_CHARS:
            low_text.append({"record_id": rid, "n_chars": n_kept, "n_pages": n_pages})
        if prune_notes:
            pruned.append({"record_id": rid, "n_full": n_full, "n_kept": n_kept, "notes": prune_notes})

        records.append({
            "record_id": rid,
            "eppi_id": None,                      # filled later if an EPPI map is provided
            "cit": build_citation(authors, year, title),
            "title": title, "authors": authors, "year": year, "doi": doi,
            "doctype": item_type,
            "unit_of_extraction": "review" if is_review else "primary_study",
            "rq_tags_hint": includes.get(rid, []),
            "segmented_full_text": seg_text,
            "sections_present": sorted(kept.keys()),
            "section_char_map": {k: len(v) for k, v in kept.items()},
            "n_pages": n_pages, "n_chars_full": n_full, "n_chars_kept": n_kept,
            "pruned": bool(prune_notes), "prune_notes": prune_notes,
        })
        if n % 100 == 0:
            print(f"  [{n}/{len(ids)}] processed...")

    rec_path = out_dir / f"records_extract_{len(records)}.jsonl"
    with open(rec_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for name, data in [("missing_pdf", missing), ("low_text", low_text), ("pruned", pruned)]:
        if data:
            with open(out_dir / f"extract_{name}.jsonl", "w", encoding="utf-8") as f:
                for d in data:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")

    n_review = sum(1 for r in records if r["unit_of_extraction"] == "review")
    print(f"\n=== Extraction-ingest summary ===")
    print(f"Included records:        {len(includes)}")
    print(f"Extracted OK:            {len(records)}  (reviews {n_review} / primary {len(records)-n_review})")
    print(f"Missing/broken PDFs:     {len(missing)}")
    print(f"Low text (<{MIN_CHARS}):        {len(low_text)}")
    print(f"Pruned (over {args.cap:,} cap): {len(pruned)}")
    print(f"\nWritten to: {rec_path}")


if __name__ == "__main__":
    main()
