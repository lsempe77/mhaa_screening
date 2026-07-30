# StrongMinds ULCM — Full-Text Screening (FTS) Process

**Project:** StrongMinds Ultra-Low-Cost Model (ULCM) for Adult Depression
**Stage:** Full-text screening — orchestrated (v1.9-fts)
**Run date:** 2026-07-27 → 2026-07-28
**Prompt version:** `orchestrator-v1.9-fts`

---

## 1. Overview

Full-text screening takes the PDFs retrieved during full-text retrieval (FTR), extracts
their text, and screens each document against the ULCM inclusion criteria using a panel of
LLMs with a tie-breaker. It is the full-text analogue of the title/abstract (RIS) screening
stage, reusing the same orchestrator engine but feeding the models the **entire paper**
instead of just the abstract.

**Result:** 2711 unique documents screened → **1769 INCLUDE / 942 EXCLUDE**, with all model
disagreements resolved automatically. Zero unresolved decision-level conflicts.

---

## 2. Pipeline

```
FTR PDFs ──▶ [1] ingest ──▶ records_fts_2721.jsonl
                              │
                              ▼
                        [2] orchestrator screening (router ON, 2-model panel, k=1)
                              │  → results_fts_v19_2721.jsonl
                              ▼
                        [3] Gemini 2.5 Pro tie-break on decision splits
                              │  → results_fts_v19_tiebreak.jsonl   (deduped, authoritative)
                              ▼
                        [4] outputs:
                              ├─ fts_human_review_3way.csv   (unresolved splits — empty)
                              ├─ fts_summary.csv             (flat review CSV, all 2711)
                              └─ includes_fts.ris            (1769 includes, for Zotero)
```

Stages 1–4 (through the empty human-review CSV) are driven by the wrapper
[run_fts.ps1](../scripts/run_fts.ps1); the review CSV and RIS export (below) were run
separately after completion.

---

## 3. Inputs

| Input | Path |
|---|---|
| FTR inventory | `projects/strongminds/full_text_retrieval/logs/inventory_merged.csv` |
| Retrieved PDFs | `projects/strongminds/full_text_retrieval/pdfs/` |
| Screening prompt | [ulcm-orchestrator-prompts-v1.9-fts.md](../prompts/ulcm-orchestrator-prompts-v1.9-fts.md) |

---

## 4. Step-by-step

### Step 1 — Ingest (PDF text extraction)

[ingest_fts_strongminds.py](../../../pipeline/ingest_fts_strongminds.py) extracts text from
each PDF with PyMuPDF and writes one JSONL record per PDF, with the **full text placed in the
`abstract` field** (so the existing orchestrator/runner work unchanged). Text is capped at
400,000 chars (~100k tokens) per document.

```bash
python pipeline/ingest_fts_strongminds.py \
    --csv projects/strongminds/full_text_retrieval/logs/inventory_merged.csv \
    --pdfs-dir projects/strongminds/full_text_retrieval/pdfs \
    --out-dir projects/strongminds/data/fts
```

Outputs (in `projects/strongminds/data/fts/`):
- `records_fts_2721.jsonl` — 2721 records (194 MB)
- `truncated.jsonl` — 47 documents capped at 400k chars
- `low_text.jsonl` — 1 document with near-zero extractable text (`5HCUQKYA`, image-only PDF)

### Steps 2–4 — Screening + tie-break + review CSV (the wrapper)

[run_fts.ps1](../scripts/run_fts.ps1) runs the whole thing with auto-restart. It is
**resumable**: the orchestrator appends to its output and skips `record_id`s already present,
so a crash/API error just triggers a 30-second restart from where it left off.

**Stage 1 — Orchestrator screening** (router ON, k=1, temperature 0, 2-model panel):

```bash
python pipeline/orchestrator.py \
    --prompt   projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9-fts.md \
    --records  projects/strongminds/data/fts/records_fts_2721.jsonl \
    --out      projects/strongminds/data/output/results_fts_v19_2721.jsonl \
    --k 1 --temperature 0 \
    --router-model openai/gpt-4o-mini \
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 \
    --uncertainty-band 0.4 0.6 \
    --workers 8
```

For each record: the **router** (`gpt-4o-mini`) classifies it into ULCM routes
(determinants / intervention / measurement / cost / …), then the matching **screener** is
run once by each of the two panel models (`claude-sonnet-4`, `glm-5.2`).

**Stage 2 — Tie-break** ([tiebreak_ris.py](../scripts/tiebreak_ris.py)): a third model
(`google/gemini-2.5-pro`) re-screens every record where the two panel models split on the
INCLUDE/EXCLUDE decision (`0.4 < vote_share_include < 0.6`) and casts the deciding vote.

```bash
python projects/strongminds/scripts/tiebreak_ris.py \
    --results projects/strongminds/data/output/results_fts_v19_2721.jsonl \
    --records projects/strongminds/data/fts/records_fts_2721.jsonl \
    --prompt  projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9-fts.md \
    --model   google/gemini-2.5-pro \
    --out     projects/strongminds/data/output/results_fts_v19_tiebreak.jsonl \
    --workers 8 --resume
```

**Stage 3 — Human-review CSV**: unresolved 3-way splits only. **Empty** here — Gemini
resolved all 912 disagreements.

### Post-run — Review CSV and RIS export

Flat, human-readable review CSV of all decisions:

```bash
python pipeline/summarize_fts.py \
    --results projects/strongminds/data/output/results_fts_v19_tiebreak.jsonl \
    --records projects/strongminds/data/fts/records_fts_2721.jsonl \
    --out     projects/strongminds/data/output/fts_summary.csv
```

RIS of the includes for import into Zotero/EndNote. The RIS is built from a **metadata-only**
records file (`records_fts_meta.jsonl` — title/year/doi/record_id, full text stripped) so the
`.ris` stays ~0.4 MB instead of ~180 MB:

```bash
# build the metadata-only records (drops the full-text abstract)
python -c "import json; \
  [open('projects/strongminds/data/fts/records_fts_meta.jsonl','a',encoding='utf-8').write(json.dumps({'record_id':r['record_id'],'title':r.get('title',''),'year':r.get('year',''),'doi':r.get('doi',''),'abstract':'NA','pdf_file':r.get('pdf_file','')},ensure_ascii=False)+'\n') \
   for r in map(json.loads, open('projects/strongminds/data/fts/records_fts_2721.jsonl',encoding='utf-8'))]"

python projects/strongminds/scripts/export_ris.py \
    --results projects/strongminds/data/output/results_fts_v19_tiebreak.jsonl \
    --records projects/strongminds/data/fts/records_fts_meta.jsonl \
    --out     projects/strongminds/data/output/includes_fts.ris \
    --decision INCLUDE
```

---

## 5. Configuration decisions

| Choice | Value | Rationale |
|---|---|---|
| Router | **ON** | Full text is where route-conditional screening pays off; the v1.9-fts prompt is built around routing. |
| Router model | **`openai/gpt-4o-mini`** | Cheap, reliable on large inputs. **Do NOT use `glm-5.2`** (see §7). |
| Panel models | `anthropic/claude-sonnet-4` + `z-ai/glm-5.2` | Same two-model panel as the RIS stage. |
| Tie-breaker | `google/gemini-2.5-pro` | Independent third family for decision splits. |
| k / temperature | k=1, temp 0 | Deterministic single pass per model (full text is expensive; k>1 not warranted). |
| Max text/doc | 400,000 chars | ~100k tokens; caps runaway PDFs (185 of 2721 docs > 100 pages). |

---

## 6. Results

**2711 unique documents** (see §7 on the 2721 → 2711 dedup).

| Decision / code | Count |
|---|---:|
| **INCLUDE_TA** | **1769** |
| EXCLUDE_STUDY_DESIGN | 368 |
| EXCLUDE_INTERVENTION_TOPIC | 244 |
| EXCLUDE_POPULATION | 230 |
| EXCLUDE_OUTCOME | 92 |
| EXCLUDE_TIME_LANGUAGE | 8 |
| **Total EXCLUDE** | **942** |

- **Model disagreements:** 912 decision splits, **all resolved** by the Gemini tie-breaker; 0 flagged for human review.
- **Run health:** 0 errors, 0 parse failures across all 2711 records; no restarts.
- **`needs_second_opinion` = 894** — these are *not* unresolved decisions. Breakdown: code-level
  disagreements (models agree on INCLUDE/EXCLUDE, differ on the exclusion reason) plus
  **232 `quote_validation_failed`** (the model's supporting quote did not verbatim-match the
  PDF text). A reasonable optional QA subset, but no decision conflict remains among them.

---

## 7. Caveats & known issues

1. **Duplicate records (2721 → 2711).** The ingest produced 2721 lines but only 2711 unique
   `record_id`s — 10 Zotero keys appear twice in `inventory_merged.csv` (same key, slightly
   different titles), e.g. `HVAJ2XJT`, `8UUZTBYR`, `LX3P47HX`, `39NTN9X5`. The tie-break file
   deduplicates to 2711; **no records were lost**. Worth cleaning upstream in the inventory.

2. **Broken PDF included by default.** `5HCUQKYA` ("Building youth resilience… Nae Disha")
   yielded only 9 chars of text (image-only PDF) and was **defaulted to INCLUDE_TA** on
   essentially no content. Re-fetch/OCR the PDF or screen it manually before extraction.

3. **232 quote-validation failures.** The INCLUDE/EXCLUDE decision is unaffected, but the
   cited supporting quote could not be verbatim-matched (common with PDF text extraction:
   hyphenation, ligatures, column reflow). Filter `fts_summary.csv` on
   `_flags = quote_validation_failed` if quotes need to be trustworthy for downstream use.

---

## 8. The `glm-5.2` router bug (why the first attempt failed)

The initial run was configured with `z-ai/glm-5.2` as the router, copied from the RIS-stage
defaults. **It failed on every record** with `_error: "API returned null content"**.

**Cause:** `glm-5.2` is a reasoning model. `run_router` ([orchestrator.py:108](../../../pipeline/orchestrator.py#L108))
hardcodes `max_tokens=500`. On ~30k-token full-text input, glm-5.2 spends the entire output
budget on hidden reasoning and returns null visible content (confirmed null even at
`max_tokens=2000`). `run_router` does not catch this, so it propagates up and dooms the record.
glm-5.2 works fine as a *screener* because that call gets `max_tokens=4000` — enough budget to
finish reasoning **and** emit JSON.

**Fix:** use `openai/gpt-4o-mini` as the router (works at 500 tokens). This is already set in
[run_fts.ps1](../scripts/run_fts.ps1). Note also: launch the `.ps1` with an **absolute path**
(`& "C:\...\run_fts.ps1"`) — a relative path makes PowerShell treat it as a module name.

---

## 9. Output manifest (`projects/strongminds/data/output/`)

| File | What |
|---|---|
| `results_fts_v19_2721.jsonl` | Raw stage-1 orchestrator output (2721 lines, incl. 10 dup keys) |
| `results_fts_v19_tiebreak.jsonl` | **Authoritative** deduped, tie-broken results (2711) |
| `fts_summary.csv` | Flat review CSV — all 2711 decisions with title/code/quote/flags |
| `includes_fts.ris` | 1769 includes for reference-manager import |
| `fts_human_review_3way.csv` | Unresolved splits (empty) |
| `fts_run.log` | Full run log |

---

## 10. Re-running / resuming

The pipeline is idempotent and resumable. To resume an interrupted run, just re-launch the
wrapper — it skips completed `record_id`s:

```powershell
& "C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation\Desktop\mhaa_screening\projects\strongminds\scripts\run_fts.ps1"
```

To re-screen from scratch, delete `results_fts_v19_2721.jsonl` and
`results_fts_v19_tiebreak.jsonl` first.
