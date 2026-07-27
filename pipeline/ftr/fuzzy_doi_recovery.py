"""Fuzzy DOI recovery for no-DOI records using OpenAlex title search (more forgiving than CrossRef)."""
import csv, json, sys, time, requests
from pathlib import Path
from datetime import datetime

INV_PATH = r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv'
USER_AGENT = "lsempe@3ieimpact.org"

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()

def openalex_doi(title, year=""):
    """Search OpenAlex by title, return (doi, confidence) or (None, 0)."""
    try:
        r = requests.get("https://api.openalex.org/works",
                         params={"filter": f"title.search:{title}", "per_page": 3},
                         headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return None, 0

    title_lower = title.lower().strip()
    best_doi = None
    best_conf = 0
    for w in results:
        wt = (w.get("title") or "").lower().strip()
        if not wt:
            continue
        # Simple word overlap similarity
        tw = set(title_lower.split())
        ww = set(wt.split())
        if not tw:
            continue
        overlap = len(tw & ww) / len(tw)
        # Also check year match
        wy = str(w.get("publication_year", ""))
        if year and wy == year:
            overlap += 0.1
        # Check if one contains the other
        if title_lower in wt or wt in title_lower:
            overlap = max(overlap, 0.95)
        if overlap > best_conf:
            best_conf = overlap
            raw_doi = w.get("doi", "") or ""
            best_doi = raw_doi.replace("https://doi.org/", "").replace("https://dx.doi.org/", "").lower()
    return best_doi, int(best_conf * 100)

def main():
    rows = list(csv.DictReader(open(INV_PATH, encoding='utf-8-sig')))
    fieldnames = list(rows[0].keys())

    no_doi = [(i, r) for i, r in enumerate(rows) if not r.get('doi', '').strip() or r.get('doi', '').strip() == 'na']
    log(f"No-DOI records to search: {len(no_doi)}")

    found = 0
    for n, (idx, r) in enumerate(no_doi, start=1):
        title = r.get('title', '').strip()
        year = r.get('year', '').strip()
        if not title or title == 'NA':
            continue

        doi, conf = openalex_doi(title, year)
        if doi and conf >= 60:  # Lower threshold than CrossRef (82)
            old = r.get('doi', '')
            rows[idx]['doi'] = doi
            if not rows[idx].get('doi_source', ''):
                rows[idx]['doi_source'] = f'openalex_fuzzy'
            found += 1
            log(f"[{n}/{len(no_doi)}] {r['zotero_key']}: DOI {doi} (conf {conf})")
        else:
            if n % 50 == 0:
                log(f"[{n}/{len(no_doi)}] ... ({found} found so far)")

        # Checkpoint every 25
        if n % 25 == 0:
            with open(INV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)

        time.sleep(0.3)

    with open(INV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    log(f"Done. Found {found} DOIs via OpenAlex fuzzy search (threshold 60).")
    log(f"Remaining without DOI: {len(no_doi) - found}")

if __name__ == '__main__':
    main()
