# MHAA Screening Pipeline

LLM-driven title/abstract (TA) screening pipeline for the **Girl Effect Mental Health
Anywhere Anytime (MHAA)** rapid evidence mapping on **digital and AI-enabled mental-health
interventions for young people**.

The pipeline screens citation records (title + abstract + year) with a panel of LLMs via
[OpenRouter](https://openrouter.ai/), runs a k-sampled consensus vote, adjudicates
uncertain records with a critic model, and calibrates the panel against human ground-truth
labels (sensitivity, Cohen's κ, ECE, Brier, reliability).

> **Status:** Prompt v1.4.3. On the 462-record EPPI seed (Claude Sonnet 4 + GLM-5.2,
> k=5, critic = Mistral Large): sensitivity 0.943, κ 0.719, ECE 0.081 (κ and ECE PASS,
> sensitivity just below the 0.95 threshold). See `reports/metrics.json` for the latest run.

---

## What's in this folder

| Path | Purpose |
|---|---|
| `prompts-screening-mhaa-unified-v1.4.md` | **Old copy of the prompt spec** (v1.4 header, content is v1.4.3 via the change log). Kept for the git history / runners that reference this filename. |
| `prompts/prompts-screening-mhaa-unified-v1.4.3.md` | **Current prompt spec** — primary screener (§1) + critic/adjudicator (§2) + calibration section. Identical content to the root copy; this is the canonical path for new runs. |
| `ingest.py` | Convert an Excel/CSV screening dataset into `records_<n>.jsonl` + `gt_<n>.json` for the runner. |
| `k5_runner.py` | **Main runner.** k-sampled screening via OpenRouter, per-model + cross-model aggregation, §2 critic adjudication on flagged records, verbatim-quote validation with re-prompt, and calibration (ECE / Brier / κ / sensitivity / per-model + inter-model breakdown). |
| `merge_results.py` | Merge stored Claude runs with a new GLM-only run file and re-aggregate (no new API calls). |
| `run_critic.py` | Re-run only the §2 critic on flagged records in an existing results JSONL (parallel). |
| `_inspect_fp.py` | Throwaway inspection script for a single record (git-ignored pattern `_*.py`). |
| `requirements.txt` | Python dependencies. |
| `.env` | `OPENROUTER_API_KEY` + OpenRouter HTTP headers (**git-ignored**). |
| `data/ground_truth.xlsx` | Source screening dataset (EPPI export). |
| `data/records_462.jsonl` | Ingested records for the 462-record seed. |
| `data/gt_462.json` | Ground-truth labels (`record_id → EPPI TAS decision string`) for the 462 seed. |
| `data/records_{10,10fn,13fn}.jsonl` | Smaller debug subsets (10-record smoke test; 10/13 false-negative slices). |
| `data/output/*.jsonl` | Aggregated per-record results from past runs (git-ignored). |
| `reports/` | Calibration outputs: `metrics.json`, `confusion_matrix.png`, `reliability_diagram.png`, `errors.jsonl` (git-ignored). |
| `.kilo/` | Kilo CLI config (Agent Manager state only). |

> Note on the two prompt files: `Compare-Object` on their contents returns **IDENTICAL**.
> The root file's header still says "v1.4" but its body and change log are v1.4.3 (see the
> change-log appendix). The `prompts/` copy is simply renamed to v1.4.3. Either path works
> with `--prompt`; prefer the `prompts/` one for new runs.

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
python ingest.py --input data/ground_truth.xlsx --out-dir data
```

Produces `data/records_<N>.jsonl` and `data/gt_<N>.json`. For a CSV or a different column
layout, see the override flags in `ingest.py --help`.

### 2. Run k-sampled screening

k=5 sampled runs per record, per model, at temperature > 0. Two model families are run in
parallel for cross-model consensus; flagged records are sent to the critic.

```powershell
python k5_runner.py `
    --prompt prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records data/records_462.jsonl `
    --gt data/gt_462.json `
    --out data/output/results_k5_462.jsonl `
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
python k5_runner.py --calibrate data/output/results_k5_462.jsonl --gt data/gt_462.json
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
python k5_runner.py `
    --prompt prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records data/records_462.jsonl --gt data/gt_462.json `
    --out data/output/results_glm_462.jsonl `
    --k 5 --temperature 0.5 `
    --models z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --workers 5

# b) Merge Claude runs (from the old file) + GLM runs (from the new file), re-aggregate
python merge_results.py `
    --old data/output/results_k5_462.jsonl `
    --new data/output/results_glm_462.jsonl `
    --records data/records_462.jsonl `
    --out data/output/results_merged_462.jsonl `
    --uncertainty-band 0.4 0.6 `
    --claude-model anthropic/claude-sonnet-4 `
    --glm-model z-ai/glm-5.2

# c) Re-run the critic on the merged verdict's flagged records
python run_critic.py `
    --prompt prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records data/records_462.jsonl `
    --in data/output/results_merged_462.jsonl `
    --out data/output/results_critic_462.jsonl `
    --critic-model mistralai/mistral-large `
    --temperature 0.5 `
    --workers 15

# d) Calibrate the merged + critic-adjudicated file
python k5_runner.py --calibrate data/output/results_critic_462.jsonl --gt data/gt_462.json
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

## Prompt versioning (v1.4 → v1.4.3)

The prompt spec is a single document with two roles:

- **§1 `mhaa.screening.ta`** — primary title/abstract screener. Hierarchical exclusion
  codes applied in order (1 language → 2 year → 3 population → 4 topic → 5 evidence type
  → 6 duplicate → 7 INCLUDE_TA), first failing code wins. v1.4 added the **AI-component
  positive test** on Code 4 (digital/mHealth-only without an explicit AI/ML signal →
  EXCLUDE_TOPIC). v1.4.1–v1.4.3 refined the age-overlap rule, added the MH-primary test +
  governance carve-out, and tightened evidence-type exclusions.
- **§2 `mhaa.screening.critic`** — second-opinion adjudicator, invoked only on flagged
  records. Re-screens from scratch, then `confirm`/`override`.

See the **Appendix C — Change log** at the bottom of the prompt file for the version
history and the per-version calibration on the 462-record seed.

---

## Quick commands

```powershell
# Smoke test on the 10-record subset
python k5_runner.py --prompt prompts/prompts-screening-mhaa-unified-v1.4.3.md `
    --records data/records_10.jsonl --gt data/gt_10.json `
    --out data/output/results_10.jsonl `
    --k 5 --temperature 0.5 --models z-ai/glm-5.2 --workers 5

# Re-calibrate any existing results file without re-running models
python k5_runner.py --calibrate data/output/results_v143_critic_462.jsonl --gt data/gt_462.json
```
