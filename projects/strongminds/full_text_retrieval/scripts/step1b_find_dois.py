"""
Step 1b: Find missing DOIs for references that have none, via CrossRef.

Many Zotero items (grey literature, book chapters, some journal records) have
no DOI, so step 2 cannot fetch a PDF for them. This step searches CrossRef by
title + first author and, when a confident match is found, writes the DOI back
into the inventory so step 2 can pick it up on the next run.

A match is accepted only when the CrossRef candidate's title is highly similar
to the reference title (guards against false positives), optionally corroborated
by the publication year.

New/updated columns:
  doi              filled in when a confident match is found
  doi_source       "crossref" when this step supplied the DOI, else unchanged
  doi_confidence   0-100 (title similarity, lightly adjusted for year match)
  doi_match_title  the CrossRef title that matched (for auditing)

Run (AFTER step 1, and when step 2 is not running):
    python step1b_find_dois.py inventory_{timestamp}.csv
    python step1b_find_dois.py inventory_{timestamp}.csv --min-confidence 85
"""

import re
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher

import pandas as pd
import requests

import config

DEFAULT_MIN_CONFIDENCE = 82  # title-similarity threshold to accept a DOI
CHECKPOINT_EVERY = 25


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def normalise_title(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"<[^>]+>", " ", text)          # strip any HTML tags
    text = re.sub(r"[^a-z0-9 ]+", " ", text)       # keep alphanumerics
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = normalise_title(a), normalise_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def first_author(authors: str) -> str:
    if not authors:
        return ""
    first = authors.split(";")[0].strip()
    # "Last, First" -> "Last"
    return first.split(",")[0].strip()


def crossref_year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            try:
                return int(parts[0][0])
            except (ValueError, TypeError):
                return None
    return None


def search_crossref(title: str, authors: str, year: str) -> tuple[str, int, str]:
    """Return (doi, confidence, matched_title) or ("", 0, "")."""
    if not title or not title.strip():
        return "", 0, ""

    params = {
        "query.bibliographic": title.strip(),
        "rows": 5,
        "select": "DOI,title,author,published-print,published-online,issued,created,score",
    }
    author = first_author(authors)
    if author:
        params["query.author"] = author

    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params=params,
            headers={"User-Agent": f"GE-ftr DOI finder (mailto:{config.USER_EMAIL})"},
            timeout=20,
        )
        if r.status_code != 200:
            return "", 0, ""
        items = r.json().get("message", {}).get("items", [])
    except Exception:
        return "", 0, ""

    best_doi, best_conf, best_title = "", 0, ""
    for item in items:
        cand_title = " ".join(item.get("title") or [])
        sim = title_similarity(title, cand_title)
        conf = int(round(sim * 100))

        # Year corroboration (small +/- adjustment)
        if year and str(year).isdigit():
            cy = crossref_year(item)
            if cy is not None:
                diff = abs(int(year) - cy)
                if diff == 0:
                    conf = min(100, conf + 5)
                elif diff > 2:
                    conf = max(0, conf - 15)

        if conf > best_conf and item.get("DOI"):
            best_doi, best_conf, best_title = item["DOI"].strip(), conf, cand_title

    return best_doi, best_conf, best_title


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python step1b_find_dois.py <inventory_csv> [--min-confidence N]")

    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    min_conf = DEFAULT_MIN_CONFIDENCE
    if "--min-confidence" in sys.argv:
        min_conf = int(sys.argv[sys.argv.index("--min-confidence") + 1])

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("doi_source", "doi_confidence", "doi_match_title"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    todo = df[(df["doi"].str.strip() == "") & (~has_pdf)].copy()
    log(f"References missing a DOI to search: {len(todo)}  (min confidence {min_conf})")

    found = 0
    for n, (idx, row) in enumerate(todo.iterrows(), start=1):
        doi, conf, matched = search_crossref(row["title"], row["authors"], row["year"])
        status = "no match"
        if doi and conf >= min_conf:
            df.at[idx, "doi"] = doi
            df.at[idx, "doi_source"] = "crossref"
            df.at[idx, "doi_confidence"] = str(conf)
            df.at[idx, "doi_match_title"] = matched
            found += 1
            status = f"DOI {doi} (conf {conf})"
        elif doi:
            # record the near-miss for auditing but do NOT set the DOI
            df.at[idx, "doi_confidence"] = str(conf)
            df.at[idx, "doi_match_title"] = matched
            status = f"low conf {conf} -> skip"

        log(f"[{n}/{len(todo)}] {row['zotero_key']}: {status}")

        if n % CHECKPOINT_EVERY == 0:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"  ...checkpoint saved ({found} DOIs found so far)")
        time.sleep(0.5)  # CrossRef polite rate

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"DOIs found and written: {found}/{len(todo)}")
    log(f"Updated inventory:      {csv_path}")
    log("Next: re-run step 2 to fetch PDFs for the newly-identified DOIs:")
    log(f"    python step2_fetch_missing_pdfs.py {csv_path.name}")


if __name__ == "__main__":
    main()
