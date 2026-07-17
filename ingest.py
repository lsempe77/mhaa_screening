"""
ingest.py — Convert a CSV/Excel screening dataset into the two files
k5_runner.py consumes:

    records_<n>.jsonl   one JSON object per record with fields:
                          record_id, year, title, abstract
    gt_<n>.json        { record_id: "EPPI TAS decision label string" }

Usage:
    python ingest.py --input ground_truth.xlsx --out-dir data
    python ingest.py --input records.csv  --out-dir data \
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
    """Read CSV or Excel; header_row is 0-indexed position of the header line."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p, header=header_row, engine="openpyxl")
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, header=header_row)
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


def main():
    p = argparse.ArgumentParser(description="Ingest CSV/Excel → records.jsonl + gt.json for k5_runner.")
    p.add_argument("--input", required=True, help="Path to CSV or Excel file.")
    p.add_argument("--out-dir", default="data", help="Output directory.")
    p.add_argument("--header-row", type=int, default=1,
                   help="0-indexed header row position (default 1 for ground_truth.xlsx).")
    p.add_argument("--col-id", default="EPPI ID", help="Column for record_id.")
    p.add_argument("--col-year", default="PY", help="Column for publication year.")
    p.add_argument("--col-title", default="T1", help="Column for title.")
    p.add_argument("--col-abstract", default="AB", help="Column for abstract.")
    p.add_argument("--col-decision", default="EPPI TAS decision",
                   help="Ground-truth label column (set to '' to skip gt.json).")
    args = p.parse_args()

    df = load_dataframe(args.input, args.header_row)
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns: {list(df.columns)}")

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
