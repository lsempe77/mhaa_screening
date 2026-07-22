"""
merge_results.py — Merge stored Claude runs (from results_k5_462.jsonl) with new
GLM runs, re-aggregate per-model + re-combine under v1.4.1 logic.

The old results file has Claude + GPT-4o-mini runs per record. We:
  1. Extract Claude's 5 runs per record from the old file.
  2. Extract GLM's 5 runs per record from the new GLM-only file.
  3. Re-run aggregate_one_model + combine_models (no new API calls).
  4. Write merged results to a new JSONL.

Critic runs from the old file are dropped — the merge re-derives needs_second_opinion
from the combined Claude+GLM agreement, so critic adjudication should be re-run
separately if needed.

Usage:
    python pipeline/merge_results.py \
        --old projects/girl_effect/ta_screening/output/results_k5_462.jsonl \
        --new projects/girl_effect/ta_screening/output/results_glm_462.jsonl \
        --records projects/girl_effect/ta_screening/data/records_462.jsonl \
        --out projects/girl_effect/ta_screening/output/results_merged_462.jsonl \
        --uncertainty-band 0.4 0.6 \
        --claude-model anthropic/claude-sonnet-4 \
        --glm-model z-ai/glm-5.2
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from k5_runner import aggregate_one_model, combine_models


def extract_model_runs(results_path: str, model_slug: str) -> dict[str, list[dict]]:
    """From a results JSONL, extract the runs tagged with _model == model_slug for each record."""
    out: dict[str, list[dict]] = {}
    for line in open(results_path, "r", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        rid = rec.get("record_id")
        runs = [r for r in rec.get("runs", []) if r.get("_model") == model_slug]
        if runs:
            out[str(rid)] = runs
    return out


def main():
    p = argparse.ArgumentParser(description="Merge Claude + GLM runs, re-aggregate under v1.4.1.")
    p.add_argument("--old", required=True, help="Old results JSONL (Claude + GPT runs)")
    p.add_argument("--new", required=True, help="New results JSONL (GLM-only runs)")
    p.add_argument("--records", required=True, help="Original records JSONL")
    p.add_argument("--out", required=True, help="Output merged JSONL")
    p.add_argument("--uncertainty-band", nargs=2, type=float, default=[0.4, 0.6])
    p.add_argument("--claude-model", default="anthropic/claude-sonnet-4")
    p.add_argument("--glm-model", default="z-ai/glm-5.2")
    args = p.parse_args()

    band = tuple(args.uncertainty_band)

    # Load records
    records = {}
    for line in open(args.records, "r", encoding="utf-8"):
        line = line.strip()
        if line:
            rec = json.loads(line)
            records[str(rec["record_id"])] = rec

    # Extract per-model runs
    claude_runs = extract_model_runs(args.old, args.claude_model)
    glm_runs = extract_model_runs(args.new, args.glm_model)

    print(f"Claude runs found: {len(claude_runs)} records")
    print(f"GLM runs found: {len(glm_runs)} records")

    common = sorted(set(claude_runs) & set(glm_runs) & set(records))
    print(f"Records with both models: {len(common)}")

    missing_claude = sorted(set(records) - set(claude_runs))
    missing_glm = sorted(set(records) - set(glm_runs))
    if missing_claude:
        print(f"WARNING: {len(missing_claude)} records missing Claude runs: {missing_claude[:5]}...")
    if missing_glm:
        print(f"WARNING: {len(missing_glm)} records missing GLM runs: {missing_glm[:5]}...")

    # Re-aggregate
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        for rid in sorted(records):
            rec = records[rid]
            c_runs = claude_runs.get(rid, [])
            g_runs = glm_runs.get(rid, [])

            per_model = []
            if c_runs:
                per_model.append(aggregate_one_model(c_runs, rec, band, args.claude_model))
            if g_runs:
                per_model.append(aggregate_one_model(g_runs, rec, band, args.glm_model))

            if not per_model:
                print(f"  SKIP {rid}: no runs from either model")
                continue

            agg = combine_models(per_model, rec, band)
            # No critic in merge — critic would need new API calls; mark for re-run
            agg["critic"] = {"applied": False, "adjudication": None, "model": None}
            # If flagged after merge, note that critic hasn't run
            if agg["needs_second_opinion"]:
                agg["critic"]["note"] = "not_re_run; merge-derived flag"

            f.write(json.dumps(agg, ensure_ascii=False) + "\n")
            written += 1

    print(f"\nWrote {written} merged records to {out_path}")

    # Quick stats
    agree = disagree = flagged = 0
    for line in out_path.open("r", encoding="utf-8"):
        r = json.loads(line)
        if r.get("model_agreement") == "agree":
            agree += 1
        else:
            disagree += 1
        if r.get("needs_second_opinion"):
            flagged += 1
    print(f"Agreement: {agree} agree, {disagree} disagree")
    print(f"Flagged for second opinion: {flagged}")


if __name__ == "__main__":
    main()
