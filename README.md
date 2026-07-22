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

> **Status (2026-07-22):** MHAA TA prompt at **v1.4.3** (sens 0.943 / κ 0.719 / ECE 0.081 on
> the 462 seed). MHAA full-text: 388 PDFs screened, awaiting human review. ULCM orchestrator
> at **v1.9** (canonical, final) — **all three thresholds passed**: sensitivity 0.966, κ 0.790,
> ECE 0.042 on the 510-record seed (23 GT corrections). Live run status, next steps,
> and full history (Parts I–VI, §1–§41) in
> [`projects/strongminds/docs/ITERATION_LOG.md`](projects/strongminds/docs/ITERATION_LOG.md).

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
│   ├── ingest.py / ingest_fts.py   #   dataset / PDF ingestion
│   └── ...                         #   merge, critic, triage, quote-fix helpers
├── projects/
│   ├── girl_effect/                # MHAA — digital/AI mental health for young people
│   │   ├── prompts/                #   TA + full-text screening prompts
│   │   ├── ta_screening/           #   TA stage: data/ + output/
│   │   └── full_text/              #   FTR + FTS: pdfs/, data/, output/, reports/
│   └── strongminds/                # ULCM — brief psych. interventions, adult depression LMICs
│       ├── prompts/                #   ulcm-*.md (orchestrator + monolithic)
│       ├── scripts/                #   project-specific analysis scripts
│       ├── data/                   #   records, ground truth, run outputs
│       ├── artifacts/              #   analysis outputs (adjudication, few-shot, RIS scores)
│       ├── docs/                   #   protocol, scope memos, ITERATION_LOG.md
│       └── strongminds_ris/        #   raw RIS corpus
├── reports/                        # calibration output (metrics/plots/errors, last run)
├── README.md · requirements.txt · .env
```

### Core pipeline (`pipeline/`)

| Path | Purpose |
|---|---|
| `pipeline/k5_runner.py` | **Main runner.** k-sampled screening via OpenRouter, per-model + cross-model aggregation, §2 critic adjudication on flagged records, verbatim-quote validation (with PDF-aware fuzzy fallback) and re-prompt, and calibration (ECE / Brier / κ / sensitivity / per-model + inter-model breakdown). Supports both `--project mhaa` and `--project strongminds`. |
| `pipeline/orchestrator.py` | **ULCM runner.** Router → route-specific screener → critic pipeline; output is `k5_runner --calibrate`-compatible. |
| `pipeline/ingest.py` | Convert an Excel/CSV screening dataset into `records_<n>.jsonl` + `gt_<n>.json` for the runner. MHAA + StrongMinds paired-row CSV layouts. |
| `pipeline/ingest_fts.py` | **Full-text variant.** Extract PDF text via PyMuPDF → `records_<n>.jsonl` with the full article text in the `abstract` field. Produces audit logs for missing/low-text/truncated PDFs. |
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
| `projects/strongminds/prompts/ulcm-orchestrator-prompts.md` | ULCM | **Orchestrator prompts (v1.7, canonical).** Router → no_intervention screener (RQ1/RQ18) → intervention screener (all other routes) → critic. v1.7 = router tightened (prevalence/biomarker/measurement studies not tagged `intervention`) + Criterion 4 reframed around "does the intervention target depression" rather than "is depression the primary outcome." |
| `projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.8.md` | ULCM | **v1.8 staged (not canonical yet).** v1.7 + ZS's scope rules encoded: biological-mechanism exclusion (neuroimaging/biomarker/genetic → FAIL), sub-population scope rule (prisoners OUT, students age-gated, refugees IN, depression-target rule), RQ18 depression-specific instruments only, critic authoritative-scope-rules block. Run only after v1.7 calibration confirms the lever effect. |

### Data & outputs

| Path | Purpose |
|---|---|
| `projects/girl_effect/ta_screening/data/ground_truth.xlsx` | MHAA source screening dataset (EPPI export). |
| `projects/girl_effect/ta_screening/data/records_462.jsonl` | Ingested MHAA TA records (462-record seed). |
| `projects/girl_effect/ta_screening/data/gt_462.json` | Ground-truth labels for the 462 seed. |
| `projects/girl_effect/ta_screening/output/*.jsonl` | Aggregated per-record results from past MHAA runs (git-ignored). |
| `projects/strongminds/data/` | ULCM ingested records + ground-truth (`gt_510.json`, git-ignored; `groundtruth.csv` tracked). |
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
# Current canonical ULCM run (v1.7 prompts, full 510 + critic, resumable)
python pipeline/orchestrator.py `
    --prompt projects/strongminds/prompts/ulcm-orchestrator-prompts.md `
    --records projects/strongminds/data/records_510.jsonl `
    --gt projects/strongminds/data/gt_510.json `
    --out projects/strongminds/data/output/results_orch_v17_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 8

# Calibrate (writes reports/metrics.json, reports/errors.jsonl)
python pipeline/k5_runner.py --calibrate `
    projects/strongminds/data/output/results_orch_v17_510.jsonl `
    --gt projects/strongminds/data/gt_510.json
```

---

## ULCM: status & handoff

Live run status, immediate next steps, the deployment decision, and the full iteration
history (Parts I–III, §1–§20) are maintained in
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
