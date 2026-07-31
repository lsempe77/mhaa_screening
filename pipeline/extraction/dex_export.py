"""dex_export.py — Flatten DEX extraction results into review-ready tables.

Reads the run_dex output JSONL and writes:
  dex_summary.csv        one row per record — headline fields + audit (eligibility, quote
                         fails, high-stakes-for-review, amstar2/rob) for a fast overview.
  dex_long.csv           one row per (record × scalar field) — value, span, section,
                         quote_ok, quote_match, agreement. The analysis-ready master.
  dex_outcomes_long.csv  additional_outcomes[] materialised (one row per depression outcome).
  dex_review_queue.csv   the human worklist: records that are Possibly-ineligible, or have
                         high-stakes fields needing review, or quote-validation failures.
  dex_review.xlsx        the above as sheets (if openpyxl is available).

Resilient to the resumable-runner's retried-error lines: dedups by record_id, keeping the
last non-error record.

Usage:
    python pipeline/extraction/dex_export.py \
        --results projects/strongminds/data/extraction/dex_full_2670.jsonl \
        --records projects/strongminds/data/extraction/records_extract_final_2670.jsonl \
        --out-dir projects/strongminds/data/extraction/reports
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_results(path: str) -> dict:
    """record_id -> latest non-error result (falls back to an error record if that's all)."""
    out, errs = {}, {}
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        rid = str(r["record_id"])
        if r.get("_error"):
            errs.setdefault(rid, r)
        else:
            out[rid] = r
    for rid, r in errs.items():
        out.setdefault(rid, r)
    return out


def cellval(fields: dict, key: str):
    c = fields.get(key)
    return c.get("value") if isinstance(c, dict) else None


def main():
    p = argparse.ArgumentParser(description="Flatten DEX results into review tables.")
    p.add_argument("--results", required=True)
    p.add_argument("--records", required=True)
    p.add_argument("--out-dir", default="projects/strongminds/data/extraction/reports")
    a = p.parse_args()

    results = load_results(a.results)
    recmeta = {str(json.loads(l)["record_id"]): json.loads(l)
               for l in open(a.records, encoding="utf-8") if l.strip()}
    out_dir = Path(a.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows, long_rows, outcome_rows, queue_rows = [], [], [], []

    for rid, r in sorted(results.items()):
        rm = recmeta.get(rid, {})
        f = r.get("fields", {})
        aud = r.get("audit", {})
        meta = r.get("_meta", {})
        err = r.get("_error", "")

        # --- summary (one row/record) ---
        hs_review = aud.get("high_stakes_for_review", []) or []
        summary_rows.append({
            "record_id": rid, "title": rm.get("title", ""), "year": rm.get("year", ""),
            "unit": r.get("unit_of_extraction", ""), "doctype": cellval(f, "doctype"),
            "design": cellval(f, "design"), "geo_focus": cellval(f, "geo_focus"),
            "country": cellval(f, "country"), "rq_tags": cellval(f, "rq_tags"),
            "comparator": cellval(f, "comparator"), "eff_metric": cellval(f, "eff_metric"),
            "eff_value": cellval(f, "eff_value"), "eff_ci_lo": cellval(f, "eff_ci_lo"),
            "eff_ci_hi": cellval(f, "eff_ci_hi"), "n_included_studies": cellval(f, "n_included_studies"),
            "amstar2_band": cellval(f, "amstar2_band"), "rob_fatal_flaw": cellval(f, "rob_fatal_flaw"),
            "eligibility_flag": cellval(f, "eligibility_flag"),
            "eligibility_concern": cellval(f, "eligibility_concern"),
            "n_quote_fail": aud.get("n_quote_fail", ""), "n_high_stakes_review": len(hs_review),
            "high_stakes_for_review": ";".join(hs_review), "error": err,
            "model": meta.get("model", ""), "prompt": meta.get("prompt", ""),
        })

        # --- long (one row/scalar field) ---
        for key, c in f.items():
            if not (isinstance(c, dict) and "value" in c):
                continue
            v = c.get("value")
            if v in (None, "", [], "NA"):
                continue
            long_rows.append({
                "record_id": rid, "field": key,
                "value": json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v,
                "span": c.get("span", ""), "section": c.get("section", ""),
                "quote_ok": c.get("_quote_ok"), "quote_match": c.get("_quote_match", ""),
                "agreement": c.get("_agreement", ""),
                "high_stakes": key in hs_review,
            })

        # --- outcomes long ---
        for el in f.get("additional_outcomes", []) or []:
            if not isinstance(el, dict):
                continue
            row = {"record_id": rid}
            for k2, c2 in el.items():
                row[k2] = c2.get("value") if isinstance(c2, dict) else c2
            outcome_rows.append(row)

        # --- review queue (prioritised: eligibility concern, high-stakes fields needing
        # review, or an extraction error — NOT every low-priority descriptive quote-fail) ---
        reasons = []
        if cellval(f, "eligibility_flag") == "Possibly-ineligible":
            reasons.append("eligibility")
        if hs_review:
            reasons.append(f"high_stakes({len(hs_review)})")
        if err:
            reasons.append("extraction_error")
        if reasons:
            queue_rows.append({
                "record_id": rid, "title": rm.get("title", "")[:120],
                "reasons": ";".join(reasons),
                "eligibility_concern": cellval(f, "eligibility_concern") or "",
                "high_stakes_for_review": ";".join(hs_review),
                "n_quote_fail": aud.get("n_quote_fail", ""),
            })

    # Merge cross-review overlap columns into the summary if the overlap analysis has run
    # (dex_overlap.py writes dex_overlap_reviews.csv, keyed by record_id).
    ov_path = out_dir / "dex_overlap_reviews.csv"
    if ov_path.exists():
        ov = {r["record_id"]: r for r in csv.DictReader(open(ov_path, encoding="utf-8-sig"))}
        for row in summary_rows:
            o = ov.get(row["record_id"], {})
            row["overlap_cluster_id"] = o.get("cluster_id", "")
            row["overlap_n_shared_studies"] = o.get("n_shared_studies", "")
            row["overlap_max_jaccard"] = o.get("max_jaccard", "")
            row["overlap_cluster_size"] = o.get("cluster_size", "")

    def write_csv(name, rows):
        path = out_dir / name
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list({k for row in rows for k in row}))
                w.writeheader(); w.writerows(rows)
        return path, len(rows)

    outs = [write_csv("dex_summary.csv", summary_rows),
            write_csv("dex_long.csv", long_rows),
            write_csv("dex_outcomes_long.csv", outcome_rows),
            write_csv("dex_review_queue.csv", queue_rows)]

    # optional Excel workbook
    try:
        import pandas as pd
        xlsx = out_dir / "dex_review.xlsx"
        with pd.ExcelWriter(xlsx) as xw:
            pd.DataFrame(summary_rows).to_excel(xw, "Summary", index=False)
            pd.DataFrame(queue_rows).to_excel(xw, "Review queue", index=False)
            pd.DataFrame(outcome_rows).to_excel(xw, "Outcomes", index=False)
            pd.DataFrame(long_rows).to_excel(xw, "Long", index=False)
        print(f"Excel workbook → {xlsx}")
    except Exception as e:
        print(f"(Excel skipped: {e})")

    print(f"\nRecords: {len(results)}")
    for path, n in outs:
        print(f"  {n:>6} rows → {path}")


if __name__ == "__main__":
    main()
