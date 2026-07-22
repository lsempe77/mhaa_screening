"""
run_critic.py — Re-run the §2 critic/adjudicator on flagged records in an existing
results JSONL, in parallel. No primary re-screening — only critic calls.

Usage:
    python pipeline/run_critic.py \
        --prompt projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.md \
        --records projects/girl_effect/ta_screening/data/records_462.jsonl \
        --in projects/girl_effect/ta_screening/output/results_merged_462.jsonl \
        --out projects/girl_effect/ta_screening/output/results_critic_462.jsonl \
        --critic-model mistralai/mistral-large \
        --temperature 0.5 \
        --workers 15
"""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from k5_runner import load_critic_message, screen_critic


def adjudicate_one(rec: dict, agg: dict, critic_system: str, model: str, temperature: float) -> dict:
    """Run critic on one flagged record; return updated agg dict."""
    primary_for_critic = {
        "screening_code": agg["screening_code"],
        "screening_decision": agg["screening_decision"],
        "explanation": (agg["runs"][0].get("explanation", "") if agg.get("runs") else ""),
        "supporting_quote": (agg["runs"][0].get("supporting_quote", "NA") if agg.get("runs") else "NA"),
    }
    critic_result = screen_critic(critic_system, rec, primary_for_critic, model, temperature)

    critic_block = {
        "applied": True,
        "adjudication": critic_result.get("adjudication"),
        "model": model,
    }
    if critic_result.get("adjudication") == "override":
        critic_block["overridden_code"] = critic_result.get("overridden_code", "NA")
        agg["screening_code"] = critic_result.get("screening_code", agg["screening_code"])
        agg["screening_decision"] = critic_result.get("screening_decision", agg["screening_decision"])
    agg["critic"] = critic_block
    # Append critic run to the runs array
    agg.setdefault("runs", []).append({**critic_result, "_role": "critic"})
    agg["needs_second_opinion"] = bool(critic_result.get("needs_second_opinion"))
    return agg


def main():
    p = argparse.ArgumentParser(description="Run §2 critic on flagged records in an existing results JSONL.")
    p.add_argument("--prompt", required=True, help="Path to the prompt .md file")
    p.add_argument("--records", required=True, help="Original records JSONL")
    p.add_argument("--in", dest="inp", required=True, help="Input results JSONL")
    p.add_argument("--out", required=True, help="Output results JSONL (with critic applied)")
    p.add_argument("--critic-model", default="mistralai/mistral-large")
    p.add_argument("--temperature", type=float, default=0.5)
    p.add_argument("--workers", type=int, default=15)
    args = p.parse_args()

    critic_system = load_critic_message(args.prompt)

    # Load records
    records = {}
    for line in open(args.records, "r", encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            records[str(r["record_id"])] = r

    # Load existing results
    results = []
    for line in open(args.inp, "r", encoding="utf-8"):
        line = line.strip()
        if line:
            results.append(json.loads(line))

    flagged = [r for r in results if r.get("needs_second_opinion")]
    unflagged = [r for r in results if not r.get("needs_second_opinion")]
    print(f"Total records: {len(results)}")
    print(f"Flagged for critic: {len(flagged)}  (unflagged: {len(unflagged)})")
    print(f"Running critic with {args.workers} parallel workers...\n")

    # Run critic in parallel on flagged records
    done_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        future_to_rid = {}
        for agg in flagged:
            rid = str(agg["record_id"])
            rec = records.get(rid, {})
            future_to_rid[ex.submit(adjudicate_one, rec, agg, critic_system, args.critic_model, args.temperature)] = rid

        completed = 0
        for fut in as_completed(future_to_rid):
            rid = future_to_rid[fut]
            try:
                done_map[rid] = fut.result()
            except Exception as e:
                # Keep original record, mark critic as failed
                orig = next(r for r in flagged if str(r["record_id"]) == rid)
                orig["critic"] = {"applied": True, "adjudication": None, "model": args.critic_model, "error": str(e)}
                done_map[rid] = orig
            completed += 1
            r = done_map[rid]
            adj = r.get("critic", {}).get("adjudication", "?")
            print(f"[{rid}] code={r['screening_code']} critic={adj} ({completed}/{len(flagged)})")

    # Merge: unflagged pass through, flagged get critic-adjudicated version
    out_records = []
    for r in results:
        rid = str(r["record_id"])
        if rid in done_map:
            out_records.append(done_map[rid])
        else:
            out_records.append(r)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in out_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    overrides = sum(1 for r in out_records if r.get("critic", {}).get("adjudication") == "override")
    confirms = sum(1 for r in out_records if r.get("critic", {}).get("adjudication") == "confirm")
    print(f"\nWrote {len(out_records)} records to {out_path}")
    print(f"Critic: {confirms} confirm, {overrides} override")


if __name__ == "__main__":
    main()
