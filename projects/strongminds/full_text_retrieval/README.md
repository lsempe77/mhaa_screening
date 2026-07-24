# StrongMinds ULCM — Full-Text Retrieval Pipeline

PDF retrieval and attachment for the 4,125 INCLUDEs from TAS screening of the
29,251-record RIS corpus. Adapted from the GE-ftr pipeline.

---

## Overview

```
includes.ris (4,125 records from TAS screening)
       ↓
Step 0: Build inventory from RIS → logs/inventory_{ts}.csv
       ↓
Step 1b: Recover missing DOIs via CrossRef (500 records without DOI)
       ↓
Step 2: Automated PDF fetch (DOI-based)
  2.  Unpaywall → OpenAlex → Semantic Scholar → publisher → Sci-Hub
  2b. No-DOI items (grey literature, WHO IRIS, gov.uk)
  2c. Elsevier ScienceDirect API (Oxford subscription)
  2d. Browser fetch (Playwright/Chrome for MDPI, BMC, Frontiers, PLOS)
       ↓
Step 3: Attach PDFs to Zotero (optional — requires RIS imported to Zotero first)
       ↓
Export: Local snapshot (CSV + all PDFs) for full-text screening
```

---

## Setup

```powershell
# Install dependencies
pip install -r requirements.txt
python -m playwright install chromium     # only for browser steps (2d/2e)

# Credentials: .env in this folder (git-ignored, already copied from GE-ftr)
# Contains ZOTERO_API_KEY, ELSEVIER_API_KEY, ELSEVIER_INSTTOKEN
```

---

## Running the pipeline

All scripts are in `scripts/` and are run from that directory:

```powershell
cd projects/strongminds/full_text_retrieval/scripts

# Step 0: Build inventory from the RIS file (4,125 INCLUDEs)
python step0_build_inventory.py

# Step 1b: Recover DOIs for the ~500 records without one (via CrossRef)
python step1b_find_dois.py inventory_{ts}.csv

# Step 2: Fetch PDFs (run the ones you need)
python step2_fetch_missing_pdfs.py inventory_{ts}.csv        # OA + publisher (+ Sci-Hub)
python step2b_no_doi.py            inventory_{ts}.csv         # grey literature (no DOI)
python step2c_elsevier.py          inventory_{ts}.csv         # Elsevier (needs .env creds)
python step2d_browser.py           inventory_{ts}.csv         # MDPI etc. (opens Chrome)

# Step 3: Attach to Zotero (optional — import RIS to Zotero first)
python step3_attach_to_zotero.py   inventory_{ts}.csv

# Helpers
python export_library.py                                     # local snapshot: CSV + all PDFs
python generate_worklist.py    inventory_{ts}.csv             # clickable worklist for manual download
python match_manual_pdfs.py    inventory_{ts}.csv             # match hand-downloaded PDFs by DOI
```

The inventory CSV is shared across all steps — each step reads it, fills in its
columns (`pdf_path`, `pdf_source`, `attach_status`), and writes it back. Steps 2
and 3 are **idempotent** — re-running skips work already done.

---

## Inventory columns

| Column | Meaning |
|--------|---------|
| `zotero_key` | EPPI ID (from RIS U1 field) — the reference↔PDF anchor |
| `doi` | DOI (step 1b may fill it via CrossRef if missing) |
| `pdf_path` | Local path once a PDF is fetched/matched |
| `pdf_source` | Which source provided it (publisher, elsevier_api, manual, etc.) |
| `has_pdf` | True once a PDF is attached |

---

## Key difference from GE-ftr

- **Starting point:** the RIS file of INCLUDEs (4,125 records), not a Zotero collection export.
  Step 0 builds the inventory from the RIS instead of calling the Zotero API.
- **Zotero keys:** the RIS uses EPPI IDs (U1 field), not Zotero item keys. For step 3
  (attach to Zotero), the RIS must first be imported into Zotero, then step1_export_zotero.py
  run to get the Zotero keys. For retrieval only (steps 2-2d), EPPI IDs work fine.
- **Config:** `scripts/config.py` points to the StrongMinds Zotero group (same as GE-ftr)
  and the RIS file at `projects/strongminds/data/output/includes.ris`.
