"""tiebreak_ris.py — Independent third-model tie-breaker for RIS screening results.

Reads a results JSONL from the orchestrator, finds all model-disagreement records
(vote_share_include == 0.5, i.e. Claude said IN, GLM said EXCLUDE or vice versa),
and calls a third model (Gemini 2.5 Pro) independently — blind to the other models'
verdicts. Majority of 3 wins. Remaining 3-way splits or uncertainties are flagged
for human review.

Usage:
    python projects/strongminds/scripts/tiebreak_ris.py
        --results projects/strongminds/data/output/results_ris_v19.jsonl
        --records projects/strongminds/data/ris_records.jsonl
        --prompt  projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9.md
        --model   google/gemini-2.5-pro
        --out     projects/strongminds/data/output/results_ris_v19_tiebreak.jsonl
        --workers 8
"""
from __future__ import annotations
import argparse, json, sys, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import pipeline.k5_runner as k


def load_records(path):
    recs = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            recs[str(r["record_id"])] = r
    return recs


def load_results(path):
    results = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            results[str(r["record_id"])] = r
    return results


def find_disagreements(results):
    """Find records where vote_share_include == 0.5 (1 model IN, 1 model EXCLUDE)."""
    disputes = []
    for rid, rec in results.items():
        v = rec.get("vote_share_include", 0)
        if 0.4 < v < 0.6:
            disputes.append(rid)
    return disputes


def screen_one(rid, rec, prompt_sys, model, temperature=0.0):
    """Call the orchestrator's screener on one record using the third model.

    We reuse the orchestrator's own message-building so the third model sees
    the exact same prompt as Claude and GLM did.
    """
    # Load the orchestrator prompt sections
    from pipeline.orchestrator import _extract_section, _extract_system_message, build_user_message, ROUTE_CODES

    user = build_user_message(rec)
    try:
        raw = k.dispatch(model, prompt_sys, user, temperature, max_tokens=4000)
        obj = k.extract_json(raw) or {}
        obj = k.normalize_response(obj)
        code = obj.get("screening_code", "INCLUDE_TA")
        decision = obj.get("screening_decision", "INCLUDE")
        return {
            "record_id": rid,
            "screening_code": code,
            "screening_decision": decision,
            "explanation": obj.get("explanation", "")[:500],
            "supporting_quote": obj.get("supporting_quote", "NA"),
            "needs_second_opinion": obj.get("needs_second_opinion", False),
            "confidence": obj.get("confidence", "Medium"),
            "_model": model,
            "_temperature": temperature,
            "_role": "tiebreaker",
        }
    except Exception as e:
        return {
            "record_id": rid,
            "screening_code": "INCLUDE_TA",
            "screening_decision": "INCLUDE",
            "explanation": f"TIEBREAKER_ERROR: {e}",
            "supporting_quote": "NA",
            "needs_second_opinion": True,
            "confidence": "Low",
            "_model": model,
            "_temperature": temperature,
            "_role": "tiebreaker",
            "_flags": ["api_error"],
        }


def main():
    p = argparse.ArgumentParser(description="Third-model tie-breaker for RIS results.")
    p.add_argument("--results", required=True, help="Results JSONL from orchestrator run")
    p.add_argument("--records", required=True, help="Records JSONL")
    p.add_argument("--prompt", required=True, help="Orchestrator prompt file")
    p.add_argument("--model", default="google/gemini-2.5-pro", help="Third model slug")
    p.add_argument("--out", required=True, help="Output JSONL (updated results)")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--resume", action="store_true", help="Skip records already tiebroken in --out")
    args = p.parse_args()

    print("Loading records...", file=sys.stderr)
    recs = load_records(args.records)
    print(f"  {len(recs)} records", file=sys.stderr)

    print("Loading results...", file=sys.stderr)
    results = load_results(args.results)
    print(f"  {len(results)} results", file=sys.stderr)

    disputes = find_disagreements(results)
    print(f"  {len(disputes)} disagreements (vote ~0.5) need tie-breaking", file=sys.stderr)

    # Load the screener system prompt (intervention screener — the main one)
    prompt_text = Path(args.prompt).read_text(encoding="utf-8")
    # Try to extract the intervention screener section (§3 in the prompt file)
    screener_sys = None
    try:
        from pipeline.orchestrator import _extract_section, _extract_system_message
        section = _extract_section(prompt_text, "3.")
        screener_sys = _extract_system_message(section)
    except Exception:
        pass
    if not screener_sys:
        # Fallback: use the whole file as system prompt
        screener_sys = prompt_text[:8000]

    # Resume support
    done = set()
    if args.resume and Path(args.out).exists():
        for line in open(args.out, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                if r.get("_tiebreaker_applied"):
                    done.add(str(r["record_id"]))
        print(f"  Resume: {len(done)} already tiebroken, skipping.", file=sys.stderr)

    to_process = [rid for rid in disputes if rid not in done]
    print(f"  Processing {len(to_process)} records with {args.model}...", file=sys.stderr)

    # Copy existing results as the base
    out_results = dict(results)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {}
        for rid in to_process:
            rec = recs.get(rid)
            if not rec:
                continue
            fut = ex.submit(screen_one, rid, rec, screener_sys, args.model, args.temperature)
            futures[fut] = rid

        completed = 0
        for fut in as_completed(futures):
            rid = futures[fut]
            try:
                tb = fut.result()
            except Exception as e:
                tb = {"record_id": rid, "screening_code": "INCLUDE_TA",
                      "screening_decision": "INCLUDE", "explanation": f"ERROR: {e}",
                      "_model": args.model, "_role": "tiebreaker", "_flags": ["error"]}

            # Apply majority-of-3 logic
            orig = results[rid]
            # Get the two primary models' decisions
            pm = orig.get("per_model", [])
            votes = []
            for m in pm:
                votes.append(1 if m.get("screening_decision") == "INCLUDE" else 0)
            # Add tiebreaker vote
            tb_vote = 1 if tb["screening_decision"] == "INCLUDE" else 0
            votes.append(tb_vote)

            include_count = sum(votes)
            final_decision = "INCLUDE" if include_count >= 2 else "EXCLUDE"
            final_code = "INCLUDE_TA" if final_decision == "INCLUDE" else tb.get("screening_code", orig.get("screening_code"))

            # Update the result
            out_results[rid] = dict(orig)
            out_results[rid]["screening_decision"] = final_decision
            out_results[rid]["screening_code"] = final_code
            out_results[rid]["vote_share_include"] = include_count / 3.0
            out_results[rid]["_tiebreaker_applied"] = True
            out_results[rid]["_tiebreaker_model"] = args.model
            out_results[rid]["_tiebreaker_vote"] = tb_vote
            out_results[rid]["_votes"] = votes
            out_results[rid]["needs_second_opinion"] = (include_count == 1 or tb.get("needs_second_opinion", False))

            # Append tiebreaker run to runs list
            if "runs" not in out_results[rid]:
                out_results[rid]["runs"] = []
            out_results[rid]["runs"].append(tb)

            completed += 1
            remaining = len(to_process) - completed
            status = "3-WAY SPLIT" if include_count in (1, 2) and tb.get("needs_second_opinion") else "RESOLVED"
            print(f"[{rid}] votes={votes} -> {final_decision} ({status}) ({remaining} left)", file=sys.stderr)

    # Write all results (tiebroken + untouched)
    with open(args.out, "w", encoding="utf-8") as f:
        for rid, rec in out_results.items():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Summary
    resolved = sum(1 for r in out_results.values() if r.get("_tiebreaker_applied") and not r.get("needs_second_opinion"))
    still_split = sum(1 for r in out_results.values() if r.get("_tiebreaker_applied") and r.get("needs_second_opinion"))
    print(f"\nDone. Tiebroken: {len(to_process)} | Resolved: {resolved} | Flagged for human: {still_split}", file=sys.stderr)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
