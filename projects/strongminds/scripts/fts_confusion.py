"""fts_confusion.py — Reconcile the human FTS ground truth against the LLM pipeline.

The human GT ("SM_FTS_Coding") is keyed by EPPI id; the LLM full-text screening is keyed
by Zotero key. Bridge: GT.eppi == ris_records.record_id -> DOI/title -> records_fts
(zotero) -> FTS prediction. We also look up the RIS (title/abstract) LLM decision directly
(keyed by EPPI), because many human-included records never reached full text — they were
dropped at the RIS/TA stage.

Outputs a per-record reconciliation CSV + prints the RIS-stage and FTS-stage matrices.

Usage:
    python projects/strongminds/scripts/fts_confusion.py \
        --gt projects/strongminds/data/gt_fts_71.csv \
        --out projects/strongminds/data/output/fts_gt_reconciliation.csv
"""
from __future__ import annotations
import argparse, csv, json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path("projects/strongminds")


def ndoi(d):
    d = (d or "").lower().strip()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d).strip()


def ntitle(t):
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return re.sub(r"[^a-z0-9]", "", t)


def load_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data/gt_fts_71.csv"))
    ap.add_argument("--out", default=str(ROOT / "data/output/fts_gt_reconciliation.csv"))
    a = ap.parse_args()

    gt = list(csv.DictReader(open(a.gt, encoding="utf-8-sig")))
    ris = {str(r["record_id"]): r for r in load_jsonl(ROOT / "data/ris_records.jsonl")}
    ris_res = {str(r["record_id"]): r for r in load_jsonl(ROOT / "data/output/results_ris_v19_tiebreak.jsonl")}
    fts = load_jsonl(ROOT / "data/fts/records_fts_2721.jsonl")
    fts_bydoi = {ndoi(r["doi"]): r["record_id"] for r in fts if ndoi(r.get("doi"))}
    fts_bytitle = {ntitle(r["title"]): r["record_id"] for r in fts}
    pred = {str(r["record_id"]): r for r in load_jsonl(ROOT / "data/output/results_fts_v19_tiebreak.jsonl")}
    # Recovered-set FTS results are keyed directly by EPPI (records came from ris_records),
    # so they join to the GT without the DOI/title bridge. Optional (may not exist yet).
    _rec_path = ROOT / "data/output/results_fts_recovered_tiebreak.jsonl"
    rec_fts = {str(r["record_id"]): r["screening_decision"]
               for r in load_jsonl(_rec_path)} if _rec_path.exists() else {}

    rows = []
    for g in gt:
        e = g["eppi_id"]; human = g["human_decision"]
        rr = ris.get(e, {})
        rres = ris_res.get(e, {})
        ris_dec = rres.get("screening_decision", "")
        ris_code = rres.get("screening_code", "")
        # FTS decision: prefer the recovered set (direct EPPI join); else bridge to the
        # original FTS run via DOI/title -> zotero key.
        zk = None
        fts_src = ""
        if e in rec_fts:
            fts_dec = rec_fts[e]; fts_src = "recovered"
        else:
            d = ndoi(rr.get("doi"))
            if d and d in fts_bydoi:
                zk = fts_bydoi[d]
            elif ntitle(rr.get("title")) in fts_bytitle:
                zk = fts_bytitle[ntitle(rr.get("title"))]
            fts_dec = pred.get(zk, {}).get("screening_decision") if zk else ""
            if fts_dec:
                fts_src = "original"
        if ris_dec == "EXCLUDE":
            disp = "EXCLUDE@RIS"
        elif fts_dec == "EXCLUDE":
            disp = "EXCLUDE@FTS"
        elif fts_dec == "INCLUDE":
            disp = "INCLUDE@FTS"
        elif ris_dec == "INCLUDE":
            disp = "INCLUDE@RIS_noFTS"
        else:
            disp = "NOT_IN_RIS"
        rows.append({"eppi": e, "short_title": g["short_title"], "human": human,
                     "ris_decision": ris_dec, "ris_code": ris_code,
                     "zotero_key": zk or "", "fts_decision": fts_dec or "",
                     "fts_source": fts_src, "disposition": disp, "note": g.get("note", "")})

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    def matrix(pairs, label):
        c = Counter(pairs)
        TP, FN = c[("INCLUDE", "INCLUDE")], c[("INCLUDE", "EXCLUDE")]
        FP, TN = c[("EXCLUDE", "INCLUDE")], c[("EXCLUDE", "EXCLUDE")]
        n = TP + FN + FP + TN
        if not n:
            print(f"\n{label}: no records"); return
        sens = TP / (TP + FN) if TP + FN else 0
        spec = TN / (TN + FP) if TN + FP else 0
        po = (TP + TN) / n
        pe = ((TP + FN) * (TP + FP) + (FP + TN) * (FN + TN)) / (n * n)
        kap = (po - pe) / (1 - pe) if (1 - pe) else 0
        print(f"\n{label} (n={n}, INCLUDE=positive)")
        print(f"                LLM INC  LLM EXC")
        print(f"  Human INC       {TP:>4}     {FN:>4}")
        print(f"  Human EXC       {FP:>4}     {TN:>4}")
        print(f"  sensitivity={sens:.3f}  specificity={spec:.3f}  accuracy={po:.3f}  kappa={kap:.3f}")

    matrix([(r["human"], r["ris_decision"]) for r in rows if r["ris_decision"] in ("INCLUDE", "EXCLUDE")],
           "RIS / title-abstract stage")
    matrix([(r["human"], r["fts_decision"]) for r in rows if r["fts_decision"] in ("INCLUDE", "EXCLUDE")],
           "FTS / full-text stage (only records that reached it)")

    print("\nDisposition of the", len(rows), "human-coded records:")
    for k, v in Counter(r["disposition"] for r in rows).most_common():
        print(f"  {v:>3}  {k}")
    print("\nRIS exclusion codes among human-INCLUDE records dropped at RIS:")
    for k, v in Counter(r["ris_code"] for r in rows if r["human"] == "INCLUDE" and r["ris_decision"] == "EXCLUDE").most_common():
        print(f"  {v:>3}  {k}")
    print(f"\nPer-record reconciliation -> {a.out}")


if __name__ == "__main__":
    main()
