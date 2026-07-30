# StrongMinds ULCM — RIS Determinants-Miss Correction

**Date:** 2026-07-28 / 29
**Trigger:** human full-text screening ground truth (`data/gt_fts_71.csv`, 71 records)
**Outcome:** +1,670 wrongly-excluded includes recovered; corrected TA includes 4,125 → **5,795**

---

## 1. What went wrong

The full RIS (title/abstract) screening run ([run_ris_v19.ps1](../scripts/run_ris_v19.ps1))
used **`--no-router`**, which applies the *intervention* screener to **every** record. The
assumption was that the intervention screener would self-detect "no intervention required"
routes and pass them. In practice it did not: **determinants (RQ1)** and **measurement (RQ18)**
papers — which legitimately have no intervention — were stamped `EXCLUDE_INTERVENTION_TOPIC`
and dropped at title/abstract, before they could reach full-text retrieval or screening.

```
RIS screening (--no-router, intervention screener on all records)
   → determinants / measurement papers wrongly EXCLUDED (EXCLUDE_INTERVENTION_TOPIC)
   → never added to the FTR inventory (built from includes only)
   → never fetch-attempted → no PDF → invisible to FTS + extraction
```

## 2. How it was found

A human FTS ground truth of 71 records was reconciled against the LLM pipeline
([fts_confusion.py](../scripts/fts_confusion.py); bridge: `GT.eppi == ris_records.record_id`
→ DOI/title → Zotero key → prediction). Only **19 of 71** reached full-text screening; **41
were excluded at RIS/TA**. The RIS-stage confusion matrix:

| | LLM INCLUDE | LLM EXCLUDE |
|---|---|---|
| **Human INCLUDE** | 18 | **27** (dropped before full text) |
| **Human EXCLUDE** | 9 | 14 |

Sensitivity **0.40**, κ ≈ 0. **23 of the 27** dropped human-includes carried
`EXCLUDE_INTERVENTION_TOPIC` — the determinants signature.

## 3. Scale confirmation (no-API, then empirical)

- `EXCLUDE_INTERVENTION_TOPIC` = **17,033 records = 58%** of the whole 29,251 corpus (the
  single dominant exclusion reason).
- A seeded random sample of 150 determinants-signature EIT records, re-screened with the
  **router ON** (v1.9 TA prompt, gpt-4o-mini router), flipped to INCLUDE at **17.3%**
  (95% CI 12.1–24.2%); 115/150 re-routed to `determinants`, 25 to `measurement`.

## 4. The correction

Re-screened **all 17,033** `EXCLUDE_INTERVENTION_TOPIC` records with the **router ON**
([run_rescreen_eit.ps1](../scripts/run_rescreen_eit.ps1)):

```powershell
python pipeline/orchestrator.py `
    --prompt  projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9.md `
    --records projects/strongminds/data/rescreen_eit_records.jsonl `
    --out     projects/strongminds/data/output/rescreen_eit_results.jsonl `
    --k 1 --temperature 0 `
    --router-model openai/gpt-4o-mini `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 --workers 8
```

**Result: 17,033 re-screened, 0 errors → 1,670 recovered includes (9.8% flip).**
Route breakdown of the recovered records: **determinants 1,532, measurement 389,
intervention 111** (routes overlap). Recovered IDs: `data/output/rescreen_eit_recovered_ids.txt`.

## 5. Downstream (in progress)

| Step | Status |
|---|---|
| Re-screen EIT (router ON) → 1,670 recovered | ✅ done |
| Corrected TA-includes RIS (4,125 + 1,670 = **5,795**) → `includes_ta_corrected_5795.ris` | ✅ done |
| FTR-retrieve PDFs for the 1,670 (**Sci-Hub OFF**) → `inventory_recovered_1670.csv` | ✅ **1,066/1,670 (64%)** — unpaywall 578, elsevier 322, publisher 92, browser/MDPI 46, openalex 20, s2 7. Steps: step2 + step1b + step2c + step2d + step2b. 604 missing (52 no-DOI) → manual worklist. |
| FTS-screen retrieved PDFs (router-ON v1.9-fts) + merge into extraction corpus | ⏳ pending |
| Regenerate **final full-text includes RIS** for the merged extraction set | ⏳ pending |

## 6. Count summary

| Metric | Before | After |
|---|---|---|
| TA / title-abstract includes | 4,125 | **5,795** (+1,670) |
| Full-text (extraction) includes | 1,769 | 1,769 + (recovered PDFs passing FTS) — pending |

## 7. Files

| File | What |
|---|---|
| `data/gt_fts_71.csv` | Human FTS ground truth (71 records) |
| `scripts/fts_confusion.py` | GT ↔ pipeline reconciliation + confusion matrices |
| `data/output/fts_gt_reconciliation.csv` | Per-record reconciliation |
| `data/rescreen_eit_records.jsonl` | The 17,033 EIT records re-screened |
| `scripts/run_rescreen_eit.ps1` | Router-ON corrective re-screen wrapper |
| `data/output/rescreen_eit_results.jsonl` | Re-screen results (17,033) |
| `data/output/rescreen_eit_recovered_ids.txt` | 1,670 recovered include IDs |
| `data/output/includes_ta_corrected_5795.ris` | Corrected TA-includes RIS |
| `full_text_retrieval/logs/inventory_recovered_1670.csv` | FTR inventory for the recovered set |

## 8. Lesson

`--no-router` was chosen for the full RIS run to halve latency, on the assumption the
intervention screener self-handles no-intervention routes. It does not. **Any run covering
the determinants (RQ1) or measurement (RQ18) routes must keep the router ON.** The FTS run
already did (v1.9-fts, router ON); only the RIS run was affected.
