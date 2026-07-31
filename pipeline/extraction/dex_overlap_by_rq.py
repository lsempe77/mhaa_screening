"""dex_overlap_by_rq.py — Structured (per-RQ) overlap and discordance.

The global overlap analysis (dex_overlap.py) chains reviews across all questions. The
methodologically correct unit is *within a research question*: reviews that address the
same question and pool the same trials. For each RQ this computes:

  - the Corrected Covered Area (CCA, Pieper 2014) among the reviews addressing that RQ, and
  - discordance: among review pairs that share primary trials, how often they report
    opposing effect directions (one favouring the intervention, the other null/favouring
    control). High discordance despite shared evidence signals a methods-driven disagreement.

Reads dex_full_2670.jsonl (needs included_study_ids, eff_direction, rq_tags per review).
Writes dex_overlap_by_rq.csv.

Usage:
    python pipeline/extraction/dex_overlap_by_rq.py \
        --results projects/strongminds/data/extraction/dex_full_2670.jsonl \
        --out projects/strongminds/data/extraction/reports/dex_overlap_by_rq.csv
"""
from __future__ import annotations
import argparse, csv, json, sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dex_overlap import study_key  # reuse the identifier normaliser

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DIR_SIGN = {"Favours-intervention": 1, "Null": 0, "Favours-control": -1, "Unclear": 0}


def cca(reviews, rev_studies):
    cols = set().union(*[rev_studies[r] for r in reviews]) if reviews else set()
    c, r = len(cols), len(reviews)
    N = sum(len(rev_studies[x]) for x in reviews)
    return ((N - c) / (c * (r - 1)) * 100 if (c and r > 1) else 0.0), N, r, c


def rating(v):
    return "slight" if v <= 5 else "moderate" if v <= 10 else "high" if v <= 15 else "very-high"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-shared", type=int, default=2)
    a = ap.parse_args()

    # per review: rq tags, study keys, effect-direction sign
    rq_reviews = defaultdict(list)
    studies = {}
    sign = {}
    for line in open(a.results, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("_error") or r.get("unit_of_extraction") != "review":
            continue
        f = r.get("fields", {})
        ids = (f.get("included_study_ids") or {}).get("value")
        if not isinstance(ids, list) or not ids:
            continue
        rid = str(r["record_id"])
        keys = {k for k in (study_key(s) for s in ids) if k}
        if not keys:
            continue
        studies[rid] = keys
        d = (f.get("eff_direction") or {}).get("value")
        sign[rid] = DIR_SIGN.get(d)
        for t in (f.get("rq_tags") or {}).get("value") or []:
            if isinstance(t, str) and t.startswith("RQ"):
                rq_reviews[t].append(rid)

    rows = []
    for n in range(1, 19):
        rq = f"RQ{n}"
        revs = [x for x in rq_reviews.get(rq, []) if x in studies]
        if len(revs) < 2:
            rows.append({"rq": rq, "n_reviews_with_studies": len(revs), "n_unique_studies": "",
                         "cca_pct": "", "overlap_rating": "", "shared_pairs": 0,
                         "discordant_pairs": 0, "discordance_pct": ""})
            continue
        val, N, r, c = cca(revs, studies)
        shared = disc = 0
        for a1, b1 in combinations(revs, 2):
            sh = len(studies[a1] & studies[b1])
            if sh >= a.min_shared:
                shared += 1
                s1, s2 = sign.get(a1), sign.get(b1)
                if s1 is not None and s2 is not None and s1 != s2:
                    disc += 1
        rows.append({"rq": rq, "n_reviews_with_studies": len(revs), "n_unique_studies": c,
                     "cca_pct": round(val, 1), "overlap_rating": rating(val),
                     "shared_pairs": shared, "discordant_pairs": disc,
                     "discordance_pct": round(100 * disc / shared, 1) if shared else ""})

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    print(f"{'RQ':<5}{'reviews':>8}{'studies':>8}{'CCA%':>7}  {'rating':<10}{'sharedPr':>9}{'disc':>6}{'disc%':>7}")
    for r in rows:
        print(f"{r['rq']:<5}{r['n_reviews_with_studies']:>8}{str(r['n_unique_studies']):>8}"
              f"{str(r['cca_pct']):>7}  {str(r['overlap_rating']):<10}{r['shared_pairs']:>9}"
              f"{r['discordant_pairs']:>6}{str(r['discordance_pct']):>7}")
    print(f"\nWrote {a.out}")


if __name__ == "__main__":
    main()
