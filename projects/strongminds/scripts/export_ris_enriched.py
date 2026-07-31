"""export_ris_enriched.py — Enrich the bare FTS-includes RIS with full bibliographic
metadata pulled from the original RIS corpus (strongminds_ris/*.txt).

The screening/export pipeline kept only id/title/year/doi (ingest_ris.py drops the
rest), so the exported includes RIS is bare. This re-reads the *source* corpus —
which carries authors (A1), journal (JF), volume (VL), issue (IS), pages (SP/EP),
ISSN (SN), abstract (AB) and keywords (KW) — and re-emits each included record with
those fields.

Matching: by EPPI id (U1) first, then by normalised DOI. When a DOI is present in
more than one source file, the record from a cleaner-author source (Citation Chaser
/ grey lit) is preferred over the academic-search export (whose first author is
split across two A1 lines). Authors are emitted verbatim from the source ("as-is",
per the project decision) — no attempt is made to un-split the academic-search
first-author defect.

Usage:
    python projects/strongminds/scripts/export_ris_enriched.py \
        --includes projects/strongminds/data/output/includes_fts_final_2670.ris \
        --source-dir projects/strongminds/strongminds_ris \
        --out projects/strongminds/data/output/includes_fts_final_2670_enriched.ris
"""
from __future__ import annotations
import argparse, glob, re, sys
from collections import defaultdict, Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TAG_RE = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")

TYPE_MAP = {
    "JOUR": "JOUR", "JOURNAL": "JOUR", "ARTICLE": "JOUR", "EJOUR": "JOUR",
    "BOOK": "BOOK", "CHAP": "CHAP", "CHAPTER": "CHAP",
    "THES": "THES", "THESIS": "THES", "CONF": "CONF", "CPAPER": "CONF",
    "ELEC": "ELEC", "GEN": "GEN", "RPRT": "RPRT", "REPORT": "RPRT",
}


def parse_ris(path: str):
    """Yield dicts (tag -> list of values); handles continuation lines."""
    recs, cur, last = [], defaultdict(list), None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").split("\n"):
        line = raw.rstrip("\r")
        m = TAG_RE.match(line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "ER":
                if cur:
                    recs.append(dict(cur))
                    cur, last = defaultdict(list), None
            else:
                cur[tag].append(val); last = tag
        elif line.strip() and last:
            cur[last][-1] = (cur[last][-1] + " " + line.strip()).strip()
    if cur:
        recs.append(dict(cur))
    return recs


def ndoi(s: str) -> str:
    s = (s or "").lower().strip()
    for p in ("https://doi.org/", "http://doi.org/",
              "https://dx.doi.org/", "http://dx.doi.org/"):
        s = s.replace(p, "")
    return s.strip().rstrip(".")


def first(r: dict, *tags: str) -> str:
    for t in tags:
        if r.get(t) and r[t][0].strip():
            return r[t][0].strip()
    return ""


def src_kind(fname: str) -> str:
    if "Academic" in fname:
        return "academic"       # first-author split defect
    if "Citation" in fname:
        return "citation"       # clean authors
    return "greylit"            # clean authors


# lower rank = preferred when the same DOI appears in several files
SRC_RANK = {"citation": 0, "greylit": 1, "academic": 2}


def better(new: dict, old: dict) -> bool:
    """Prefer a source record with authors, then a cleaner-author source file."""
    if bool(new.get("A1")) != bool(old.get("A1")):
        return bool(new.get("A1"))
    return SRC_RANK.get(new.get("_src"), 9) < SRC_RANK.get(old.get("_src"), 9)


def build_index(source_dir: str):
    by_id, by_doi = {}, {}
    for f in sorted(glob.glob(str(Path(source_dir) / "*.txt"))):
        kind = src_kind(Path(f).name)
        for r in parse_ris(f):
            r["_src"] = kind
            u1 = first(r, "U1")
            do = ndoi(first(r, "DO"))
            if u1 and (u1 not in by_id or better(r, by_id[u1])):
                by_id[u1] = r
            if do and (do not in by_doi or better(r, by_doi[do])):
                by_doi[do] = r
    return by_id, by_doi


def match(inc: dict, by_id: dict, by_doi: dict):
    """Best source record for an include: id match, upgraded to a cleaner DOI match."""
    uid, do = first(inc, "U1"), ndoi(first(inc, "DO"))
    cand = by_id.get(uid)
    dmatch = by_doi.get(do) if do else None
    if dmatch is not None and (cand is None or better(dmatch, cand)):
        cand = dmatch
    return cand


def write_record(fh, inc: dict, src: dict | None):
    """Write one enriched RIS record. Falls back to the bare include if unmatched."""
    r = src or inc
    ty = TYPE_MAP.get(first(r, "TY").upper(), "JOUR")
    fh.write(f"TY  - {ty}\n")
    # authors — verbatim from source A1 (as-is)
    for au in (r.get("A1") or []):
        au = au.strip()
        if au:
            fh.write(f"AU  - {au}\n")
    title = first(r, "T1", "TI") or first(inc, "T1", "TI")
    if title:
        fh.write(f"TI  - {title}\n")
    year = first(r, "PY", "Y1", "DA") or first(inc, "PY")
    ym = re.search(r"\d{4}", year)
    if ym:
        fh.write(f"PY  - {ym.group(0)}\n")
    journal = first(r, "JF", "JO", "T2", "JA")
    if journal:
        fh.write(f"T2  - {journal}\n")
    for tag in ("VL", "IS", "SP", "EP", "SN"):
        v = first(r, tag)
        if v:
            fh.write(f"{tag}  - {v}\n")
    ab = first(r, "AB", "N2")
    if ab and ab != "NA":
        fh.write(f"AB  - {ab}\n")
    for kw in (r.get("KW") or []):
        kw = kw.strip()
        if kw and kw.lower() != "eppi-reviewer":
            fh.write(f"KW  - {kw}\n")
    doi = ndoi(first(r, "DO") or first(inc, "DO"))
    if doi:
        fh.write(f"DO  - {doi}\n")
    url = first(r, "UR")
    if url:
        fh.write(f"UR  - {url}\n")
    uid = first(inc, "U1") or first(r, "U1")
    if uid:
        fh.write(f"U1  - {uid}\n")
    fh.write("ER  - \n\n")


def main():
    ap = argparse.ArgumentParser(description="Enrich bare FTS-includes RIS from the source corpus.")
    ap.add_argument("--includes", required=True, help="Bare includes RIS to enrich")
    ap.add_argument("--source-dir", required=True, help="Directory of source RIS *.txt files")
    ap.add_argument("--out", required=True, help="Output enriched RIS path")
    a = ap.parse_args()

    print("Indexing source corpus...", file=sys.stderr)
    by_id, by_doi = build_index(a.source_dir)
    print(f"  {len(by_id):,} by id, {len(by_doi):,} by doi", file=sys.stderr)

    includes = parse_ris(a.includes)
    print(f"  {len(includes):,} includes to enrich", file=sys.stderr)

    stats = Counter()
    with open(a.out, "w", encoding="utf-8") as fh:
        for inc in includes:
            src = match(inc, by_id, by_doi)
            if src is None:
                stats["unmatched"] += 1
            else:
                stats[f"src_{src['_src']}"] += 1
                if src.get("A1"):
                    stats["with_authors"] += 1
                if first(src, "VL"):
                    stats["with_volume"] += 1
                if first(src, "JF", "JO", "T2"):
                    stats["with_journal"] += 1
            write_record(fh, inc, src)

    print(f"\nWrote {len(includes):,} records -> {a.out}")
    for k in ("src_academic", "src_citation", "src_greylit", "unmatched",
              "with_authors", "with_journal", "with_volume"):
        print(f"  {k:<14} {stats.get(k, 0):>6,}")


if __name__ == "__main__":
    main()
