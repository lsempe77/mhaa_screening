"""Score RIS records with the deployable config (exemplars + temp0, k=1, 2 models).

Exemplars retrieved from the 510 labeled seed (leave-one-out when the target IS a
seed record, so no record sees its own label).

--pilot : score the 502 labeled seed-overlap records (-> recall/precision at cutoffs)
          + a random N unlabeled records (-> flag rate, score distribution, cost).
--full  : score all unique RIS records with an abstract (minus seed overlap).

Output score per record = INCLUDE vote share across both models (the ranking signal).
"""
import json, sys, random, argparse, concurrent.futures as cf
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import strongminds.fewshot_screen as F   # reuse RUBRIC, MODELS, screen_one, fmt_exemplars, build_index

SEED = 20260720
N_EXEMPLARS = 4

def lines(p): return [l for l in (ROOT / p).read_text(encoding="utf-8").split("\n") if l.strip()]

def load():
    seed_recs = {}
    for l in lines("strongminds/data/records_510.jsonl"):
        r = json.loads(l); seed_recs[str(r["record_id"])] = r
    gt = json.loads((ROOT / "strongminds/data/gt_510.json").read_text(encoding="utf-8"))
    gt_bin = {k: ("INCLUDE" if "INCLUDE" in v.upper() else "EXCLUDE") for k, v in gt.items()}
    ris = {}
    for l in lines("strongminds/data/ris_records.jsonl"):
        r = json.loads(l)
        if r["record_id"]:
            ris[str(r["record_id"])] = r
    return seed_recs, gt, gt_bin, ris

def retrieve_excl(rid, bm25, ids, toks, gt, recs, exclude, k=N_EXEMPLARS):
    scores = bm25.get_scores(toks(rid))
    ranked = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)
    top = [ids[i] for i in ranked if ids[i] != exclude][:16]
    inc = [x for x in top if "INCLUDE" in gt.get(x, "").upper()]
    exc = [x for x in top if "INCLUDE" not in gt.get(x, "").upper()]
    picked, i, j = [], 0, 0
    while len(picked) < k and (i < len(inc) or j < len(exc)):
        if j < len(exc): picked.append(exc[j]); j += 1
        if len(picked) < k and i < len(inc): picked.append(inc[i]); i += 1
    return picked

def score_batch(target_ids, recs_lookup, bm25, seed_ids, toks, gt, workers):
    """Return {rid: vote_share_include} using both models at temp 0 (k=1)."""
    ex_blocks = {}
    for rid in target_ids:
        ex_ids = retrieve_excl(rid, bm25, seed_ids, toks, gt, recs_lookup, exclude=rid)
        ex_blocks[rid] = F.fmt_exemplars(ex_ids, gt, recs_lookup)
    votes = {rid: [] for rid in target_ids}
    tasks = [(m, rid) for rid in target_ids for m in F.MODELS]
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(F.screen_one, m, rid, recs_lookup, ex_blocks[rid], 0.0): (m, rid) for (m, rid) in tasks}
        done = 0
        for fut in cf.as_completed(futs):
            m, rid = futs[fut]
            votes[rid].append(fut.result())
            done += 1
            if done % 100 == 0: print(f"  {done}/{len(tasks)}", file=sys.stderr)
    out = {}
    for rid in target_ids:
        vs = [v for v in votes[rid] if v]
        out[rid] = (sum(v == "INCLUDE" for v in vs) / len(vs)) if vs else None
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--n-random", type=int, default=500)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    seed_recs, gt, gt_bin, ris = load()
    recs_lookup = {**ris, **seed_recs}   # seed content authoritative for overlap ids
    seed_ids = sorted(seed_recs)
    bm25, ids, toks = F.build_index(set(seed_ids), recs_lookup)

    rng = random.Random(SEED)

    if args.pilot:
        overlap = sorted(set(gt) & set(ris))
        ris_only = [r for r in ris if r not in set(gt) and ris[r]["abstract"]]
        rng.shuffle(ris_only)
        random_sample = ris_only[:args.n_random]
        print(f"Pilot: {len(overlap)} labeled seed-overlap + {len(random_sample)} random unlabeled", file=sys.stderr)

        lab_scores = score_batch(overlap, recs_lookup, bm25, ids, toks, gt, args.workers)
        rnd_scores = score_batch(random_sample, recs_lookup, bm25, ids, toks, gt, args.workers)

        # recall/precision at cutoffs on the labeled overlap
        print("\n" + "=" * 66)
        print("A) VALIDATION on 502 labeled seed records (in-corpus, LOO exemplars)")
        print("=" * 66)
        pos = [r for r in overlap if gt_bin[r] == "INCLUDE"]
        print(f"labeled includes={len(pos)}  excludes={len(overlap)-len(pos)}")
        print(f"{'cutoff':>8} {'flagged':>8} {'recall':>8} {'precision':>10} {'workload%':>10}")
        for cut in [0.0, 0.25, 0.5, 0.75, 1.0]:  # 'score >= cut -> flag INCLUDE'; 0.0 means any INCLUDE vote
            flagged = [r for r in overlap if (lab_scores[r] or 0) >= cut and (lab_scores[r] or 0) > 0] if cut > 0 else \
                      [r for r in overlap if (lab_scores[r] or 0) > 0]
            tp = sum(gt_bin[r] == "INCLUDE" for r in flagged)
            rec = tp / len(pos) if pos else float("nan")
            prec = tp / len(flagged) if flagged else float("nan")
            print(f"{cut:>8.2f} {len(flagged):>8} {rec:>8.3f} {prec:>10.3f} {len(flagged)/len(overlap):>10.1%}")

        print("\n" + "=" * 66)
        print(f"B) FLAG RATE / SCORE DIST on {len(random_sample)} random unlabeled RIS records")
        print("=" * 66)
        dist = Counter()
        for r in random_sample:
            s = rnd_scores[r]
            b = "none/err" if s is None else "0.0(excl)" if s == 0 else "0.5(split)" if s == 0.5 else "1.0(incl)" if s == 1.0 else f"{s:.2f}"
            dist[b] += 1
        for k, v in sorted(dist.items()):
            print(f"  {k:>12}: {v:5d}  ({v/len(random_sample):.1%})")
        flag = sum(1 for r in random_sample if (rnd_scores[r] or 0) > 0)
        print(f"\n  FLAG RATE (any INCLUDE vote): {flag}/{len(random_sample)} = {flag/len(random_sample):.1%}")
        print(f"  -> projected flags over 29,251 corpus: ~{round(flag/len(random_sample)*29251):,}")
        confident_excl = sum(1 for r in random_sample if rnd_scores[r] == 0)
        print(f"  CONFIDENT-EXCLUDE (both models EXCLUDE): {confident_excl/len(random_sample):.1%}"
              f"  -> ~{round(confident_excl/len(random_sample)*29251):,} auto-excludable candidates")

        out = {"labeled_scores": lab_scores, "random_scores": rnd_scores,
               "overlap_ids": overlap, "random_ids": random_sample}
        (HERE / "ris_pilot_scores.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {HERE/'ris_pilot_scores.json'}")

    elif args.full:
        targets = [r for r in ris if r not in set(gt) and ris[r]["abstract"]]
        print(f"Full scoring: {len(targets)} records (excl seed + abstract-less)", file=sys.stderr)
        scores = score_batch(targets, recs_lookup, bm25, ids, toks, gt, args.workers)
        with (HERE / "ris_full_scores.jsonl").open("w", encoding="utf-8") as fh:
            for rid in targets:
                r = ris[rid]
                fh.write(json.dumps({"record_id": rid, "score": scores[rid],
                                     "title": r["title"], "year": r["year"]}, ensure_ascii=False) + "\n")
        print(f"Wrote {HERE/'ris_full_scores.jsonl'}")
    else:
        print("specify --pilot or --full")

if __name__ == "__main__":
    main()
