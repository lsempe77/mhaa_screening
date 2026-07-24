# StrongMinds ULCM — Full-Text Retrieval (data folder)

This folder holds the project-specific data for the FTR pipeline:
- `pdfs/` — retrieved PDFs (git-ignored)
- `logs/` — inventory CSVs and checkpoints (git-ignored)
- `.env` — credentials (Zotero, Elsevier) — git-ignored

The pipeline scripts live in **`pipeline/ftr/`** (shared engine). See the
[pipeline/ftr/ README](../../../pipeline/ftr/README.md) for full docs.

## Quick start

```powershell
# From repo root:
python pipeline/ftr/step0_build_inventory.py
python pipeline/ftr/step1b_find_dois.py logs/inventory_{ts}.csv
python pipeline/ftr/step2_fetch_missing_pdfs.py logs/inventory_{ts}.csv
```

The `FTR_PROJECT_DIR` env var overrides the project data folder (defaults to
`projects/strongminds/full_text_retrieval/`).
