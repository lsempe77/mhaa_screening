"""
summarize_fts.py — Flatten the k5_runner output JSONL into a review-friendly CSV.

Reads GE_FTS/output/results_fts_glm_388.jsonl and writes:
    GE_FTS/reports/summary.csv   one row per record:
      record_id, pdf_file, year, title, n_pages, n_chars,
      screening_code, screening_decision, confidence,
      needs_second_opinion, vote_share_include, explanation, supporting_quote

Usage:
    python summarize_fts.py --results GE_FTS/output/results_fts_glm_388.jsonl \
                            --records GE_FTS/data/records_388.jsonl \
                            --out GE_FTS/reports/summary.csv
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_records_map(records_path: str) -> dict[str, dict]:
    m: dict[str, dict] = {}
    with open(records_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            m[str(r["record_id"])] = r
    return m


def main():
    p = argparse.ArgumentParser(description="Flatten k5_runner output into a review CSV.")
    p.add_argument("--results", required=True, help="k5_runner output JSONL")
    p.add_argument("--records", required=True, help="records JSONL (for title/pages/chars)")
    p.add_argument("--out", required=True, help="Output CSV path")
    args = p.parse_args()

    recs = load_records_map(args.records)

    rows: list[dict] = []
    with open(args.results, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rid = str(r["record_id"])
            rec = recs.get(rid, {})
            run = (r.get("runs") or [{}])[0]
            rows.append({
                "record_id": rid,
                "pdf_file": rec.get("pdf_file", ""),
                "year": rec.get("year", r.get("year", "")),
                "title": rec.get("title", r.get("title", "")),
                "n_pages": rec.get("n_pages", ""),
                "n_chars": rec.get("n_chars", ""),
                "screening_code": r.get("screening_code", ""),
                "screening_decision": r.get("screening_decision", ""),
                "confidence": run.get("confidence", ""),
                "needs_second_opinion": r.get("needs_second_opinion", ""),
                "vote_share_include": r.get("vote_share_include", ""),
                "explanation": run.get("explanation", ""),
                "supporting_quote": run.get("supporting_quote", ""),
                "_flags": ";".join(r.get("_flags", []) or run.get("_flags", []) or []),
                "_model": run.get("_model", ""),
            })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Console summary
    from collections import Counter
    by_code = Counter(r["screening_code"] for r in rows)
    by_decision = Counter(r["screening_decision"] for r in rows)
    n_second = sum(1 for r in rows if r["needs_second_opinion"])
    n_flagged = sum(1 for r in rows if r["_flags"])

    print(f"Wrote {len(rows)} rows → {out}")
    print(f"\nBy screening_code:")
    for code, cnt in by_code.most_common():
        print(f"  {cnt:>4}  {code}")
    print(f"\nBy screening_decision: {dict(by_decision)}")
    print(f"needs_second_opinion=True: {n_second}")
    print(f"records with _flags: {n_flagged}")
    if n_flagged:
        from collections import Counter as C
        flag_counts = C()
        for r in rows:
            for fl in (r["_flags"] or "").split(";"):
                if fl:
                    flag_counts[fl] += 1
        print(f"  flag breakdown: {dict(flag_counts)}")


if __name__ == "__main__":
    main()
