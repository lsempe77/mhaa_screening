"""Study the errors of the best config (exemplars + temp0, k=1) on the test split,
to find a targeted 'small twist'. Reconstructs retrieved exemplars deterministically."""
import json, sys
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]        # repo root
sys.path.insert(0, str(HERE))                  # sibling module: fewshot_screen
sys.path.insert(0, str(ROOT / "pipeline"))     # shared engine: k5_runner
import fewshot_screen as F

def lines(p): return [l for l in (ROOT / p).read_text(encoding="utf-8").split("\n") if l.strip()]

recs = {}
for l in lines("projects/strongminds/data/records_510.jsonl"):
    r = json.loads(l); recs[str(r["record_id"])] = r
gt_raw = json.loads((ROOT / "projects/strongminds/data/gt_510.json").read_text(encoding="utf-8"))
gt_bin = {k: ("INCLUDE" if "INCLUDE" in v.upper() else "EXCLUDE") for k, v in gt_raw.items()}
adj = {a["record_id"]: a for a in (json.loads(l) for l in lines("projects/strongminds/artifacts/gt_adjudication.jsonl"))}
res = json.loads((ROOT / "projects/strongminds/artifacts/fewshot_results_k1.json").read_text(encoding="utf-8"))
fs, vote, test = res["fs_decision"], res["fs_vote"], set(res["test_ids"])
flipped = set(res["flipped"])
gt_clean = dict(gt_bin)
for rid in flipped:
    gt_clean[rid] = adj[rid]["pipeline_decision"]

# rebuild retrieval index exactly as the run did
train = set(recs) - test
bm25, ids, toks = F.build_index(train, recs)

def yr(rid):
    try: return int(str(recs[rid].get("year"))[:4])
    except: return None

DESIGN_WORDS = ["narrative review", "letter", "protocol", "commentary", "editorial",
                "cross-sectional", "case report", "case-control", "cohort study", "primary study"]
def design_flag(rid):
    t = (recs[rid].get("title","") + " " + recs[rid].get("abstract","")).lower()
    return [w for w in DESIGN_WORDS if w in t]

def analyze(gt, tag):
    fp = [r for r in test if fs.get(r) == "INCLUDE" and gt.get(r) == "EXCLUDE"]
    fn = [r for r in test if fs.get(r) == "EXCLUDE" and gt.get(r) == "INCLUDE"]
    print(f"\n{'='*72}\n{tag}: {len(fp)} FP, {len(fn)} FN\n{'='*72}")
    print("\nFP by ORIGINAL GT reason:")
    for k, v in Counter(gt_raw.get(r, "?") for r in fp).most_common():
        print(f"  {v:2d}  {k}")
    print("FN by contested-verdict / structure:")
    print(f"  pre-2000 among FN: {sum(yr(r) and yr(r) < 2000 for r in fn)}")
    print(f"  design-word among FN: {sum(bool(design_flag(r)) for r in fn)}")
    return fp, fn

fp_o, fn_o = analyze(gt_bin, "vs ORIGINAL GT")
fp_c, fn_c = analyze(gt_clean, "vs CLEANED GT")

def dump(rids, gt, header):
    print(f"\n----- {header} (n={len(rids)}) -----")
    for r in sorted(rids, key=lambda x: vote.get(x) if vote.get(x) is not None else 0):
        v = adj.get(r, {}).get("verdict", "not_contested")
        print(f"\n[vote_inc={vote.get(r)}] rid={r} yr={yr(r)} GT={gt_raw.get(r)} step1={v}")
        print(f"  {recs[r].get('title','')[:150]}")
        dw = design_flag(r)
        if dw: print(f"  DESIGN WORDS: {dw}")

print("\n" + "#"*72 + "\n# CLEANED-GT ERRORS (the ones that matter for the real target)\n" + "#"*72)
dump(fp_c, gt_clean, "FALSE POSITIVES (fs INCLUDE, cleaned-GT EXCLUDE)")
dump(fn_c, gt_clean, "FALSE NEGATIVES (fs EXCLUDE, cleaned-GT INCLUDE)")

# vote-share distribution of errors vs correct, to check threshold tuning headroom
print("\n" + "#"*72 + "\n# VOTE-SHARE of errors (threshold-tuning headroom?)\n" + "#"*72)
def band(rids):
    return Counter(("<=0.3" if (vote.get(r) or 0)<=0.3 else "0.3-0.5" if (vote.get(r) or 0)<0.5
                    else "0.5-0.7" if (vote.get(r) or 0)<0.7 else ">=0.7") for r in rids)
print("FP cleaned vote bands:", dict(band(fp_c)))
print("FN cleaned vote bands:", dict(band(fn_c)))
