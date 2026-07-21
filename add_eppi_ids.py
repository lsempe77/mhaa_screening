"""Build zotero_key -> EPPI_ID_correct mapping and add eppi_id to all output CSVs.

Joins the Zotero references CSV with the EPPI ID mapping CSV on DOI (primary),
URL (fallback), title (last resort). Then adds an `eppi_id` column to:
  - summary.csv
  - excludes_triage.csv
  - flags_triage.csv

Also writes a standalone lookup: GE_FTS/data/zotero_to_eppi.csv

Usage:
    python add_eppi_ids.py \
        --refs GE_FTS/references_20260718_204803.csv \
        --mapping "C:/Users/LucasSempe/Downloads/Copy of EPPI_ID_mapping.csv" \
        --reports-dir GE_FTS/reports
"""
from __future__ import annotations
import argparse, csv, json, re, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def norm_doi(doi: str) -> str:
    """Normalize a DOI for matching: strip URL prefixes, lowercase, strip whitespace."""
    if not doi:
        return ""
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:", "", d)
    return d.strip()


def norm_url(url: str) -> str:
    """Normalize a URL for matching: strip trailing slashes, lowercase."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"/+$", "", u)
    return u


def norm_title(title: str) -> str:
    """Normalize a title for fuzzy matching: lowercase, collapse whitespace, strip punctuation."""
    if not title:
        return ""
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)  # strip punctuation
    return t.strip()


def build_lookup(mapping_rows: list[dict]) -> dict[str, dict[str, str]]:
    """Build lookup dicts: doi -> {eppi_id_correct, eppi_id_zotero, title}, same for url, title."""
    by_doi: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    by_title: dict[str, dict] = {}

    for row in mapping_rows:
        eppi_correct = (row.get("EPPI_ID_correct") or "").strip()
        eppi_zotero = (row.get("EPPI tagged in Zotero") or "").strip()
        title = row.get("title", "")
        entry = {
            "eppi_id_correct": eppi_correct,
            "eppi_id_zotero": eppi_zotero,
            "title": title,
        }
        doi_n = norm_doi(row.get("doi", ""))
        if doi_n:
            by_doi[doi_n] = entry
        url_n = norm_url(row.get("url", ""))
        if url_n:
            by_url[url_n] = entry
        title_n = norm_title(title)
        if title_n:
            by_title[title_n] = entry

    return {"doi": by_doi, "url": by_url, "title": by_title}


def lookup_eppi(ref_row: dict, lookup: dict) -> tuple[str, str]:
    """Return (eppi_id_correct, match_method) for a reference row."""
    # Try DOI first
    doi_n = norm_doi(ref_row.get("doi", ""))
    if doi_n and doi_n in lookup["doi"]:
        return lookup["doi"][doi_n]["eppi_id_correct"], "DOI"
    # Try URL
    url_n = norm_url(ref_row.get("url", ""))
    if url_n and url_n in lookup["url"]:
        return lookup["url"][url_n]["eppi_id_correct"], "URL"
    # Try title
    title_n = norm_title(ref_row.get("title", ""))
    if title_n and title_n in lookup["title"]:
        return lookup["title"][title_n]["eppi_id_correct"], "title"
    return "", "not_found"


def add_eppi_to_csv(csv_path: Path, zotero_to_eppi: dict[str, str]) -> int:
    """Add an eppi_id column to a CSV, inserting it right after record_id.
    Returns the number of rows that got an eppi_id."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    if "eppi_id" not in fieldnames:
        # Insert after record_id
        idx = fieldnames.index("record_id") + 1 if "record_id" in fieldnames else len(fieldnames)
        fieldnames.insert(idx, "eppi_id")

    matched = 0
    for row in rows:
        rid = row.get("record_id", "")
        eppi = zotero_to_eppi.get(rid, "")
        row["eppi_id"] = eppi
        if eppi:
            matched += 1

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return matched


def main():
    p = argparse.ArgumentParser(description="Add correct EPPI IDs to screening output CSVs.")
    p.add_argument("--refs", required=True, help="Zotero references CSV")
    p.add_argument("--mapping", required=True, help="EPPI ID mapping CSV")
    p.add_argument("--reports-dir", default="GE_FTS/reports", help="Directory with summary/triage CSVs")
    args = p.parse_args()

    # Load mapping CSV
    with open(args.mapping, "r", encoding="utf-8-sig", newline="") as f:
        mapping_rows = list(csv.DictReader(f))
    print(f"Loaded {len(mapping_rows)} mapping rows")

    # Load references CSV
    with open(args.refs, "r", encoding="utf-8-sig", newline="") as f:
        ref_rows = list(csv.DictReader(f))
    print(f"Loaded {len(ref_rows)} reference rows")

    # Build lookup
    lookup = build_lookup(mapping_rows)

    # Match each reference to its correct EPPI ID
    zotero_to_eppi: dict[str, str] = {}
    method_counts: dict[str, int] = {}
    unmatched: list[str] = []

    for ref in ref_rows:
        zotero_key = ref["zotero_key"]
        eppi, method = lookup_eppi(ref, lookup)
        if eppi:
            zotero_to_eppi[zotero_key] = eppi
            method_counts[method] = method_counts.get(method, 0) + 1
        else:
            unmatched.append(f"  {zotero_key} | {ref.get('title', '')[:60]}")

    print(f"\n=== Match results ===")
    print(f"Matched: {len(zotero_to_eppi)} / {len(ref_rows)}")
    for method, count in sorted(method_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {method}: {count}")
    if unmatched:
        print(f"Unmatched ({len(unmatched)}):")
        for u in unmatched:
            print(u)

    # Write standalone lookup
    lookup_path = Path("GE_FTS/data/zotero_to_eppi.csv")
    lookup_path.parent.mkdir(parents=True, exist_ok=True)
    with lookup_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["zotero_key", "eppi_id"])
        writer.writeheader()
        for ref in ref_rows:
            zk = ref["zotero_key"]
            writer.writerow({"zotero_key": zk, "eppi_id": zotero_to_eppi.get(zk, "")})
    print(f"\nWrote lookup -> {lookup_path}")

    # Add eppi_id to output CSVs
    reports = Path(args.reports_dir)
    csvs = ["summary.csv", "excludes_triage.csv", "flags_triage.csv"]
    for name in csvs:
        path = reports / name
        if not path.exists():
            print(f"  (skipped: {name} not found)")
            continue
        matched = add_eppi_to_csv(path, zotero_to_eppi)
        total = sum(1 for _ in open(path, encoding="utf-8-sig")) - 1
        print(f"  {name}: {matched}/{total} rows got eppi_id")

    print("\nDone. The `eppi_id` column now holds the correct EPPI ID; `record_id` still holds the Zotero key.")


if __name__ == "__main__":
    main()
