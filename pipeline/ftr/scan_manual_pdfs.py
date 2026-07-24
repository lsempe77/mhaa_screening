"""
Scan the pdfs/ folder for manually-downloaded files (saved with the exact
{zotero_key}_*.pdf name from the worklist) and mark them in the inventory so
step3_attach_to_zotero.py can upload them.

Run:
    python scan_manual_pdfs.py inventory_{timestamp}.csv
    python step3_attach_to_zotero.py inventory_{timestamp}.csv
"""

import sys

import pandas as pd

import config
from step2_fetch_missing_pdfs import target_filename


def is_pdf(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5).startswith(b"%PDF")
    except Exception:
        return False


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scan_manual_pdfs.py <inventory_csv>")
    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    todo = df[(~has_pdf) & (df["pdf_path"].str.strip() == "") & (df["doi"].str.strip() != "")]

    found = 0
    for idx, row in todo.iterrows():
        # exact expected name, plus any {zotero_key}_*.pdf the user saved
        candidates = [config.PDF_DIR / target_filename(row["zotero_key"], row["doi"].strip(), row["title"])]
        candidates += sorted(config.PDF_DIR.glob(f"{row['zotero_key']}_*.pdf"))
        for c in candidates:
            if c.exists() and c.stat().st_size > 1000 and is_pdf(c):
                df.at[idx, "pdf_path"] = str(c.relative_to(config.ROOT))
                df.at[idx, "pdf_source"] = "manual_oxford"
                found += 1
                print(f"  matched {row['zotero_key']} -> {c.name}")
                break

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nNewly matched manual downloads: {found}")
    print(f"Now run: python step3_attach_to_zotero.py {csv_path.name}")


if __name__ == "__main__":
    main()
