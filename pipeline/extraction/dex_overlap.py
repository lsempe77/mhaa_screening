"""dex_overlap.py — Cross-review overlap / double-counting analysis for the DEX corpus.

Entry points are SRs/MAs, so the same primary trials are pooled in multiple reviews.
Synthesising across reviews without accounting for this double-counts evidence and
overstates certainty. This uses the extracted `included_study_ids` to:
  - map each primary study -> the reviews that include it (the double-counting map),
  - cluster reviews that share studies and compute the Corrected Covered Area (CCA,
    Pieper 2014) per cluster,
  - emit per-review overlap metrics that join to dex_summary by record_id.

It only FLAGS overlap; it never drops records. Study matching from author-year strings
is approximate (DOIs are reliable) — high-overlap clusters should be human-confirmed.

Outputs (in --out-dir): dex_overlap_studies.csv, dex_overlap_reviews.csv,
dex_overlap_clusters.csv. Prints a summary.

Usage:
    python pipeline/extraction/dex_overlap.py \
        --results projects/strongminds/data/extraction/dex_full_2670.jsonl \
        --out-dir projects/strongminds/data/extraction/reports
"""
from __future__ import annotations
import argparse, csv, json, re, sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def study_key(s: str) -> str | None:
    """Normalise a study identifier to a match key: DOI if present, else surname:year."""
    if not s or not str(s).strip():
        return None
    s = str(s).strip().lower()
    m = re.search(r"10\.\d{4,9}/[^\s,;]+", s)
    if m:
        return "doi:" + m.group(0).rstrip(".")
    ym = re.search(r"(19|20)\d{2}", s)
    year = ym.group(0) if ym else "????"
    s2 = re.sub(r"\bet al\.?\b", "", s)
    wm = re.search(r"[a-z][a-z'-]{2,}", s2)          # first surname-like token
    surname = wm.group(0) if wm else re.sub(r"[^a-z]", "", s2)[:8] or "anon"
    return f"{surname}:{year}"


def main():
    p = argparse.ArgumentParser(description="Cross-review overlap / CCA analysis.")
    p.add_argument("--results", required=True)
    p.add_argument("--out-dir", default="projects/strongminds/data/extraction/reports")
    p.add_argument("--min-shared", type=int, default=2,
                   help="min shared studies to link two reviews into a cluster")
    a = p.parse_args()

    # review_id -> set(study keys); keep only reviews that listed studies
    rev_studies: dict[str, set] = {}
    n_ids = n_parsed = 0
    for line in open(a.results, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("_error"):
            continue
        f = r.get("fields", {})
        cell = f.get("included_study_ids") or {}
        ids = cell.get("value") if isinstance(cell, dict) else None
        if not isinstance(ids, list) or not ids:
            continue
        keys = set()
        for s in ids:
            n_ids += 1
            k = study_key(s)
            if k:
                keys.add(k); n_parsed += 1
        if keys:
            rev_studies[str(r["record_id"])] = keys

    # study -> reviews
    study_revs: dict[str, set] = defaultdict(set)
    for rid, keys in rev_studies.items():
        for k in keys:
            study_revs[k].add(rid)

    # pairwise shared counts (only for reviews that share >=1 study)
    pair_shared: dict[tuple, int] = defaultdict(int)
    for k, revs in study_revs.items():
        revs = sorted(revs)
        for i in range(len(revs)):
            for j in range(i + 1, len(revs)):
                pair_shared[(revs[i], revs[j])] += 1

    # clusters: connected components over edges with shared >= min_shared
    adj: dict[str, set] = defaultdict(set)
    for (u, v), n in pair_shared.items():
        if n >= a.min_shared:
            adj[u].add(v); adj[v].add(u)
    seen, clusters = set(), []
    for node in rev_studies:
        if node in seen:
            continue
        stack, comp = [node], []
        seen.add(node)
        while stack:
            x = stack.pop(); comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y); stack.append(y)
        clusters.append(comp)

    def cca(reviews: list[str]) -> tuple[float, int, int, int]:
        cols = set().union(*[rev_studies[r] for r in reviews]) if reviews else set()
        c = len(cols); rr = len(reviews)
        N = sum(len(rev_studies[r]) for r in reviews)      # total inclusions (with dups)
        val = (N - c) / (c * (rr - 1)) * 100 if (c and rr > 1) else 0.0
        return round(val, 1), N, rr, c

    def rate(v):
        return ("slight" if v <= 5 else "moderate" if v <= 10 else "high" if v <= 15 else "very-high")

    import os
    os.makedirs(a.out_dir, exist_ok=True)

    # studies file
    studies_rows = [{"study_key": k, "n_reviews": len(revs), "review_ids": ";".join(sorted(revs))}
                    for k, revs in study_revs.items()]
    studies_rows.sort(key=lambda x: -x["n_reviews"])
    with open(f"{a.out_dir}/dex_overlap_studies.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["study_key", "n_reviews", "review_ids"]); w.writeheader(); w.writerows(studies_rows)

    # per-review file (joins to dex_summary by record_id)
    cluster_of = {r: i for i, comp in enumerate(clusters) for r in comp}
    rev_rows = []
    for rid, keys in rev_studies.items():
        shared = sum(1 for k in keys if len(study_revs[k]) > 1)
        # max Jaccard with any other review
        maxj = 0.0; partner = ""
        for other, okeys in rev_studies.items():
            if other == rid:
                continue
            inter = len(keys & okeys)
            if inter:
                j = inter / len(keys | okeys)
                if j > maxj:
                    maxj, partner = j, other
        comp = clusters[cluster_of[rid]]
        rev_rows.append({"record_id": rid, "n_studies": len(keys), "n_shared_studies": shared,
                         "max_jaccard": round(maxj, 2), "max_jaccard_partner": partner,
                         "cluster_id": cluster_of[rid], "cluster_size": len(comp)})
    rev_rows.sort(key=lambda x: (-x["cluster_size"], -x["n_shared_studies"]))
    with open(f"{a.out_dir}/dex_overlap_reviews.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rev_rows[0].keys())); w.writeheader(); w.writerows(rev_rows)

    # clusters file (multi-review clusters only)
    clus_rows = []
    for i, comp in enumerate(clusters):
        if len(comp) < 2:
            continue
        v, N, rr, c = cca(comp)
        clus_rows.append({"cluster_id": i, "n_reviews": rr, "n_unique_studies": c,
                          "total_inclusions": N, "cca_pct": v, "overlap_rating": rate(v),
                          "review_ids": ";".join(sorted(comp))})
    clus_rows.sort(key=lambda x: -x["n_reviews"])
    with open(f"{a.out_dir}/dex_overlap_clusters.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["cluster_id", "n_reviews", "n_unique_studies",
                          "total_inclusions", "cca_pct", "overlap_rating", "review_ids"])
        w.writeheader(); w.writerows(clus_rows)

    # summary
    multi = [c for c in clusters if len(c) >= 2]
    shared_studies = sum(1 for revs in study_revs.values() if len(revs) > 1)
    print(f"Reviews with included-study lists: {len(rev_studies)}")
    print(f"Study identifiers: {n_ids} listed, {n_parsed} parsed → {len(study_revs)} unique studies")
    print(f"Studies shared by >1 review: {shared_studies} ({100*shared_studies/max(1,len(study_revs)):.0f}%)")
    print(f"Overlap clusters (>=2 reviews, >={a.min_shared} shared): {len(multi)} "
          f"covering {sum(len(c) for c in multi)} reviews")
    if clus_rows:
        print("\nLargest / highest-overlap clusters:")
        for r in clus_rows[:8]:
            print(f"  cluster {r['cluster_id']}: {r['n_reviews']} reviews, {r['n_unique_studies']} studies, "
                  f"CCA {r['cca_pct']}% ({r['overlap_rating']})")
    print("\nMost-shared primary studies:")
    for r in studies_rows[:8]:
        if r["n_reviews"] > 1:
            print(f"  {r['study_key']}: in {r['n_reviews']} reviews")
    print(f"\nWrote dex_overlap_studies.csv / _reviews.csv / _clusters.csv → {a.out_dir}")


if __name__ == "__main__":
    main()
