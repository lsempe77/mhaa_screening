"""Generate focused triage CSVs for human review.

Outputs:
    projects/girl_effect/full_text/reports/excludes_triage.csv  — the 30 EXCLUDE decisions (highest stakes)
    projects/girl_effect/full_text/reports/flags_triage.csv     — the 258 needs_second_opinion INCLUDEs

The excludes file includes the model's explanation and supporting quote so a human
reviewer can confirm or override each EXCLUDE. The flags file classifies the flag
reason (age-overlap, postpartum/do-not-exclude, governance carve-out, low confidence,
quote failure, etc.) by scanning the explanation text.
"""
from __future__ import annotations
import csv, json, sys, re
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_records_map(path: str) -> dict[str, dict]:
    m = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            m[str(r["record_id"])] = r
    return m


def classify_flag(explanation: str, quote: str) -> str:
    """Infer why the model flagged this record, from its explanation text."""
    e = (explanation or "").lower()
    q = (quote or "").lower()
    reasons = []
    if "age-overlap" in e or "age overlap" in e or "overlaps the 10-24" in e or "overlap" in e:
        reasons.append("age_overlap")
    if any(k in e for k in ["postpartum", "postnatal", "do-not-exclude", "do not exclude"]):
        reasons.append("do_not_exclude_population")
    if any(k in e for k in ["governance", "ethics", "safety", "regulation", "carve-out", "carveout"]):
        reasons.append("governance_safety_carveout")
    if any(k in e for k in ["sibling", "defer-to-merge", "defer to merge"]):
        reasons.append("possible_duplicate")
    if any(k in e for k in ["ambiguous", "unclear", "genuinely ambiguous"]):
        reasons.append("ambiguous_ai_component")
    if "feasibility" in e or "usability" in e or "development" in e:
        reasons.append("feasibility_dev_study")
    if "second opinion" in e or "needs_second_opinion" in e:
        reasons.append("model_defensive_flag")
    if not reasons:
        reasons.append("other")
    return ";".join(reasons)


def main():
    results_path = "projects/girl_effect/full_text/output/results_fts_glm_388.jsonl"
    records_path = "projects/girl_effect/full_text/data/records_388.jsonl"
    out_dir = Path("projects/girl_effect/full_text/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load records (for title/pages/chars where available)
    recs = load_records_map(records_path)

    all_rows: list[dict] = []
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rid = str(r["record_id"])
            rec = recs.get(rid, {})
            run = (r.get("runs") or [{}])[0]
            run_flags = run.get("_flags", []) or []
            all_rows.append({
                "record_id": rid,
                "year": rec.get("year", r.get("year", "")),
                "title": rec.get("title", r.get("title", "")),
                "screening_code": r.get("screening_code", ""),
                "screening_decision": r.get("screening_decision", ""),
                "confidence": run.get("confidence", ""),
                "needs_second_opinion": r.get("needs_second_opinion", ""),
                "flag_reason": classify_flag(run.get("explanation", ""), run.get("supporting_quote", "")),
                "explanation": run.get("explanation", ""),
                "supporting_quote": run.get("supporting_quote", ""),
                "_flags": ";".join(run_flags) if run_flags else "",
                "n_chars": rec.get("n_chars", ""),
            })

    # --- Excludes triage ---
    excludes = [r for r in all_rows if r["screening_decision"] == "EXCLUDE"]
    excludes_path = out_dir / "excludes_triage.csv"
    with excludes_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(excludes[0].keys()) if excludes else ["record_id"])
        w.writeheader()
        w.writerows(excludes)

    # --- Flags triage (needs_second_opinion=True) ---
    flags = [r for r in all_rows if r["needs_second_opinion"] and r["screening_decision"] == "INCLUDE"]
    flags_path = out_dir / "flags_triage.csv"
    with flags_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(flags[0].keys()) if flags else ["record_id"])
        w.writeheader()
        w.writerows(flags)

    print(f"Wrote {len(excludes)} EXCLUDE records -> {excludes_path}")
    print(f"Wrote {len(flags)} flagged INCLUDE records -> {flags_path}")

    # --- Flag reason breakdown ---
    from collections import Counter
    reason_counts: Counter = Counter()
    for r in flags:
        for reason in r["flag_reason"].split(";"):
            reason_counts[reason] += 1
    print(f"\nFlag reason breakdown (flagged INCLUDEs, n={len(flags)}):")
    for reason, cnt in reason_counts.most_common():
        print(f"  {cnt:>4}  {reason}")

    # --- Exclude code breakdown ---
    code_counts: Counter = Counter(r["screening_code"] for r in excludes)
    print(f"\nEXCLUDE code breakdown (n={len(excludes)}):")
    for code, cnt in code_counts.most_common():
        print(f"  {cnt:>4}  {code}")


if __name__ == "__main__":
    main()
