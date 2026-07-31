"""dex_wide_by_rq.py — Wide (all-fields-as-columns) extraction table + per-RQ workbook.

The other exports are long/tidy (one row per record x field). This builds the
*wide* view reviewers usually want:

  dex_wide.csv     one row per record, one column per extracted field (~305 cols),
                   plus record metadata (title/year/unit) and rq_tags.
  dex_by_rq.xlsx   an "Index" sheet, an "All fields" sheet (the full wide table),
                   and one sheet per RQ (RQ01..RQ18). Each RQ sheet holds the
                   records tagged with that question, and drops columns that are
                   empty across that subset — so each sheet auto-focuses on the
                   fields that question actually uses (driver_* for RQ1, dose_*
                   for RQ7, psychom_* for RQ18, ...), lead/identity columns first.

Reads the long + summary exports produced by dex_export.py.

Usage:
    python pipeline/extraction/dex_wide_by_rq.py \
        --long    projects/strongminds/data/extraction/reports/dex_long.csv \
        --summary projects/strongminds/data/extraction/reports/dex_summary.csv \
        --out-dir projects/strongminds/data/extraction/reports
"""
from __future__ import annotations
import argparse, ast, re, sys
from collections import defaultdict, Counter
from pathlib import Path
import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RQ_LABEL = {
    1: "Determinants of adult depression (LMIC)",
    2: "Which low-intensity group interventions are effective",
    3: "Parameters determining effectiveness",
    4: "Facilitator training / supervision / background",
    5: "Components associated with symptom reduction",
    6: "Components that can be dropped",
    7: "Minimum viable dose (length x sessions)",
    8: "Durability: short- vs longer-term effects",
    9: "When single- vs multi-session is appropriate",
    10: "Group-size ranges",
    11: "Spillover to general populations",
    12: "Stepped-care model design",
    13: "Non-specialist / lay / peer delivery",
    14: "Therapeutic model and active ingredients",
    15: "Design choices and engagement",
    16: "Cost drivers and cost-per-participant",
    17: "Safety monitoring and referral pathways",
    18: "Measurement tools: validity and reliability (LMIC)",
}

# columns pinned to the front of every sheet (identity / cross-cutting descriptors)
LEAD = ["record_id", "title", "year", "unit", "rq_tags", "doctype", "design",
        "geo_focus", "country", "comparator", "n_included_studies",
        "amstar2_band", "rob_fatal_flaw", "eff_metric", "eff_value",
        "eff_ci_lo", "eff_ci_hi", "eff_direction"]


def order_cols(cols):
    lead = [c for c in LEAD if c in cols]
    rest = sorted(c for c in cols if c not in lead)
    return lead + rest


def rq_fieldsets(contrib_path: str, min_freq: int = 3):
    """RQ -> the extraction fields the extractor actually used to answer it, taken
    from rq_contribution_data_fields (kept if used by >= min_freq contributions)."""
    fld = defaultdict(Counter)
    with open(contrib_path, encoding="utf-8-sig") as fh:
        import csv
        for r in csv.DictReader(fh):
            rq = r.get("rq_id", "")
            raw = r.get("rq_contribution_data_fields", "") or ""
            try:
                vals = ast.literal_eval(raw)
                vals = vals if isinstance(vals, list) else [vals]
            except Exception:
                vals = raw.split(",")
            for f in vals:
                f = str(f).strip(" []'\"")
                if f:
                    fld[rq][f] += 1
    return {rq: [f for f, c in cnt.most_common() if c >= min_freq]
            for rq, cnt in fld.items()}


def main():
    ap = argparse.ArgumentParser(description="Wide extraction table + per-RQ workbook.")
    ap.add_argument("--long", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--contrib", default="projects/strongminds/data/extraction/reports/dex_rq_contributions_long.csv",
                    help="rq_contributions long CSV, for per-RQ field selection")
    ap.add_argument("--min-field-freq", type=int, default=3)
    ap.add_argument("--out-dir", default="projects/strongminds/data/extraction/reports")
    a = ap.parse_args()

    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    # --- pivot long -> wide (record x field) ---
    long = pd.read_csv(a.long, dtype=str, keep_default_na=False)
    wide = (long.pivot_table(index="record_id", columns="field", values="value",
                             aggfunc="first")
                .reset_index())
    wide.columns.name = None

    # --- record metadata from summary (title/year/unit not in the long/field set).
    # rq_tags IS an extraction field, so it already came through the pivot — keep that
    # copy and avoid a merge-suffix collision by not re-importing it from summary. ---
    summ = pd.read_csv(a.summary, dtype=str, keep_default_na=False)
    metacols = [c for c in ("title", "year", "unit") if c in summ.columns]
    meta = summ[["record_id"] + metacols].drop_duplicates("record_id")
    wide = wide.drop(columns=[c for c in metacols if c in wide.columns])
    wide = meta.merge(wide, on="record_id", how="right").fillna("")

    wide = wide[order_cols(wide.columns)]
    # strip control chars (PDF-extraction artifacts) that openpyxl rejects and that
    # dirty the CSV; harmless to remove from the text values.
    for c in wide.columns:
        wide[c] = wide[c].map(
            lambda v: ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v)
    wide_path = out / "dex_wide.csv"
    wide.to_csv(wide_path, index=False, encoding="utf-8-sig")
    print(f"  {len(wide):,} records x {wide.shape[1]:,} columns -> {wide_path}")

    # --- per-RQ workbook ---
    def rqs_of(tags: str):
        return set(re.findall(r"RQ\d+", tags or ""))
    wide["_rqset"] = wide["rq_tags"].map(rqs_of)

    # per-RQ columns: lead/identity + the fields the extractor used to answer that RQ
    # (from rq_contribution_data_fields). Falls back to "non-empty across the subset"
    # for any RQ with no contribution-field record.
    fieldsets = rq_fieldsets(a.contrib, a.min_field_freq) if Path(a.contrib).exists() else {}

    rq_frames, index_rows = [], []
    for n in range(1, 19):
        rq = f"RQ{n}"
        sub = wide[wide["_rqset"].map(lambda s: rq in s)].drop(columns="_rqset")
        rqf = [c for c in fieldsets.get(rq, []) if c in sub.columns]
        if rqf:
            keep = [c for c in sub.columns if c in LEAD or c in rqf]
        else:  # fallback: keep lead + any column non-empty in this subset
            keep = [c for c in sub.columns
                    if c in LEAD or sub[c].astype(str).str.strip().ne("").any()]
        sub = sub[order_cols(keep)]
        rq_frames.append((f"RQ{n:02d}", sub))
        index_rows.append({"RQ": rq, "question": RQ_LABEL[n],
                           "records": len(sub), "fields_shown": sub.shape[1]})

    xlsx = out / "dex_by_rq.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        pd.DataFrame(index_rows)[["RQ", "question", "records", "fields_shown"]] \
            .to_excel(xw, sheet_name="Index", index=False)
        wide.drop(columns="_rqset").to_excel(xw, sheet_name="All fields", index=False)
        for name, sub in rq_frames:
            sub.to_excel(xw, sheet_name=name, index=False)

    print(f"  workbook -> {xlsx}")
    print(f"\n{'RQ':<5}{'records':>8}  question")
    for r in index_rows:
        print(f"{r['RQ']:<5}{r['records']:>8}  {r['question']}")


if __name__ == "__main__":
    main()
