"""
rerun_flagged.py — Re-screen records that failed (parse_error / api_error) in a prior run.

Reuses k5_runner's screen_once() but with a higher max_tokens override, since most failures
are "API returned null content" (output truncated at 1500 tokens) or "could not parse JSON"
(model ran out of output tokens mid-JSON).

Usage:
    python pipeline/rerun_flagged.py \
        --results projects/girl_effect/full_text/output/results_fts_glm_388.jsonl \
        --records projects/girl_effect/full_text/data/records_388.jsonl \
        --prompt projects/girl_effect/prompts/prompts-screening-mhaa-fulltext-v1.md \
        --model z-ai/glm-5.2 \
        --max-tokens 4000 \
        --workers 5
"""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import k5_runner


def main():
    p = argparse.ArgumentParser(description="Re-screen flagged records with higher max_tokens.")
    p.add_argument("--results", required=True, help="Output JSONL (will be edited in-place).")
    p.add_argument("--records", required=True, help="Records JSONL.")
    p.add_argument("--prompt", required=True, help="Prompt .md file.")
    p.add_argument("--model", default="z-ai/glm-5.2")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--project", default="mhaa")
    args = p.parse_args()

    # Override max_tokens in the project config
    k5_runner.PROJECT_CONFIG[args.project]["max_tokens"] = args.max_tokens
    print(f"Overrode {args.project} max_tokens → {args.max_tokens}")

    # Load system message
    system = k5_runner.load_system_message(args.prompt)

    # Load all records into a map
    records_map: dict[str, dict] = {}
    with open(args.records, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            records_map[str(r["record_id"])] = r

    # Load existing results, split into good / flagged
    good_lines: list[str] = []
    flagged_ids: list[str] = []
    with open(args.results, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            flags: set[str] = set()
            if r.get("_flags"):
                flags.update(r["_flags"])
            if r.get("runs") and r["runs"][0].get("_flags"):
                flags.update(r["runs"][0]["_flags"])
            if flags & {"parse_error", "api_error", "record_error"}:
                flagged_ids.append(str(r["record_id"]))
            else:
                good_lines.append(line)

    print(f"Kept {len(good_lines)} good records; re-screening {len(flagged_ids)} flagged:")
    print("  " + ", ".join(flagged_ids))

    # Re-screen flagged records
    flagged_recs = [records_map[rid] for rid in flagged_ids if rid in records_map]
    missing = [rid for rid in flagged_ids if rid not in records_map]
    if missing:
        print(f"WARNING: {len(missing)} flagged IDs not found in records file: {missing}")

    new_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(k5_runner.screen_once, system, rec, args.model, args.temperature, args.project): rec
            for rec in flagged_recs
        }
        for fut in as_completed(futures):
            rec = futures[fut]
            try:
                run_result = fut.result()
            except Exception as e:
                run_result = {
                    "record_id": rec["record_id"],
                    "screening_code": "INCLUDE_TA",
                    "screening_decision": "INCLUDE",
                    "needs_second_opinion": True,
                    "explanation": f"API_ERROR: {e}",
                    "confidence": "Low",
                    "_flags": ["api_error"],
                    "_model": args.model,
                }
            # Wrap in the aggregated format k5_runner produces for k=1, single model
            agg = k5_runner.combine_models(
                [k5_runner.aggregate_one_model([run_result], rec, (0.4, 0.6), args.model)],
                rec, (0.4, 0.6),
            )
            agg["critic"] = {"applied": False, "adjudication": None, "model": None}
            new_results[str(rec["record_id"])] = agg
            flags_str = ";".join(run_result.get("_flags", []) or [])
            print(f"  [{rec['record_id']}] {agg['screening_code']} conf={run_result.get('confidence','?')} flags={flags_str}")

    # Write back: good lines + new results (preserving original order of flagged_ids)
    with open(args.results, "w", encoding="utf-8") as f:
        for line in good_lines:
            f.write(line + "\n")
        for rid in flagged_ids:
            if rid in new_results:
                f.write(json.dumps(new_results[rid], ensure_ascii=False) + "\n")

    # Summary
    still_bad = sum(
        1 for rid in flagged_ids
        if rid in new_results and (
            set(new_results[rid].get("_flags", []) or [])
            | set(new_results[rid].get("runs", [{}])[0].get("_flags", []) or [])
        ) & {"parse_error", "api_error", "record_error"}
    )
    print(f"\nRe-screened {len(flagged_ids)} records; {still_bad} still failing.")
    print(f"Wrote {len(good_lines) + len(new_results)} total records to {args.results}")


if __name__ == "__main__":
    main()
