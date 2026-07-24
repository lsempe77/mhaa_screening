# pipeline/ftr/ — Full-Text Retrieval Pipeline

PDF retrieval and attachment for screening INCLUDEs. Adapted from the GE-ftr pipeline.

## Pipeline

```
includes.ris (from TAS screening)
       ↓
Step 0: Build inventory from RIS → logs/inventory_{ts}.csv
       ↓
Step 1b: Recover missing DOIs via CrossRef
       ↓
Step 2: Automated PDF fetch (DOI-based)
  2.  Unpaywall → OpenAlex → Semantic Scholar → publisher → Sci-Hub
  2b. No-DOI items (grey literature, WHO IRIS, gov.uk)
  2c. Elsevier ScienceDirect API (subscription creds)
  2d. Browser fetch (Playwright/Chrome for JS-protected publishers)
       ↓
Step 3: Attach PDFs to Zotero (optional)
```

## Scripts

| Script | What it does |
|--------|--------------|
| `config.py` | Configuration: Zotero target, paths, .env loading. `FTR_PROJECT_DIR` env var overrides the project data folder. |
| `step0_build_inventory.py` | Build inventory CSV from an RIS file of INCLUDEs (replaces Zotero collection export) |
| `step1_export_zotero.py` | Export a Zotero collection → inventory CSV (alternative to step 0 when items are already in Zotero) |
| `step1b_find_dois.py` | Recover missing DOIs via CrossRef (title + author, similarity-guarded) |
| `step2_fetch_missing_pdfs.py` | OA + publisher fetch by DOI (Unpaywall → OpenAlex → S2 → publisher → Sci-Hub) |
| `step2b_no_doi.py` | Grey literature: URL-based fetch for no-DOI items |
| `step2c_elsevier.py` | ScienceDirect Article Retrieval API (needs ELSEVIER_API_KEY + INSTTOKEN) |
| `step2d_browser.py` | Real Chrome (Playwright) for OA papers behind JS/Akamai (MDPI, BMC, Frontiers) |
| `step2e_auth_browser.py` | Authenticated browser via SSO (experimental) |
| `step2g_notes_retry.py` | Live re-fetch of still-missing items, skipping Zotero items marked "exclude" |
| `step3_attach_to_zotero.py` | Upload fetched PDFs as Zotero attachments |
| `generate_worklist.py` | Build worklist.html — clickable links for manual download |
| `match_manual_pdfs.py` | Match hand-downloaded PDFs by DOI (PyMuPDF) |
| `scan_manual_pdfs.py` | Match PDFs saved with exact `{key}_….pdf` naming |
| `audit_zotero_pdfs.py` | Live query: how many references have/lack a PDF |
| `export_missing.py` | Write CSV of references still lacking a PDF |
| `export_library.py` | Full local snapshot: CSV + all PDFs |
| `fix_empty_attachments.py` | Find/replace empty Zotero-Connector PDF stubs |
| `delete_empty_attachments.py` | Delete fileless attachment stubs |

## Usage

```powershell
# From repo root:
python pipeline/ftr/step0_build_inventory.py
python pipeline/ftr/step1b_find_dois.py logs/inventory_{ts}.csv
python pipeline/ftr/step2_fetch_missing_pdfs.py logs/inventory_{ts}.csv

# For a different project, set FTR_PROJECT_DIR:
$env:FTR_PROJECT_DIR = "projects/other_project/full_text_retrieval"
python pipeline/ftr/step0_build_inventory.py
```

## Project data folders

Each project has its own data folder (default: `projects/strongminds/full_text_retrieval/`):
- `pdfs/` — retrieved PDFs
- `logs/` — inventory CSVs and checkpoints
- `.env` — credentials (Zotero API key, Elsevier API key + insttoken)
- `requirements.txt` — Python dependencies

## Inventory columns

| Column | Meaning |
|--------|---------|
| `zotero_key` | EPPI ID or Zotero item key — the reference↔PDF anchor |
| `doi` | DOI (step 1b may fill via CrossRef) |
| `pdf_path` | Local path once fetched |
| `pdf_source` | Source (publisher, elsevier_api, manual, etc.) |
| `has_pdf` | True once a PDF is attached |
