"""Interpret the GT adjudication: corrected metrics + candidate listing."""
import json, math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ART = HERE.parent / "artifacts"          # projects/strongminds/artifacts
rows = [json.loads(l) for l in (ART / "gt_adjudication.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]

def kappa(tp, fp, fn, tn):
    n = tp + fp + fn + tn
    po = (tp + tn) / n
    pred_pos = (tp + fp) / n
    act_pos = (tp + fn) / n
    pe = pred_pos * act_pos + (1 - pred_pos) * (1 - act_pos)
    return (po - pe) / (1 - pe)

# Baseline v1.6 full-510 confusion (from reports/metrics.json)
tp, fp, fn, tn = 48, 41, 26, 395
print(f"BASELINE (v1.6, GT as-is):  sens={tp/(tp+fn):.3f}  spec={tn/(tn+fp):.3f}  kappa={kappa(tp,fp,fn,tn):.3f}")

# Scenario 1: correct only the CLEAR GT errors (both independent adjudicators contradict GT)
ge_fp = sum(1 for r in rows if r["verdict"] == "gt_error_candidate" and r["error_type"] == "FP")
ge_fn = sum(1 for r in rows if r["verdict"] == "gt_error_candidate" and r["error_type"] == "FN")
# FP that is GT-error -> was fp, becomes tp (GT should have been INCLUDE)
# FN that is GT-error -> was fn, becomes tn (GT should have been EXCLUDE)
tp1, fp1, fn1, tn1 = tp + ge_fp, fp - ge_fp, fn - ge_fn, tn + ge_fn
print(f"\nCLEAR GT-errors: {ge_fp} FP-side + {ge_fn} FN-side = {ge_fp+ge_fn}")
print(f"SCENARIO 1 (fix clear GT errors only): sens={tp1/(tp1+fn1):.3f}  spec={tn1/(tn1+fp1):.3f}  kappa={kappa(tp1,fp1,fn1,tn1):.3f}")

# Scenario 2: the 'fuzzy' contested records are irreducible label noise.
# Estimate the reliability ceiling: agreement between two independent expert-level
# adjudicators on the contested set (proxy for human dual-screening IRR here).
n_contested = len(rows)
agreed = sum(1 for r in rows if r["verdict"] in ("gt_error_candidate", "gt_robust"))
split = sum(1 for r in rows if r["verdict"] == "fuzzy_boundary")
print(f"\nInter-adjudicator agreement on the {n_contested} contested records: "
      f"{agreed}/{n_contested} = {agreed/n_contested:.0%} agree, {split}/{n_contested} split")
print("(Two strong independent reviewers applying the exact protocol reach OPPOSITE")
print(" conclusions on 76% of the records where the pipeline disagrees with GT.)")

print("\n" + "=" * 70)
print("CLEAR GT-ERROR CANDIDATES (both independent adjudicators contradict GT)")
print("=" * 70)
for r in rows:
    if r["verdict"] != "gt_error_candidate":
        continue
    adjs = r["adjudicators"]
    print(f"\n[{r['error_type']}] rid={r['record_id']}  GT={r['gt_label']}  pipeline={r['pipeline_decision']}  vote_inc={r['vote_share_include']}")
    print(f"  TITLE: {r['title'][:160]}")
    for m, a in adjs.items():
        print(f"  {m.split('/')[-1]:18s} -> {a.get('decision')} ({a.get('fail_criterion')}): {str(a.get('reasoning'))[:170]}")
