"""Re-validate quote_validation_failed flags in the results file using the improved
verify_quote(). Removes the flag from records that now pass, and clears
needs_second_opinion if it was only set due to quote failure.

Usage:
    python revalidate_quotes.py --results GE_FTS/output/results_fts_glm_388.jsonl \
                                 --records GE_FTS/data/records_388.jsonl
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import k5_runner


def main():
    p = argparse.ArgumentParser(description="Re-validate quote flags with improved verify_quote().")
    p.add_argument("--results", required=True)
    p.add_argument("--records", required=True)
    args = p.parse_args()

    recs = {}
    with open(args.records, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            recs[str(r["record_id"])] = r

    lines_out: list[str] = []
    fixed = 0
    still_failing = 0
    with open(args.results, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            runs = r.get("runs") or []
            if not runs:
                lines_out.append(json.dumps(r, ensure_ascii=False))
                continue
            run = runs[0]
            rec_flags = list(run.get("_flags", []) or [])
            if "quote_validation_failed" not in rec_flags:
                lines_out.append(json.dumps(r, ensure_ascii=False))
                continue

            rid = str(r["record_id"])
            rec = recs.get(rid, {})
            title = rec.get("title", "")
            abstract = rec.get("abstract", "")
            year = rec.get("year", "")
            quote = run.get("supporting_quote", "NA")

            now_passes = k5_runner.verify_quote(quote, title, abstract, year)
            if now_passes:
                rec_flags = [f for f in rec_flags if f != "quote_validation_failed"]
                run["_flags"] = rec_flags
                runs[0] = run
                r["runs"] = runs
                # Clear needs_second_opinion if it was only set due to quote failure
                # (the model's own flag is preserved in run.needs_second_opinion)
                model_flagged = bool(run.get("needs_second_opinion"))
                if not model_flagged:
                    r["needs_second_opinion"] = False
                fixed += 1
                print(f"[{rid}] FIXED - quote now passes")
            else:
                still_failing += 1
                print(f"[{rid}] STILL FAILING")

            lines_out.append(json.dumps(r, ensure_ascii=False))

    with open(args.results, "w", encoding="utf-8") as f:
        for line in lines_out:
            f.write(line + "\n")

    print(f"\nFixed: {fixed} | Still failing: {still_failing}")


if __name__ == "__main__":
    main()
