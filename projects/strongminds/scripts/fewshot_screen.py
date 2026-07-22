"""Step 3: few-shot exemplar-retrieval screener.

Rationale (from Step 1 GT-noise analysis): the pipeline's errors split into
(a) systematic prompt<->GT misalignments at topical boundaries -- comorbid
depression, prevalence-vs-determinants, design/date calls -- which are
*consistent* and therefore learnable from labeled neighbours, and (b)
irreducible fuzziness (76% of contested records). This screener attacks (a) by
retrieving GT-labeled boundary exemplars (BM25 over title+abstract) from a
held-out TRAIN pool and injecting them as few-shot anchors. Metrics are computed
ONLY on the held-out TEST split (no exemplar leakage) and reported against both
the original GT and a cleaned GT (12 clear GT-errors from Step 1 fixed).

Baseline for a fair comparison = the v1.6 orchestrator's decisions on the SAME
test records (read from results_orch_v16_510.jsonl). So any delta is the
exemplar effect, not a different record set.

Stage A (default): k=1, temp 0, both panel models -> cheap proof-of-signal.
Stage B (--k5):     k=5, temp 0.3, both models -> full vote-share + calibration.
"""
import json, os, sys, random, argparse, concurrent.futures as cf
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]        # repo root
sys.path.insert(0, str(ROOT / "pipeline"))
load_dotenv(ROOT / ".env")
from k5_runner import call_openrouter, extract_json

MODELS = ["anthropic/claude-sonnet-4", "z-ai/glm-5.2"]
CRITIC_MODEL = "mistralai/mistral-large"
SEED = 20260720
TEST_FRAC = 0.40
N_EXEMPLARS = 4

# Compact protocol rubric (same operative rules as the ULCM TAS protocol).
RUBRIC = """You are screening a Title & Abstract for the StrongMinds Ultra-Low-Cost Model
(ULCM) rapid review of adult depression. Decide INCLUDE or EXCLUDE.

ROUTES (a record may fit several; NO intervention needed for RQ1 determinants or RQ18 measurement):
- Determinants (RQ1): drivers/risk factors (incl. prevalence w/ associated/risk factors) for adult depression, esp. LMICs.
- Intervention (RQ2-6,10,13-15): brief structured psychological/psychosocial intervention, group, non-specialist/lay/peer/task-shared delivery.
- Dose/SSI/stepped care (RQ7-9,12,14): intensity/timing/durability/sequencing; specialist & HIC evidence MAY be eligible here.
- Spillover (RQ11); Cost (RQ16); Safety/referral (RQ17); Measurement (RQ18): validity/reliability of depression measures in LMICs.

EXCLUSION CRITERIA (first CLEAR failure wins):
1. POPULATION: adults 18+ with depression/anxiety-depression/CMD/distress; perinatal women eligible. Mixed adult/adolescent RETAINED unless clearly adolescent-only. Comorbid physical illness does NOT by itself exclude; apply the intervention (3) and outcome (4) tests normally to such records.
2. STUDY DESIGN (level=review): require a systematic review / evidence synthesis. Narrative reviews, editorials, letters, protocols-without-results, primary studies FAIL.
3. INTERVENTION/TOPIC: standard routes need a brief structured psychological/psychosocial intervention, group, non-specialist. EXCLUDE pharmacotherapy-only, neurostimulation-only, purely diagnostic/screening (outside RQ18), fully digital/self-guided w/o human facilitator, unrelated topics. RQ1/RQ18 need no intervention.
4. OUTCOME: intervention routes need a depression-relevant outcome. RQ1 needs depression determinants; RQ18 needs measure validity/reliability.
5. GEOGRAPHY: LMIC scope; HIC/UMIC eligible for RQ7-9,12,14. If geography absent/mixed, RETAIN.
6. TIME/LANGUAGE: EXCLUDE if clearly non-English or published before 2000; if missing, RETAIN.

GOVERNING RULE: If NO criterion CLEARLY fails, decide INCLUDE. Ambiguous / under-reported records are INCLUDED.

Below are similar previously-decided records with their GOLD decisions. Use them to
calibrate the boundary, then judge the TARGET record. Return ONLY:
{"decision":"INCLUDE|EXCLUDE","fail_criterion":"population|study_design|intervention|outcome|geography|time_language|none","confidence":0.0-1.0,"reasoning":"1-2 sentences"}"""


def load():
    def lines(p):
        return [l for l in (ROOT / p).read_text(encoding="utf-8").split("\n") if l.strip()]
    recs = {}
    for l in lines("projects/strongminds/data/records_510.jsonl"):
        r = json.loads(l); recs[str(r["record_id"])] = r
    gt = json.loads((ROOT / "projects/strongminds/data/gt_510.json").read_text(encoding="utf-8"))
    gt_bin = {k: ("INCLUDE" if "INCLUDE" in v.upper() else "EXCLUDE") for k, v in gt.items()}
    # cleaned GT: flip the 12 clear GT-error candidates from Step 1
    adj = [json.loads(l) for l in lines("projects/strongminds/artifacts/gt_adjudication.jsonl")]
    gt_clean = dict(gt_bin)
    flipped = []
    for a in adj:
        if a["verdict"] == "gt_error_candidate":
            rid = a["record_id"]
            gt_clean[rid] = a["pipeline_decision"]   # adjudicators + pipeline agree this is right
            flipped.append(rid)
    # v1.6 baseline decisions
    v16 = {}
    for l in lines("projects/strongminds/data/output/results_orch_v16_510.jsonl"):
        d = json.loads(l)
        v16[str(d["record_id"])] = {
            "decision": d["screening_decision"],
            "vote": float(d.get("vote_share_include", 0) or 0),
        }
    return recs, gt, gt_bin, gt_clean, flipped, v16


def stratified_split(recs, gt_bin):
    inc = sorted([r for r in recs if gt_bin.get(r) == "INCLUDE"])
    exc = sorted([r for r in recs if gt_bin.get(r) == "EXCLUDE"])
    rng = random.Random(SEED)
    rng.shuffle(inc); rng.shuffle(exc)
    n_inc_test = round(len(inc) * TEST_FRAC)
    n_exc_test = round(len(exc) * TEST_FRAC)
    test = set(inc[:n_inc_test] + exc[:n_exc_test])
    train = set(recs) - test
    return train, test


def build_index(train, recs):
    from rank_bm25 import BM25Okapi
    ids = sorted(train)
    def toks(rid):
        t = (recs[rid].get("title", "") + " " + recs[rid].get("abstract", "")).lower()
        return [w for w in "".join(c if c.isalnum() else " " for c in t).split() if len(w) > 2]
    corpus = [toks(rid) for rid in ids]
    return BM25Okapi(corpus), ids, toks


def retrieve(rid, bm25, ids, toks, gt, recs, k=N_EXEMPLARS):
    scores = bm25.get_scores(toks(rid))
    ranked = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)
    # take top candidates, then balance labels so the model sees the boundary
    top = [ids[i] for i in ranked[:16]]
    inc = [x for x in top if "INCLUDE" in gt.get(x, "").upper()]
    exc = [x for x in top if "INCLUDE" not in gt.get(x, "").upper()]
    picked, i, j = [], 0, 0
    while len(picked) < k and (i < len(inc) or j < len(exc)):
        if j < len(exc): picked.append(exc[j]); j += 1
        if len(picked) < k and i < len(inc): picked.append(inc[i]); i += 1
    return picked


def fmt_exemplars(ex_ids, gt, recs):
    out = []
    for x in ex_ids:
        r = recs[x]
        out.append(
            f"- GOLD: {gt.get(x)}\n  TITLE: {r.get('title','')[:140]}\n"
            f"  ABSTRACT: {r.get('abstract','')[:420]}"
        )
    return "EXEMPLARS:\n" + "\n".join(out)


def screen_one(model, rid, recs, exemplar_block, temperature):
    r = recs[rid]
    user = (
        f"{exemplar_block}\n\n=== TARGET RECORD ===\n"
        f"screening_level: {r.get('screening_level','review')}\nyear: {r.get('year','')}\n"
        f"TITLE: {r.get('title','')}\n\nABSTRACT: {r.get('abstract','')}\n"
    )
    try:
        raw = call_openrouter(model, RUBRIC, user, temperature, max_tokens=2000)
        obj = extract_json(raw) or {}
        dec = str(obj.get("decision", "")).upper()
        return dec if dec in ("INCLUDE", "EXCLUDE") else None
    except Exception:
        return None


CRITIC_RUBRIC = RUBRIC.replace(
    "You are screening a Title & Abstract",
    "You are the SENIOR ADJUDICATOR. Two independent screeners DISAGREED on this Title & Abstract"
) + "\n\nRe-screen INDEPENDENTLY and give the deciding call."


def critic_one(rid, recs, exemplar_block):
    r = recs[rid]
    user = (
        f"{exemplar_block}\n\n=== CONTESTED RECORD (screeners split INCLUDE vs EXCLUDE) ===\n"
        f"screening_level: {r.get('screening_level','review')}\nyear: {r.get('year','')}\n"
        f"TITLE: {r.get('title','')}\n\nABSTRACT: {r.get('abstract','')}\n"
    )
    try:
        raw = call_openrouter(CRITIC_MODEL, CRITIC_RUBRIC, user, 0.0, max_tokens=2000)
        obj = extract_json(raw) or {}
        dec = str(obj.get("decision", "")).upper()
        return dec if dec in ("INCLUDE", "EXCLUDE") else None
    except Exception:
        return None


def kappa(tp, fp, fn, tn):
    n = tp + fp + fn + tn
    if n == 0: return float("nan")
    po = (tp + tn) / n
    pp, ap = (tp + fp) / n, (tp + fn) / n
    pe = pp * ap + (1 - pp) * (1 - ap)
    return (po - pe) / (1 - pe) if pe != 1 else float("nan")


def metrics(decisions, gt_bin, test):
    tp = fp = fn = tn = 0
    for rid in test:
        d = decisions.get(rid); g = gt_bin.get(rid)
        if d is None: continue
        if d == "INCLUDE" and g == "INCLUDE": tp += 1
        elif d == "INCLUDE" and g == "EXCLUDE": fp += 1
        elif d == "EXCLUDE" and g == "INCLUDE": fn += 1
        else: tn += 1
    sens = tp / (tp + fn) if (tp + fn) else float("nan")
    spec = tn / (tn + fp) if (tn + fp) else float("nan")
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, sens=sens, spec=spec, kappa=kappa(tp, fp, fn, tn))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k5", action="store_true", help="k=5 temp0.3 (else k=1 temp0)")
    ap.add_argument("--no-exemplars", action="store_true", help="control: strip exemplar block (isolates temperature effect)")
    ap.add_argument("--critic", action="store_true", help="TWIST: mistral-large breaks screener-disagreement ties")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    k = 5 if args.k5 else 1
    temp = 0.3 if args.k5 else 0.0

    recs, gt_raw, gt_bin, gt_clean, flipped, v16 = load()
    train, test = stratified_split(recs, gt_bin)
    print(f"Split: train={len(train)} test={len(test)} | "
          f"test INCLUDE={sum(gt_bin[r]=='INCLUDE' for r in test)} "
          f"EXCLUDE={sum(gt_bin[r]=='EXCLUDE' for r in test)}", file=sys.stderr)
    print(f"Cleaned GT flips {len(flipped)} labels", file=sys.stderr)

    if args.no_exemplars:
        ex_blocks = {rid: "(No exemplars provided. Apply the protocol directly.)" for rid in test}
        print("CONTROL MODE: exemplars stripped", file=sys.stderr)
    else:
        bm25, ids, toks = build_index(train, recs)
        ex_blocks = {rid: fmt_exemplars(retrieve(rid, bm25, ids, toks, gt_raw, recs), gt_raw, recs) for rid in test}

    # run few-shot screener on test
    per_model_votes = {rid: {m: [] for m in MODELS} for rid in test}
    tasks = [(m, rid, run) for rid in test for m in MODELS for run in range(k)]
    print(f"Running {len(tasks)} calls (k={k}, temp={temp})...", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(screen_one, m, rid, recs, ex_blocks[rid], temp): (m, rid) for (m, rid, _) in tasks}
        done = 0
        for fut in cf.as_completed(futs):
            m, rid = futs[fut]
            per_model_votes[rid][m].append(fut.result())
            done += 1
            if done % 50 == 0: print(f"  {done}/{len(tasks)}", file=sys.stderr)

    # aggregate: vote share INCLUDE across all runs & both models
    fs_decision, fs_vote = {}, {}
    ties = []
    for rid in test:
        allv = [v for m in MODELS for v in per_model_votes[rid][m] if v]
        if not allv:
            fs_decision[rid] = None; fs_vote[rid] = None; continue
        share = sum(v == "INCLUDE" for v in allv) / len(allv)
        fs_vote[rid] = share
        fs_decision[rid] = "INCLUDE" if share >= 0.5 else "EXCLUDE"
        if abs(share - 0.5) <= 0.1:
            ties.append(rid)

    critic_calls = {}
    if args.critic and ties:
        print(f"Critic breaking {len(ties)} disagreement ties...", file=sys.stderr)
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            cf_futs = {ex.submit(critic_one, rid, recs, ex_blocks[rid]): rid for rid in ties}
            for fut in cf.as_completed(cf_futs):
                rid = cf_futs[fut]
                verdict = fut.result()
                critic_calls[rid] = verdict
                if verdict:
                    fs_decision[rid] = verdict

    base_decision = {rid: v16.get(rid, {}).get("decision") for rid in test}

    # report
    def show(title, dec, gt):
        m = metrics(dec, gt, test)
        print(f"  {title:28s} sens={m['sens']:.3f} spec={m['spec']:.3f} "
              f"kappa={m['kappa']:.3f}  (tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']})")

    print(f"\n{'='*74}\nSTEP 3 RESULTS on held-out TEST split (N={len(test)}), k={k}\n{'='*74}")
    print("\n--- vs ORIGINAL GT ---")
    show("v1.6 baseline (same recs)", base_decision, gt_bin)
    show("few-shot exemplar screener", fs_decision, gt_bin)
    print("\n--- vs CLEANED GT (12 Step-1 fixes) ---")
    show("v1.6 baseline (same recs)", base_decision, gt_clean)
    show("few-shot exemplar screener", fs_decision, gt_clean)

    out = {"split_seed": SEED, "test_ids": sorted(test), "flipped": flipped,
           "fs_decision": fs_decision, "fs_vote": fs_vote, "base_decision": base_decision,
           "k": k, "temp": temp}
    if args.critic:
        resolved = sum(1 for r in ties if critic_calls.get(r))
        print(f"\nCritic resolved {resolved}/{len(ties)} ties "
              f"(-> {sum(critic_calls.get(r)=='EXCLUDE' for r in ties)} EXCLUDE, "
              f"{sum(critic_calls.get(r)=='INCLUDE' for r in ties)} INCLUDE)")
    tag = f"k{k}" + ("_noex" if args.no_exemplars else "") + ("_critic" if args.critic else "")
    (ROOT / "projects/strongminds/artifacts" / f"fewshot_results_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote {ROOT / 'projects/strongminds/artifacts' / f'fewshot_results_{tag}.json'}")


if __name__ == "__main__":
    main()
