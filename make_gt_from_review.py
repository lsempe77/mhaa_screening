"""Convert a human-annotated triage CSV into the ground-truth JSON the pipeline expects.

The reviewer adds a `human_decision` column to the triage CSV (or summary CSV)
with values: INCLUDE, EXCLUDE, or leave blank for "no review / defer to model".

This script reads that CSV and writes gt_<n>.json in the format k5_runner's
--calibrate flag consumes: { record_id: "INCLUDE on title & abstract" | "EXCLUDE on topic/interest" }

Usage:
    python make_gt_from_review.py \\
        --csv GE_FTS/reports/summary_annotated.csv \\
        --out GE_FTS/data/gt_406.json
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    p = argparse.ArgumentParser(description="Convert annotated CSV -> ground-truth JSON for calibration.")
    p.add_argument("--csv", required=True, help="Annotated CSV (must have record_id + human_decision columns).")
    p.add_argument("--out", required=True, help="Output gt_<n>.json path.")
    args = p.parse_args()

    gt: dict[str, str] = {}
    reviewed = 0
    included = 0
    excluded = 0
    with open(args.csv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "record_id" not in reader.fieldnames:
            sys.exit(f"CSV must have a 'record_id' column. Found: {reader.fieldnames}")
        # Accept a few common column name variants
        decision_col = None
        for candidate in ("human_decision", "human_review", "review_decision", "decision", "final_decision"):
            if candidate in reader.fieldnames:
                decision_col = candidate
                break
        if not decision_col:
            sys.exit(
                f"CSV must have a human-decision column. Expected one of: "
                f"human_decision, human_review, review_decision, decision, final_decision. "
                f"Found columns: {reader.fieldnames}"
            )

        for row in reader:
            rid = row["record_id"].strip()
            if not rid:
                continue
            decision = (row[decision_col] or "").strip().upper()
            if not decision:
                continue  # blank = not reviewed, skip
            reviewed += 1
            if decision in ("INCLUDE", "INCLUDE_TA", "Y", "YES", "1"):
                gt[rid] = "INCLUDE on title & abstract"
                included += 1
            elif decision in ("EXCLUDE", "N", "NO", "0"):
                gt[rid] = "EXCLUDE on topic/interest"
                excluded += 1
            else:
                print(f"  WARNING: {rid} has unrecognized decision '{row[decision_col]}' — skipped")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {len(gt)} reviewed records to {out}")
    print(f"  INCLUDE: {included}")
    print(f"  EXCLUDE: {excluded}")
    print(f"  Not reviewed (blank): skipped")
    print(f"\nNext step:")
    print(f"  python k5_runner.py --calibrate GE_FTS/output/results_fts_glm_388.jsonl --gt {out}")


if __name__ == "__main__":
    main()
