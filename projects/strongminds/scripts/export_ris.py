"""export_ris.py — Export screening results to a RIS file.

Reads the tiebreak results JSONL + the original RIS records, filters to INCLUDEs,
and writes a RIS file suitable for import into Zotero/EndNote/Mendeley.

Usage:
    python projects/strongminds/scripts/export_ris.py
        --results projects/strongminds/data/output/results_ris_v19_tiebreak.jsonl
        --records  projects/strongminds/data/ris_records.jsonl
        --out      projects/strongminds/data/output/includes.ris
        --decision INCLUDE
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

# RIS tag mapping: field -> RIS tag
# Based on the RIS format spec and what ingest_ris.py reads
TAG_MAP = {
    "type": "TY",
    "title": "T1",
    "year": "PY",
    "abstract": "AB",
    "doi": "DO",
    "record_id": "U1",
}


def load_jsonl(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            out[str(r["record_id"])] = r
    return out


def write_ris(records: dict, include_ids: set, out_path: Path, default_type: str = "JOUR"):
    n = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for rid in sorted(include_ids):
            rec = records.get(rid)
            if not rec:
                continue
            rtype = rec.get("type") or default_type
            # Normalize type to RIS TY values
            rtype_map = {
                "JOUR": "JOUR", "JOURNAL": "JOUR", "ARTICLE": "JOUR",
                "BOOK": "BOOK", "CHAP": "CHAP", "CHAPTER": "CHAP",
                "THES": "THES", "THESIS": "THES",
                "CONF": "CONF", "ELEC": "ELEC", "EJOUR": "JOUR",
                "GEN": "GEN", "RPRT": "RPRT", "REPORT": "RPRT",
            }
            ty = rtype_map.get(rtype.upper(), default_type)
            f.write(f"TY  - {ty}\n")
            if rec.get("title"):
                f.write(f"T1  - {rec['title']}\n")
            if rec.get("year"):
                f.write(f"PY  - {rec['year']}\n")
            if rec.get("abstract") and rec["abstract"] != "NA":
                f.write(f"AB  - {rec['abstract']}\n")
            if rec.get("doi"):
                doi = rec["doi"].lower().replace("https://doi.org/", "").replace("http://doi.org/", "")
                f.write(f"DO  - {doi}\n")
            if rec.get("record_id"):
                f.write(f"U1  - {rec['record_id']}\n")
            f.write("ER  - \n\n")
            n += 1
    return n


def main():
    p = argparse.ArgumentParser(description="Export screening results to RIS file.")
    p.add_argument("--results", required=True, help="Tiebreak results JSONL")
    p.add_argument("--records", required=True, help="RIS records JSONL (full corpus)")
    p.add_argument("--out", required=True, help="Output RIS file path")
    p.add_argument("--decision", default="INCLUDE", help="Filter: INCLUDE or EXCLUDE")
    args = p.parse_args()

    print("Loading records...", file=sys.stderr)
    records = load_jsonl(args.records)
    print(f"  {len(records)} records", file=sys.stderr)

    print("Loading results...", file=sys.stderr)
    results = load_jsonl(args.results)
    print(f"  {len(results)} results", file=sys.stderr)

    # Filter by decision
    include_ids = set()
    for rid, res in results.items():
        if res.get("screening_decision") == args.decision:
            include_ids.add(rid)

    print(f"  {len(include_ids)} records with decision={args.decision}", file=sys.stderr)

    n = write_ris(records, include_ids, Path(args.out))
    print(f"\nWrote {n} records to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
