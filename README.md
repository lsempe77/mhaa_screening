# MHAA / ULCM Screening Pipeline

LLM-driven screening pipeline for two rapid evidence mappings:

1. **MHAA** — *Girl Effect Mental Health Anywhere Anytime* rapid evidence mapping on
   **digital and AI-enabled mental-health interventions for young people**. Supports both
   title/abstract (TA) screening and full-text (FT) screening of PDFs.
2. **ULCM** — *StrongMinds Ultra-Low-Cost Model* rapid review on **brief, structured
   psychological interventions for adult depression in LMICs**. TA screening with
   **research-question (RQ) routing**: 18 RQs across 7 routes (determinants,
   intervention effectiveness, dose/SSI/stepped-care, spillover, cost, safety, measurement)
   drive route-conditional exclusion logic.

The pipeline screens citation records (title + abstract + year, or full PDF text) with a
panel of LLMs via [OpenRouter](https://openrouter.ai/), runs a k-sampled consensus vote,
adjudicates uncertain records with a critic model, and calibrates the panel against human
ground-truth labels (sensitivity, Cohen's κ, ECE, Brier, reliability).

> **Status (2026-07-28):**
>
> **MHAA** — TA prompt at **v1.4.3**: sens 0.943 / κ 0.719 / ECE 0.081 on the 462-record
> seed (κ ✅, ECE ✅, sens 0.007 short). Full-text: 388 PDFs screened, awaiting human
> review. Metrics: [`projects/girl_effect/METRICS.md`](projects/girl_effect/METRICS.md).
>
> **ULCM** — Orchestrator at **v1.9** (canonical): **all three thresholds passed** —
> sensitivity 0.966, κ 0.790, ECE 0.042 on the 510-record seed (23 GT corrections).
> Metrics: [`projects/strongminds/METRICS.md`](projects/strongminds/METRICS.md).
> Full iteration history (Parts I–VI, §1–§41):
> [`projects/strongminds/docs/ITERATION_LOG.md`](projects/strongminds/docs/ITERATION_LOG.md).
>
> The ULCM corpus has now been screened end-to-end across three stages:
>
> | Stage | Input | Output | Result |
> |---|---|---|---|
> | **TA (RIS)** ✅ done | 29,251 records | `includes_worklist.csv` | 4,125 includes → **5,795 after correction** |
> | **FTR** ✅ done | 4,125 includes | `full_text_retrieval/pdfs/` | PDFs retrieved (see [FTR README](pipeline/ftr/README.md)) |
> | **FTS (full text)** ✅ done | 2,721 PDFs | `includes_fts.ris` | **1,769 includes** (0 unresolved) |
> | **RIS determinants correction** ✅ done | 17,033 `EXCLUDE_INTERVENTION_TOPIC` | `includes_fts_final_2670.ris` | +1,670 TA recovered → 1,066 PDFs (no Sci-Hub) → **+901 FTS includes**. Full-text includes **1,769 → 2,670** |
> | **DEX (data extraction)** ✅ done | 2,670 full-text includes | `reports/dex_*.csv` + `dex_review.xlsx` | **2,668/2,670 extracted** (Sonnet, k=1, grounded, audit-stamped). Eligibility review of the 457 possibly-ineligible **complete → drop 323 / keep 134**, so the analysed corpus is **2,347** ([`eligibility_decisions_457.csv`](projects/strongminds/docs/eligibility_decisions_457.csv)). 2 residual (oversized umbrella reviews). [`dex_extraction_process.md`](projects/strongminds/docs/dex_extraction_process.md) |
>
> FTS prompt **v1.9-fts**, run 2026-07-27/28. Write-ups:
> [`fts_screening_process.md`](projects/strongminds/docs/fts_screening_process.md) ·
> [`ris_determinants_correction.md`](projects/strongminds/docs/ris_determinants_correction.md)
> (the `--no-router` RIS run wrongly dropped ~1,670 determinants/measurement includes; being recovered).

---

## What's in this folder

The repository is organised into a **shared engine** (`pipeline/`) and **per-project**
folders (`projects/`), one per rapid evidence mapping. Each project is further split by
screening stage — title/abstract (TA), full-text retrieval (FTR), and full-text
screening (FTS).

```text
mhaa_screening/
├── pipeline/                       # shared engine — run as `python pipeline/<script>.py`
│   ├── k5_runner.py                #   main k-sampled screener + calibration
│   ├── orchestrator.py             #   ULCM router → screener → critic runner
│   ├── ingest.py                   #   dataset ingestion (Excel/CSV → records + gt)
│   ├── ingest_fts.py               #   PDF ingestion (GE full text → records)
│   ├── ingest_fts_strongminds.py   #   PDF ingestion (ULCM full text → records)
│   ├── summarize_fts.py            #   results JSONL → flat review CSV
│   ├── extraction/                 #   post-screening data extraction (dual-model + reconcile)
│   └── ...                         #   merge, critic, triage, quote-fix helpers
├── pipeline/ftr/                   # full-text retrieval pipeline (step0-step3 + helpers)
│   ├── config.py                   #   FTR config (FTR_PROJECT_DIR env var for project data)
│   ├── step0_build_inventory.py    #   build inventory from RIS file
│   ├── step1b-step3 + helpers      #   DOI recovery, PDF fetch, Zotero attach
│   └── README.md                   #   FTR pipeline docs
├── projects/
│   ├── girl_effect/                # MHAA — digital/AI mental health for young people
│   │   ├── prompts/                #   TA + full-text screening prompts
│   │   ├── ta_screening/           #   TA stage: data/ + output/
│   │   └── full_text/              #   FTR + FTS: pdfs/, data/, output/, reports/
│   └── strongminds/                # ULCM — brief psych. interventions, adult depression LMICs
│       ├── prompts/                #   ulcm-*.md (orchestrator TA + v1.9-fts full text)
│       ├── scripts/                #   run_ris_v19.ps1, run_fts.ps1, tiebreak_ris.py, export_ris.py, ...
│       ├── data/                   #   records, ground truth, run outputs
│       │   ├── fts/                #     full-text records: records_fts_2721.jsonl + audit logs
│       │   └── output/             #     all run results (TA RIS + FTS) + includes/review CSVs
│       ├── artifacts/              #   analysis outputs (adjudication, few-shot, RIS scores)
│       ├── docs/                   #   protocol, scope memos, ITERATION_LOG.md, fts_screening_process.md
│       ├── strongminds_ris/        #   raw RIS corpus
│       └── full_text_retrieval/    #   FTR project data (pdfs/, logs/, .env)
│           ├── pdfs/               #     retrieved PDFs (git-ignored)
│           ├── logs/               #     inventory CSVs (git-ignored)
│           └── README.md           #     FTR project data docs
├── reports/                        # calibration output (metrics/plots/errors, last run)
├── README.md · requirements.txt · .env
```

### Core pipeline (`pipeline/`)

| Path | Purpose |
|---|---|
| `pipeline/k5_runner.py` | **Main runner.** k-sampled screening via OpenRouter, per-model + cross-model aggregation, §2 critic adjudication on flagged records, verbatim-quote validation (with PDF-aware fuzzy fallback) and re-prompt, and calibration (ECE / Brier / κ / sensitivity / per-model + inter-model breakdown). Supports both `--project mhaa` and `--project strongminds`. |
| `pipeline/orchestrator.py` | **ULCM runner.** Router → route-specific screener → critic pipeline; output is `k5_runner --calibrate`-compatible. |
| `pipeline/ingest.py` | Convert an Excel/CSV screening dataset into `records_<n>.jsonl` + `gt_<n>.json` for the runner. MHAA + StrongMinds paired-row CSV layouts. |
| `pipeline/ingest_fts.py` | **Full-text variant (GE).** Extract PDF text via PyMuPDF → `records_<n>.jsonl` with the full article text in the `abstract` field. Produces audit logs for missing/low-text/truncated PDFs. |
| `pipeline/ingest_fts_strongminds.py` | **Full-text variant (ULCM).** Same as above but reads the FTR `inventory_merged.csv` + `pdfs/` layout → `projects/strongminds/data/fts/records_fts_<n>.jsonl`. |
| `pipeline/merge_results.py` | Merge stored Claude runs with a new GLM-only run file and re-aggregate (no new API calls). |
| `pipeline/run_critic.py` | Re-run only the §2 critic on flagged records in an existing results JSONL (parallel). |
| `pipeline/summarize_fts.py` | Flatten a results JSONL into a review-friendly CSV (one row per record). |
| `pipeline/generate_triage.py` | Produce `excludes_triage.csv` + `flags_triage.csv` with flag-reason classification for human review. |
| `pipeline/make_gt_from_review.py` | Convert a human-annotated CSV (with a `human_decision` column) into `gt_<n>.json` for calibration. |
| `pipeline/rerun_flagged.py` | Re-screen records that failed (parse_error / api_error) with a higher `max_tokens` override. |
| `pipeline/revalidate_quotes.py` | Re-run quote validation on stored results after `verify_quote()` improvements (no new API calls). |
| `pipeline/fix_quote_failures.py` | Re-screen `quote_validation_failed` records with cleaned (mojibake-stripped) PDF text. |
| `pipeline/add_eppi_ids.py` | Join Zotero keys to EPPI IDs and add an `eppi_id` column to the review CSVs. |
| `requirements.txt` | Python dependencies. |
| `.env` | `OPENROUTER_API_KEY` + OpenRouter HTTP headers (**git-ignored**). |

### Prompts

| Path | Project | Purpose |
|---|---|---|
| `projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.3.md` | MHAA | **TA screener (§1) + critic/adjudicator (§2) + calibration.** Hierarchical exclusion codes 1→7 with the AI-component positive test (Code 4), MH-primary test, governance/safety carve-out. Canonical path for new TA runs. |
| `projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.md` | MHAA | Old copy (v1.4 header, v1.4.3 body). Kept for runners that reference this filename. |
| `projects/girl_effect/prompts/prompts-screening-mhaa-fulltext-v1.md` | MHAA | **Full-text variant of v1.4.3.** Input scope changed from title+abstract to title+full PDF text. Same exclusion codes, same AI-component test, same carve-outs. Quotes may come from the body. Used for the GE_FTS run. |
| `projects/strongminds/prompts/ulcm-tas-screening-prompts-hierarchical.md` | ULCM | **Monolithic TA screener + critic with RQ routing (v1.1, best monolithic).** Superseded by the orchestrator prompt below from v1.6 onward; kept for reproducibility. 18 RQs across 7 routes (see below). Route-conditional exclusion: RQ1 (determinants) and RQ18 (measurement) skip the intervention criterion; RQ7-9/12/14 allow specialist delivery and HIC evidence; RQ11 allows non-case populations. Supports `screening_level: review \| primary_study`. |
| `projects/strongminds/prompts/ulcm-orchestrator-prompts.md` | ULCM | **Orchestrator prompts (v1.7).** Router → no_intervention screener (RQ1/RQ18) → intervention screener (all other routes) → critic. Superseded by v1.9 (below) as canonical for new runs. |
| `projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.8.md` | ULCM | v1.8 staged prompts (ZS scope rules: bio-mechanism exclusion, sub-population scope, RQ18 instruments). Intermediate — superseded by v1.9. |
| `projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9.md` | ULCM | **v1.9 prompts (canonical for TA).** v1.8.1 + 7 surgical fixes: unstated-age rule, mixed-age rule, working-memory/task-sharing/process-study PASS additions, CMD promotion. All three thresholds passed (sens 0.966, κ 0.790, ECE 0.042). Used for the 29,251-record RIS run. |
| `projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9-fts.md` | ULCM | **Full-text screening variant of v1.9.** Router + no_intervention + intervention + critic sections, adapted for full PDF text (quotes may come from the body). Canonical for the ULCM FTS run (2,721 PDFs). |

### Data & outputs

| Path | Purpose |
|---|---|
| `projects/girl_effect/ta_screening/data/ground_truth.xlsx` | MHAA source screening dataset (EPPI export). |
| `projects/girl_effect/ta_screening/data/records_462.jsonl` | Ingested MHAA TA records (462-record seed). |
| `projects/girl_effect/ta_screening/data/gt_462.json` | Ground-truth labels for the 462 seed. |
| `projects/girl_effect/ta_screening/output/*.jsonl` | Aggregated per-record results from past MHAA runs (git-ignored). |
| `projects/strongminds/data/` | ULCM ingested records + ground-truth (`gt_510.json`, git-ignored; `groundtruth.csv` tracked). |
| `projects/strongminds/data/fts/` | **ULCM full-text records.** `records_fts_2721.jsonl` (full PDF text) + audit logs (`truncated.jsonl`, `low_text.jsonl`). |
| `projects/strongminds/data/output/` | ULCM run results. **TA/RIS:** `results_ris_v19_tiebreak.jsonl`, `includes_worklist.csv`, `includes.ris` (4,125 includes). **FTS:** `results_fts_v19_tiebreak.jsonl`, `fts_summary.csv`, `includes_fts.ris` (1,769 includes). |
| `projects/strongminds/artifacts/` | ULCM analysis outputs: GT adjudication, few-shot results, RIS scores. |
| `projects/strongminds/docs/` | ULCM protocol, scope memos, and `ITERATION_LOG.md` (full history). |
| `reports/` | Calibration outputs (shared): `metrics.json`, `confusion_matrix.png`, `reliability_diagram.png`, `errors.jsonl`. Overwritten by each `--calibrate` run (git-ignored). |
| `projects/girl_effect/full_text/` | **GE full-text screening set.** `pdfs/` (388 PDFs), `references_*.csv` (Zotero export), `data/` (ingested records + audit logs), `output/` (results JSONL), `reports/` (summary + triage CSVs + review email). All git-ignored. |
| `.kilo/` | Kilo CLI config (Agent Manager state only). |

> Note on the two MHAA TA prompt files: `Compare-Object` on their contents returns
> **IDENTICAL**. The `...unified-v1.4.md` header still says "v1.4" but its body and change
> log are v1.4.3; `...unified-v1.4.3.md` is simply the renamed copy. Both now live in
> `projects/girl_effect/prompts/` and either works with `--prompt`; prefer the `v1.4.3`
> one for new runs.

---

## Setup

```powershell
# 1. Python deps
pip install -r requirements.txt

# 2. OpenRouter API key (the .env already has one; replace with your own)
#    .env contents:
#      OPENROUTER_API_KEY=sk-or-v1-...
#      HTTP_REFERER=http://localhost/mhaa
#      X_TITLE=MHAA Screening
```

All model calls go through OpenRouter, so you need one key regardless of how many model
families you use. Model slugs below are OpenRouter IDs (e.g. `anthropic/claude-sonnet-4`,
`z-ai/glm-5.2`, `mistralai/mistral-large`).

---

## End-to-end workflow

### 1. Ingest your dataset

Defaults match the MHAA `ground_truth.xlsx` layout (header on row index 1; columns
`EPPI ID`, `PY`, `T1`, `AB`, `EPPI TAS decision`).

```powershell
python pipeline/ingest.py --input projects/girl_effect/ta_screening/data/ground_truth.xlsx --out-dir data
```

Produces `projects/girl_effect/ta_screening/data/records_<N>.jsonl` and `projects/girl_effect/ta_screening/data/gt_<N>.json`. For a CSV or a different column
layout, see the override flags in `ingest.py --help`.

### 2. Run k-sampled screening

k=5 sampled runs per record, per model, at temperature > 0. Two model families are run in
parallel for cross-model consensus; flagged records are sent to the critic.

```powershell
python pipeline/k5_runner.py `
    --prompt projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records projects/girl_effect/ta_screening/data/records_462.jsonl `
    --gt projects/girl_effect/ta_screening/data/gt_462.json `
    --out projects/girl_effect/ta_screening/output/results_k5_462.jsonl `
    --k 5 `
    --temperature 0.5 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 5
```

**Resume:** the runner appends to `--out` and skips `record_id`s already present, so you
can re-run the same command after an interruption.

**Per-record verdict logic** (in `k5_runner.py`):
- Each model gets k runs; `aggregate_one_model` takes the majority code + INCLUDE vote share.
- `combine_models` pools runs across models; `needs_second_opinion` fires on model
  disagreement, vote share in `[lo, hi]`, low confidence, or any run-level flag.
- Flagged records go to the §2 critic, which independently re-screens and either `confirm`s
  or `override`s the primary verdict.
- Verbatim `supporting_quote` is validated against the title/abstract; on failure the run
  is re-prompted once, then forced to `needs_second_opinion`.

### 3. Calibrate against ground truth

```powershell
python pipeline/k5_runner.py --calibrate projects/girl_effect/ta_screening/output/results_k5_462.jsonl --gt projects/girl_effect/ta_screening/data/gt_462.json
```

Prints the confusion matrix, sensitivity/specificity/precision/κ/ECE/Brier, per-model
breakdown, inter-model agreement (κ between the two primary models), and critic
adjudication counts. Writes `reports/metrics.json`, `reports/confusion_matrix.png`,
`reports/reliability_diagram.png`, and `reports/errors.jsonl` (the FN + FP records for
criteria refinement).

**Protocol thresholds:** sensitivity ≥ 0.95, Cohen's κ ≥ 0.70, ECE ≤ 0.10.

---

## Two-model merge workflow (Claude + GLM)

If you already have a Claude + GPT results file and want to add GLM runs (or any second
model) without re-paying for the Claude calls:

```powershell
# a) Run GLM-only (one model) into its own file
python pipeline/k5_runner.py `
    --prompt projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records projects/girl_effect/ta_screening/data/records_462.jsonl --gt projects/girl_effect/ta_screening/data/gt_462.json `
    --out projects/girl_effect/ta_screening/output/results_glm_462.jsonl `
    --k 5 --temperature 0.5 `
    --models z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --workers 5

# b) Merge Claude runs (from the old file) + GLM runs (from the new file), re-aggregate
python pipeline/merge_results.py `
    --old projects/girl_effect/ta_screening/output/results_k5_462.jsonl `
    --new projects/girl_effect/ta_screening/output/results_glm_462.jsonl `
    --records projects/girl_effect/ta_screening/data/records_462.jsonl `
    --out projects/girl_effect/ta_screening/output/results_merged_462.jsonl `
    --uncertainty-band 0.4 0.6 `
    --claude-model anthropic/claude-sonnet-4 `
    --glm-model z-ai/glm-5.2

# c) Re-run the critic on the merged verdict's flagged records
python pipeline/run_critic.py `
    --prompt projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records projects/girl_effect/ta_screening/data/records_462.jsonl `
    --in projects/girl_effect/ta_screening/output/results_merged_462.jsonl `
    --out projects/girl_effect/ta_screening/output/results_critic_462.jsonl `
    --critic-model mistralai/mistral-large `
    --temperature 0.5 `
    --workers 15

# d) Calibrate the merged + critic-adjudicated file
python pipeline/k5_runner.py --calibrate projects/girl_effect/ta_screening/output/results_critic_462.jsonl --gt projects/girl_effect/ta_screening/data/gt_462.json
```

`merge_results.py` does **not** make new API calls — it reuses stored per-run objects and
re-derives aggregation. The merge drops old critic runs because the `needs_second_opinion`
flags are re-derived from the combined model agreement; run `run_critic.py` afterwards to
adjudicate the freshly-flagged set.

---

## Output schema

Each line of a results JSONL is one record's aggregated verdict:

```jsonc
{
  "record_id": "130142880",
  "screening_code": "INCLUDE_TA",          // majority code across all pooled runs
  "screening_decision": "INCLUDE",          // INCLUDE | EXCLUDE
  "vote_share_include": 0.8,                // pooled INCLUDE count / total runs (for ECE)
  "n_runs": 10,                            // k × #models (+ critic if applied)
  "code_counts": {"INCLUDE_TA": 8, "EXCLUDE_TOPIC": 2},
  "in_uncertainty_band": false,            // vote share inside [0.4, 0.6]
  "model_agreement": "agree",              // agree | disagree (binary decision across models)
  "needs_second_opinion": false,           // triggers critic
  "per_model": [ /* per-model majority + vote share, no runs */ ],
  "runs": [ /* all individual runs incl. _model, _temperature; critic run has _role:"critic" */ ],
  "critic": {
    "applied": true,
    "adjudication": "confirm",              // confirm | override | null
    "model": "mistralai/mistral-large",
    "overridden_code": "NA"                 // present only on override
  }
}
```

Each individual run carries the fields the screener returns: `screening_code`,
`screening_decision`, `ssa_lmic_marker`, `explanation`, `supporting_quote`,
`needs_second_opinion`, `confidence`, `record_id`.

---

## Prompt versioning

### MHAA (v1.4 → v1.4.3, plus full-text variant)

The MHAA prompt spec is a single document with two roles:

- **§1 `mhaa.screening.ta`** — primary title/abstract screener. Hierarchical exclusion
  codes applied in order (1 language → 2 year → 3 population → 4 topic → 5 evidence type
  → 6 duplicate → 7 INCLUDE_TA), first failing code wins. v1.4 added the **AI-component
  positive test** on Code 4 (digital/mHealth-only without an explicit AI/ML signal →
  EXCLUDE_TOPIC). v1.4.1–v1.4.3 refined the age-overlap rule, added the MH-primary test +
  governance carve-out, and tightened evidence-type exclusions.
- **§2 `mhaa.screening.critic`** — second-opinion adjudicator, invoked only on flagged
  records. Re-screens from scratch, then `confirm`/`override`.

**Full-text variant** (`projects/girl_effect/prompts/prompts-screening-mhaa-fulltext-v1.md`): identical criteria to
v1.4.3, but the input scope changed from title+abstract to title+full PDF text. Verbatim
quotes may come from the body. Used for the GE_FTS run (388 PDFs, GLM-5.2, k=1).

See the **Appendix C — Change log** at the bottom of each prompt file for the version
history and the per-version calibration on the 462-record seed.

### ULCM / StrongMinds (draft-v1.0 → draft-v1.1, RQ-routed)

The ULCM prompt is RQ-routed: the model assigns plausible research-question tags
**before** applying the exclusion hierarchy, and several exclusion criteria are
route-conditional.

**Research-question routes (18 RQs across 7 routes):**

| Route | RQ tags | Key scope signal |
|---|---|---|
| Determinants | RQ1 | Risk factors for adult depression; **no intervention required** |
| Intervention effectiveness & design | RQ2-RQ6, RQ10, RQ13-RQ15 | Brief structured psychological intervention; group + non-specialist delivery |
| Dose / SSI / temporal / stepped care | RQ7-RQ9, RQ12, RQ14 | Session number, timing, durability; **specialist delivery + HIC evidence eligible** |
| Spillover | RQ11 | Effects on non-cases / households; **non-case populations eligible** |
| Cost | RQ16 | Cost-effectiveness, resource use |
| Safety & referral | RQ17 | Adverse events, escalation pathways for lay-delivered interventions |
| Measurement | RQ18 | Validity/reliability of depression tools in LMICs; **no intervention required** |

**Route-conditional exclusion logic (v1.1):**
- **RQ1 and RQ18 skip the intervention criterion (Code 3)** — these routes don't require
  a psychological intervention, so the intervention test is automatically passed.
- **RQ7-RQ9/RQ12/RQ14 allow specialist delivery and HIC/UMIC evidence** — the standard
  non-specialist + LMIC requirement is relaxed for dose/SSI/stepped-care questions.
- **RQ11 allows non-case or universal-prevention populations** — the standard
  adult-depression population requirement is relaxed for spillover evidence.
- **RQ17 requires lay-delivered or low-resource delivery** — safety/referral evidence
  must concern brief psychological intervention systems, not specialist clinics.

The model assigns `rq_tags` (e.g. `["RQ2", "RQ13"]`) and a `stream` (Stream 1 / Stream 2 /
Both) in its response, then walks the hierarchical codes P → S → I → O → Geo → T. The
first clear failure wins; uncertain records are retained.

v1.1 was driven by calibration on the 510-record seed: 18/28 FNs were
`EXCLUDE_INTERVENTION_TOPIC` wrongly applied to RQ1/RQ18 records (the "no intervention
required" carve-out was buried at the end of Criterion 3). v1.1 promotes the route check
to the top of Criterion 3, forcing the model to resolve RQ-assignment before applying the
intervention test.

---

## GE_FTS full-text screening workflow

Screen a set of PDFs on their full text (not just title+abstract). Used for the GE
Zotero reference set (388 PDFs).

### 1. Ingest PDFs

```powershell
python pipeline/ingest_fts.py `
    --csv projects/girl_effect/full_text/references_20260718_204803.csv `
    --pdfs-dir projects/girl_effect/full_text/pdfs `
    --out-dir projects/girl_effect/full_text/data
```

Extracts text from each PDF via PyMuPDF, writes `records_<n>.jsonl` (full text in the
`abstract` field), and produces audit logs: `missing_pdf.jsonl` (no PDF in Zotero),
`truncated.jsonl` (text capped at 400k chars ≈ 100k tokens), `low_text.jsonl` (likely
scanned images).

### 2. Run full-text screening

```powershell
python pipeline/k5_runner.py `
    --prompt projects/girl_effect/prompts/prompts-screening-mhaa-fulltext-v1.md `
    --records projects/girl_effect/full_text/data/records_388.jsonl `
    --out projects/girl_effect/full_text/output/results_fts_glm_388.jsonl `
    --k 1 --temperature 0 `
    --models z-ai/glm-5.2 `
    --workers 5
```

Single-model, single-pass (k=1, temperature 0). No `--critic-model` → no §2 adjudication.
No `--gt` → no calibration (the GE set has no human ground-truth yet).

**Note on `max_tokens`:** GLM-5.2 may need `max_tokens > 1500` for long full-text records.
If records fail with `api_error` (null content) or `parse_error` (truncated JSON), re-run
with `rerun_flagged.py --max-tokens 4000`.

### 3. Produce review artifacts

```powershell
# Flatten results → review CSV
python pipeline/summarize_fts.py `
    --results projects/girl_effect/full_text/output/results_fts_glm_388.jsonl `
    --records projects/girl_effect/full_text/data/records_388.jsonl `
    --out projects/girl_effect/full_text/reports/summary.csv

# Triage CSVs: excludes (high-stakes) + flagged INCLUDEs (with flag-reason classification)
python pipeline/generate_triage.py
```

### 4. Human review + calibration

Reviewers add a `human_decision` column (INCLUDE / EXCLUDE / blank) directly to
`summary.csv`, save as `summary_annotated.csv`, then:

```powershell
# Convert annotated CSV → ground-truth JSON
python pipeline/make_gt_from_review.py `
    --csv projects/girl_effect/full_text/reports/summary_annotated.csv `
    --out projects/girl_effect/full_text/data/gt_388.json

# Calibrate
python pipeline/k5_runner.py --calibrate projects/girl_effect/full_text/output/results_fts_glm_388.jsonl --gt projects/girl_effect/full_text/data/gt_388.json
```

---

## ULCM / StrongMinds workflow

```powershell
# 1. Ingest the StrongMinds paired-row CSV (decision on the row below each record)
python pipeline/ingest.py --input projects/strongminds/data/groundtruth.csv --out-dir projects/strongminds/data `
    --format strongminds_csv

# 2. Run k-sampled screening with RQ routing
python pipeline/k5_runner.py `
    --project strongminds `
    --prompt projects/strongminds/prompts/ulcm-tas-screening-prompts-hierarchical.md `
    --records projects/strongminds/data/records_510.jsonl `
    --gt projects/strongminds/data/gt_510.json `
    --out projects/strongminds/data/output/results_k5_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 5

# 3. Calibrate
python pipeline/k5_runner.py --calibrate projects/strongminds/data/output/results_k5_510.jsonl `
    --gt projects/strongminds/data/gt_510.json
```

The `--project strongminds` flag selects the ULCM user/critic message templates (which
carry `screening_level`, `language_metadata`, `keywords`, `source_review_id`,
`source_review_in_scope` fields) and raises `max_tokens` to 3000 (the ULCM response schema
includes a 6-step `hierarchical_trace` with rationale + quote per step).

> **Note:** the orchestrator (`orchestrator.py`) supersedes the monolithic
> `k5_runner.py` workflow for ULCM from v1.6 onward. It splits screening into a router →
> route-specific screener → critic pipeline, and its prompt file
> (`projects/strongminds/prompts/ulcm-orchestrator-prompts.md`) is the canonical one for new ULCM runs.
> See `projects/strongminds/docs/ITERATION_LOG.md` Part I §5–7 and Part III §16–§20 for the full history.

```powershell
# Canonical ULCM run (v1.9 prompts, full 510 + critic, resumable)
python pipeline/orchestrator.py `
    --prompt projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9.md `
    --records projects/strongminds/data/records_510.jsonl `
    --gt projects/strongminds/data/gt_510.json `
    --out projects/strongminds/data/output/results_orch_v19_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 8

# Calibrate (writes reports/metrics.json, reports/errors.jsonl)
python pipeline/k5_runner.py --calibrate `
    projects/strongminds/data/output/results_orch_v19_510.jsonl `
    --gt projects/strongminds/data/gt_510.json
```

### Full RIS corpus run (29,251 records)

The full corpus uses a leaner config (k=1, temp 0, no critic, `--no-router`) per §13
findings, with a Gemini 2.5 Pro tie-breaker on model disagreements. The `--no-router`
flag skips the router call and runs the intervention screener directly on all records —
the screener self-determines whether an intervention is required (Criterion 3 step 1),
halving serial latency per record (~2x throughput). The 3-stage pipeline runs
automatically via a wrapper script:

```powershell
# Runs all 3 stages automatically (persistent, auto-restart on crash):
#   Stage 1: Orchestrator screens all 29,251 records (Claude + GLM, k=1, temp 0, --no-router)
#   Stage 2: Gemini 2.5 Pro tie-breaks ~3,900 disagreements (majority of 3)
#   Stage 3: Produces human_review_3way.csv of unresolved 3-way splits
powershell -ExecutionPolicy Bypass -File projects/strongminds/scripts/run_ris_v19.ps1

# Check progress:
Get-Content projects/strongminds/data/output/ris_run.log -Tail 10
```

### Full-text screening (FTS) run

The 4,125 RIS includes went through FTR (PDF retrieval), then full-text screening. FTS
screens each PDF on its **entire text** with the router **on** (route-conditional screening
is where full text pays off) and a 2-model panel + Gemini tie-breaker — same engine as the
RIS run, driven by [`run_fts.ps1`](projects/strongminds/scripts/run_fts.ps1). Full write-up
and command reference: [`projects/strongminds/docs/fts_screening_process.md`](projects/strongminds/docs/fts_screening_process.md).

```powershell
# 1. Ingest retrieved PDFs → full-text records
python pipeline/ingest_fts_strongminds.py `
    --csv projects/strongminds/full_text_retrieval/logs/inventory_merged.csv `
    --pdfs-dir projects/strongminds/full_text_retrieval/pdfs `
    --out-dir projects/strongminds/data/fts

# 2. Screen + tie-break + review CSV (persistent, auto-restart, router ON, gpt-4o-mini router)
& "C:\...\projects\strongminds\scripts\run_fts.ps1"      # use an ABSOLUTE path

# 3. Review artifacts
python pipeline/summarize_fts.py `
    --results projects/strongminds/data/output/results_fts_v19_tiebreak.jsonl `
    --records projects/strongminds/data/fts/records_fts_2721.jsonl `
    --out projects/strongminds/data/output/fts_summary.csv
python projects/strongminds/scripts/export_ris.py `
    --results projects/strongminds/data/output/results_fts_v19_tiebreak.jsonl `
    --records projects/strongminds/data/fts/records_fts_meta.jsonl `
    --out projects/strongminds/data/output/includes_fts.ris --decision INCLUDE
```

> **Router model matters:** the FTS router must be `openai/gpt-4o-mini`, **not** `z-ai/glm-5.2`.
> On ~30k-token full-text input with the router's 500-token cap, glm-5.2 (a reasoning model)
> returns null content and every record fails. glm-5.2 is fine as a *screener* (4000-token
> budget). See §8 of the FTS process doc.

---

## ULCM: status & handoff

**v1.9 final — all three thresholds passed** (sens 0.966 ✅, κ 0.790 ✅, ECE 0.042 ✅).
Metrics: [`projects/strongminds/METRICS.md`](projects/strongminds/METRICS.md).

**TA/RIS run ✅ complete** — 29,251 records screened + tie-broken → **4,125 includes**
(`includes_worklist.csv`), 0 unresolved 3-way splits.

**FTS run ✅ complete** — 2,721 PDFs (2,711 unique) screened + tie-broken → **1,769 includes**
(`includes_fts.ris`), 0 unresolved decision splits, 0 errors. Details:
[`projects/strongminds/docs/fts_screening_process.md`](projects/strongminds/docs/fts_screening_process.md).

**RIS determinants correction ✅** — the initial `--no-router` TA pass wrongly excluded determinant/measurement records (EXCLUDE_INTERVENTION_TOPIC); a router-ON re-screen recovered **1,670** records → FTR (no Sci-Hub) → FTS → **901** further includes. Details: [`projects/strongminds/docs/ris_determinants_correction.md`](projects/strongminds/docs/ris_determinants_correction.md).

**Data extraction (DEX) ✅ complete** — the FTS includes plus the recovered set gave **2,670 full-text includes**; **2,668 extracted** into the ~220-field, verbatim-quote-grounded ULCM schema (`dex_full_2670.jsonl`). The human eligibility review of the 457 possibly-ineligible records is complete (**drop 323 / keep 134**, in [`eligibility_decisions_457.csv`](projects/strongminds/docs/eligibility_decisions_457.csv)), so the **analysed corpus is 2,347** — applied at report-generation via the `--exclude` flag. Process: [`pipeline/extraction/README.md`](pipeline/extraction/README.md), [`projects/strongminds/docs/dex_extraction_process.md`](projects/strongminds/docs/dex_extraction_process.md).

**Evidence-synthesis report ✅ complete** — a Quarto book answering RQ1–RQ18 by workstream (A context/drivers · B ingredients/dose · C delivery/workforce/engagement · D cost/spillovers, + safety/measurement), with harvest plots, robustness-across-subsets, GRADE-lite, and per-RQ answer boxes. Rendered to **HTML, PDF (132 pp) and Word** at [`projects/strongminds/analysis/book/`](projects/strongminds/analysis/book/). It is a *descriptive* map of machine-extracted, not-yet-human-verified evidence; confidence is capped at Low and the vote-count/publication-bias caveats are stated in its Methods.

### Reproducing the extraction → reports → report

Prerequisites: Python deps (`pandas`, `matplotlib`, `openpyxl`, `tabulate`), [Quarto](https://quarto.org), and a LaTeX toolchain for PDF (`quarto install tinytex`). Paths below are abbreviated with `…` = `projects/strongminds/data/extraction`.

```powershell
# 0. Data extraction itself (LLM extractor over the 2,670 includes) — see pipeline/extraction/README.md
#    -> …/dex_full_2670.jsonl
#    Human eligibility review of the 457 possibly-ineligible records is captured in the
#    committed, PII-free decisions file (record_id, action, concern):
#    projects/strongminds/docs/eligibility_decisions_457.csv  (drop 323 / keep 134)
#    Passing --exclude removes the 323 'drop' records -> analysed corpus = 2,347.
EX=projects/strongminds/docs/eligibility_decisions_457.csv

# 1. Flatten extraction -> review tables (summary / long / outcomes / rq_contributions / queue + Excel)
python pipeline/extraction/dex_export.py `
  --results …/dex_full_2670.jsonl --records …/records_extract_final_2670.jsonl --out-dir …/reports --exclude $EX

# 2. Cross-review overlap (global + per-RQ CCA / discordance)
python pipeline/extraction/dex_overlap.py       --results …/dex_full_2670.jsonl --out-dir …/reports --exclude $EX
python pipeline/extraction/dex_overlap_by_rq.py --results …/dex_full_2670.jsonl --out …/reports/dex_overlap_by_rq.csv --exclude $EX

# 3. Wide all-fields table (dex_wide.csv) + per-RQ Excel workbook (dex_by_rq.xlsx)
python pipeline/extraction/dex_wide_by_rq.py `
  --long …/reports/dex_long.csv --summary …/reports/dex_summary.csv --out-dir …/reports

# 4. Enriched includes RIS (authors/journal/volume/pages/abstract from the source corpus)
python projects/strongminds/scripts/export_ris_enriched.py `
  --includes projects/strongminds/data/output/includes_fts_final_2670.ris `
  --source-dir projects/strongminds/strongminds_ris `
  --out projects/strongminds/data/output/includes_fts_final_2670_enriched.ris

# 5. Render the evidence-synthesis report in all three formats, from the reports/ CSVs
cd projects/strongminds/analysis/book
quarto render          # -> _book/index.html (+ chapters), _book/*.pdf, _book/*.docx
```

The book consumes only the `reports/` CSVs (`dex_summary`, `dex_long`, `dex_wide`, `dex_rq_contributions_long`, `dex_overlap_by_rq`); `chapter_helpers.py` resolves that path relative to itself, so `quarto render` runs from the book folder with no arguments. The earlier standalone *evidence-and-gap map* ([`analysis/ulcm_evidence_map.qmd`](projects/strongminds/analysis/ulcm_evidence_map.qmd)) renders from the same CSVs. All data files and rendered outputs (`_book/`, CSVs, `.xlsx`, `.ris`) are gitignored — they are regenerated by the steps above.

- The screener remains a **second reviewer / triage tool**, not an autonomous excluder (§14 finding); all extracted quantitative values are **machine-extracted and pending human verification** before use in synthesis.

Full iteration history (Parts I–VI, §1–§41):
[`projects/strongminds/docs/ITERATION_LOG.md`](projects/strongminds/docs/ITERATION_LOG.md).

---

## Quick commands

```powershell
# MHAA smoke test on the 10-record subset
python pipeline/k5_runner.py --prompt projects/girl_effect/prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records projects/girl_effect/ta_screening/data/records_10.jsonl --gt projects/girl_effect/ta_screening/data/gt_10.json `
    --out projects/girl_effect/ta_screening/output/results_10.jsonl `
    --k 5 --temperature 0.5 --models z-ai/glm-5.2 --workers 5

# MHAA re-calibrate any existing results file without re-running models
python pipeline/k5_runner.py --calibrate projects/girl_effect/ta_screening/output/results_v143_critic_462.jsonl --gt projects/girl_effect/ta_screening/data/gt_462.json

```
