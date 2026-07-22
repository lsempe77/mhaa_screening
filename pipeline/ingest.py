"""
ingest.py — Convert a CSV/Excel screening dataset into the two files
k5_runner.py consumes:

    records_<n>.jsonl   one JSON object per record with fields:
                          record_id, year, title, abstract
    gt_<n>.json        { record_id: "EPPI TAS decision label string" }

Usage:
    python pipeline/ingest.py --input ground_truth.xlsx --out-dir projects/girl_effect/ta_screening/data
    python pipeline/ingest.py --input records.csv  --out-dir projects/girl_effect/ta_screening/data \
        --col-id "EPPI ID" --col-year PY --col-title T1 --col-abstract AB \
        --col-decision "EPPI TAS decision" --header-row 1

Defaults match the MHAA ground_truth.xlsx layout
(header in row index 1, columns T1/A1/PY/AB/UR/EPPI ID/EPPI TAS decision).
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import pandas as pd


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _clean(v, default: str = "NA") -> str:
    if v is None or _is_nan(v):
        return default
    s = str(v).strip()
    return s if s else default


def load_dataframe(path: str, header_row: int) -> pd.DataFrame:
    """Read CSV or Excel as all-string, so numeric IDs/years don't get a '.0' suffix
    and unicode stays intact. header_row is 0-indexed position of the header line."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p, header=header_row, engine="openpyxl", dtype=str)
    if p.suffix.lower() == ".csv":
        # encoding="utf-8" handles the BOM correctly; dtype=str prevents float coercion.
        return pd.read_csv(p, header=header_row, dtype=str, encoding="utf-8")
    raise ValueError(f"Unsupported file type: {p.suffix}")


def build_outputs(
    df: pd.DataFrame,
    out_dir: str,
    col_id: str,
    col_year: str,
    col_title: str,
    col_abstract: str,
    col_decision: str | None,
) -> tuple[Path, Path | None]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = len(df)

    # records.jsonl
    records_path = out / f"records_{n}.jsonl"
    with records_path.open("w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            rid = _clean(row[col_id])
            rec = {
                "record_id": rid,
                "year": _clean(row.get(col_year)),
                "title": _clean(row.get(col_title), default=""),
                "abstract": _clean(row.get(col_abstract)),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # gt.json  (only if a decision column is supplied and exists)
    gt_path: Path | None = None
    if col_decision and col_decision in df.columns:
        gt_path = out / f"gt_{n}.json"
        gt: dict[str, str] = {}
        for _, row in df.iterrows():
            rid = _clean(row[col_id])
            gt[rid] = _clean(row[col_decision])
        gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")

    return records_path, gt_path


def build_outputs_paired(
    df: pd.DataFrame,
    out_dir: str,
    col_id: str = "Item ID",
    col_year: str = "Year",
    col_title: str = "Title",
    col_abstract: str = "Abstract",
    col_decision: str = "TAS Human Coding",
) -> tuple[Path, Path]:
    """Build records.jsonl + gt.json from the StrongMinds paired-row CSV layout.

    The StrongMinds groundtruth.csv stores each record on one row (with Item ID,
    Title, Year, Abstract populated) and the TAS decision on the *following* row,
    which is blank except for the decision value in `col_decision`. This function
    walks the dataframe, and for each row where `col_id` is non-null it pairs the
    next row's decision value.

    Also writes `screening_level: "review"` into each record so the ULCM prompt
    gets the mandatory metadata field without the caller having to pass it.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    id_rows = df[df[col_id].notna()].index.tolist()
    records: list[dict] = []
    gt: dict[str, str] = {}

    for i in id_rows:
        rid = _clean(df[col_id].iloc[i])
        # Decision lives on the next row (its Item ID is NaN).
        decision = "NA"
        if i + 1 < len(df) and pd.isna(df[col_id].iloc[i + 1]):
            decision = _clean(df[col_decision].iloc[i + 1])
        records.append({
            "record_id": rid,
            "year": _clean(df[col_year].iloc[i]),
            "title": _clean(df[col_title].iloc[i], default=""),
            "abstract": _clean(df[col_abstract].iloc[i]),
            "screening_level": "review",
        })
        gt[rid] = decision

    n = len(records)
    records_path = out / f"records_{n}.jsonl"
    with records_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    gt_path = out / f"gt_{n}.json"
    gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")

    return records_path, gt_path


def main():
    p = argparse.ArgumentParser(description="Ingest CSV/Excel → records.jsonl + gt.json for k5_runner.")
    p.add_argument("--input", required=True, help="Path to CSV or Excel file.")
    p.add_argument("--out-dir", default="data", help="Output directory.")
    p.add_argument("--format", default="auto",
                   choices=["auto", "mhaa", "strongminds_csv"],
                   help="Input format preset. 'strongminds_csv' uses the paired-row "
                        "StrongMinds groundtruth.csv layout (record + decision on the "
                        "next row). 'mhaa' is the original EPPI layout (one row per "
                        "record with a decision column). 'auto' keeps the old behaviour "
                        "(column flags below).")
    p.add_argument("--header-row", type=int, default=None,
                   help="0-indexed header row position. Defaults to 1 for 'mhaa'/'auto' "
                        "(ground_truth.xlsx has a title row above the header) and 0 for "
                        "'strongminds_csv' (header is the first row).")
    p.add_argument("--col-id", default="EPPI ID", help="Column for record_id.")
    p.add_argument("--col-year", default="PY", help="Column for publication year.")
    p.add_argument("--col-title", default="T1", help="Column for title.")
    p.add_argument("--col-abstract", default="AB", help="Column for abstract.")
    p.add_argument("--col-decision", default="EPPI TAS decision",
                   help="Ground-truth label column (set to '' to skip gt.json).")
    args = p.parse_args()

    if args.header_row is None:
        args.header_row = 0 if args.format == "strongminds_csv" else 1

    df = load_dataframe(args.input, args.header_row)
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns: {list(df.columns)}")

    if args.format == "strongminds_csv":
        # StrongMinds paired-row layout: hard-coded column names, decision on the next row.
        for c in ("Item ID", "Title", "Year", "Abstract", "TAS Human Coding"):
            if c not in df.columns:
                raise SystemExit(f"Column {c!r} not found in {list(df.columns)} "
                                 f"(required for --format strongminds_csv)")
        records_path, gt_path = build_outputs_paired(df, args.out_dir)
        print(f"\nWrote {records_path}")
        print(f"Wrote {gt_path}")
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        from collections import Counter
        dist = Counter(gt.values())
        print("\nGround-truth label distribution:")
        for label, cnt in sorted(dist.items(), key=lambda kv: -kv[1]):
            print(f"  {cnt:>4}  {label}")
        return

    # Original MHAA / auto path
    for c in (args.col_id, args.col_year, args.col_title, args.col_abstract):
        if c not in df.columns:
            raise SystemExit(f"Column {c!r} not found in {list(df.columns)}")

    records_path, gt_path = build_outputs(
        df, args.out_dir,
        col_id=args.col_id, col_year=args.col_year,
        col_title=args.col_title, col_abstract=args.col_abstract,
        col_decision=args.col_decision or None,
    )
    print(f"\nWrote {records_path}")
    if gt_path:
        print(f"Wrote {gt_path}")
        # quick label distribution
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        from collections import Counter
        dist = Counter(gt.values())
        print("\nGround-truth label distribution:")
        for label, cnt in sorted(dist.items(), key=lambda kv: -kv[1]):
            print(f"  {cnt:>4}  {label}")


if __name__ == "__main__":
    main()
