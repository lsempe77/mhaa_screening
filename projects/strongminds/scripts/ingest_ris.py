"""Parse the StrongMinds RIS corpus -> dedup -> audit.

Reads all .txt RIS files in strongminds/strongminds_ris/, extracts the fields the
T/A screener needs (id, title, abstract, year, type, doi), deduplicates in stages
(EPPI id -> DOI -> normalized title), and reports scale + data-quality stats
(missing abstracts/titles, pre-2000, type mix, overlap with the 510 labeled seed).

Output: strongminds/data/ris_records.jsonl  (deduped, screener-ready)
No API calls. This is the free 'know the real scale' step before any scoring run.
"""
import json, re, sys
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]        # repo root
RIS_DIR = ROOT / "projects/strongminds/strongminds_ris"
OUT = ROOT / "projects/strongminds/data/ris_records.jsonl"

TAG_RE = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")

def parse_ris(path):
    """Yield dict per record. Handles multi-line values (continuation lines)."""
    recs = []
    cur = defaultdict(list)
    last_tag = None
    for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = raw.rstrip("\r")
        m = TAG_RE.match(line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "ER":
                if cur:
                    recs.append(dict(cur)); cur = defaultdict(list)
                last_tag = None
            else:
                cur[tag].append(val); last_tag = tag
        elif line.strip() and last_tag:      # continuation of previous field
            cur[last_tag][-1] = (cur[last_tag][-1] + " " + line.strip()).strip()
    if cur:
        recs.append(dict(cur))
    return recs

def first(d, *tags):
    for t in tags:
        if d.get(t) and d[t][0].strip():
            return d[t][0].strip()
    return ""

def norm_year(s):
    m = re.search(r"(\d{4})", s or "")
    return m.group(1) if m else ""

def norm_title(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def main():
    files = sorted(RIS_DIR.glob("*.txt"))
    print(f"RIS files: {len(files)}", file=sys.stderr)
    all_recs = []
    per_file = {}
    for f in files:
        rs = parse_ris(f)
        per_file[f.name] = len(rs)
        for d in rs:
            all_recs.append({
                "record_id": first(d, "U1"),
                "title": first(d, "T1", "TI"),
                "abstract": first(d, "AB", "N2"),
                "year": norm_year(first(d, "PY", "Y1", "DA")),
                "type": first(d, "TY"),
                "doi": first(d, "DO").lower(),
                "source_file": f.name,
            })

    raw_n = len(all_recs)

    # ---- dedup in stages ----
    seen_id, after_id = set(), []
    no_id = 0
    for r in all_recs:
        rid = r["record_id"]
        if not rid:
            no_id += 1
            after_id.append(r)                    # keep id-less for now (dedup by title later)
        elif rid not in seen_id:
            seen_id.add(rid); after_id.append(r)
    n_after_id = len(after_id)

    seen_doi, after_doi = set(), []
    for r in after_id:
        doi = r["doi"]
        if doi and doi in seen_doi:
            continue
        if doi:
            seen_doi.add(doi)
        after_doi.append(r)
    n_after_doi = len(after_doi)

    seen_title, final = set(), []
    for r in after_doi:
        t = norm_title(r["title"])
        if t and t in seen_title:
            continue
        if t:
            seen_title.add(t)
        final.append(r)
    n_final = len(final)

    # ---- audit ----
    no_abs = sum(1 for r in final if not r["abstract"])
    no_title = sum(1 for r in final if not r["title"])
    pre2000 = sum(1 for r in final if r["year"].isdigit() and int(r["year"]) < 2000)
    no_year = sum(1 for r in final if not r["year"].isdigit())
    types = Counter(r["type"] for r in final)

    # overlap with 510 labeled seed
    gt = json.loads((ROOT / "projects/strongminds/data/gt_510.json").read_text(encoding="utf-8"))
    seed_ids = set(gt)
    final_ids = {r["record_id"] for r in final if r["record_id"]}
    overlap = seed_ids & final_ids

    with OUT.open("w", encoding="utf-8") as fh:
        for r in final:
            r["screening_level"] = "review"
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("\n" + "=" * 64)
    print("RIS INGEST + DEDUP + AUDIT")
    print("=" * 64)
    print("\nPer-file record counts:")
    for k, v in per_file.items():
        print(f"  {v:6d}  {k}")
    print(f"\nDEDUP FUNNEL:")
    print(f"  raw parsed records:        {raw_n:6d}")
    print(f"  after EPPI-id dedup:       {n_after_id:6d}  (removed {raw_n-n_after_id}; {no_id} had no id)")
    print(f"  after DOI dedup:           {n_after_doi:6d}  (removed {n_after_id-n_after_doi})")
    print(f"  after title dedup:         {n_final:6d}  (removed {n_after_doi-n_final})")
    print(f"  >>> UNIQUE RECORDS:        {n_final:6d}")
    print(f"\nDATA QUALITY (of {n_final} unique):")
    print(f"  missing abstract:          {no_abs:6d}  ({no_abs/n_final:.1%})")
    print(f"  missing title:             {no_title:6d}  ({no_title/n_final:.1%})")
    print(f"  missing/invalid year:      {no_year:6d}  ({no_year/n_final:.1%})")
    print(f"  pre-2000 (auto-excludable): {pre2000:6d}  ({pre2000/n_final:.1%})")
    print(f"\nRECORD TYPES (top): {dict(types.most_common(8))}")
    print(f"\nOVERLAP WITH 510 SEED:       {len(overlap):6d} of 510 seed ids found in RIS")
    print(f"  (these are already labeled; can be excluded from scoring or used to validate)")
    print(f"\nWrote {OUT}")

if __name__ == "__main__":
    main()
