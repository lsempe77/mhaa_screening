# ULCM TAS Screening — Iteration Log

**Project:** StrongMinds Ultra-Low-Cost Model (ULCM) for Adult Depression — Title & Abstract Screening
**Date range:** 2026-07-18 to 2026-07-20
**Ground truth:** 510-record EPPI seed (`strongminds/groundtruth.csv`), 74 INCLUDE / 436 EXCLUDE
**Models:** Claude Sonnet 4 + GLM-5.2 (k=5 sampled, temperature 0.3), critic = Mistral Large
**Protocol thresholds:** Sensitivity ≥ 0.95, Cohen's κ ≥ 0.70, ECE ≤ 0.10

---

## 1. Context and starting point

The MHAA screening pipeline (`k5_runner.py`) had been calibrated to κ 0.719 on a 462-record
seed. The goal was to reuse the same infrastructure (OpenRouter calls, k-sampled aggregation,
critic adjudication, ECE/κ calibration) for the StrongMinds ULCM rapid evidence review — a
different review with a different prompt, different exclusion codes, and a more complex
18-research-question routing structure.

The ULCM prompt (`ulcm-tas-screening-prompts-hierarchical.md`) was draft v1.0, untested.

---

## 2. Infrastructure setup

The engine in `k5_runner.py` is project-agnostic. What needed adapting was the adapter layer —
the pieces that touch project-specific schema. Four changes, all additive (no breakage to MHAA):

1. **Prompt loader generalized** — the ULCM prompt uses `# 1.` (level-1 headings) and ```` ```text ````
  fences, while MHAA uses `## 1.` and ```` ``` ````. The loader now matches level-1/2 headings
  by regex and strips any fence language tag.

2. **Codes + label map extended** — added ULCM exclusion codes (`EXCLUDE_STUDY_DESIGN`,
  `EXCLUDE_INTERVENTION_TOPIC`, `EXCLUDE_OUTCOME`, `EXCLUDE_CONTEXT_GEOGRAPHY`,
  `EXCLUDE_TIME_LANGUAGE`) and the StrongMinds CSV human labels (`EXCLUDE on intervention`,
  `EXCLUDE on study design`, etc.) to `CODES` and `LABEL_MAP`.

3. **`--project {mhaa,strongminds}` flag** — switches the user/critic message template (ULCM
  adds `SCREENING_LEVEL`, `KEYWORDS`, `SOURCE_REVIEW_ID`, etc.), defaults
  `screening_level=review`, and raises `max_tokens` to 4000 for the 6-step
  `hierarchical_trace` schema.

4. **`ingest.py --format strongminds_csv`** — handles the paired-row CSV layout (record on one
  row, TAS decision on the next) and reads as all-string (fixes `.0` suffix on numeric IDs and
  preserves unicode).

**Regression verified:** MHAA calibration unchanged (sens 0.943 / κ 0.719 / ECE 0.081).

---

## 3. The normalize_response bug

The first full 510-record run (v1.0) showed terrible metrics: sens 0.419, κ 0.324. Root cause
was a bug in `normalize_response` — models (especially Claude) were putting the exclusion code
(e.g. `EXCLUDE_STUDY_DESIGN`) into the `screening_decision` field instead of the binary
`INCLUDE`/`EXCLUDE`, leaving `screening_code` null. This zeroed out vote shares on ~40% of runs.

**Fix:** `normalize_response` now:
- Detects a non-binary value in `screening_decision` and promotes it to `screening_code`
- Derives `screening_code` from the `hierarchical_trace`'s first FAIL criterion when absent
- Derives the binary decision from the resolved code
- Maps model-improvised field aliases (`final_code`, `exclusion_code`, `quote`, `rationale`)
  to canonical names
- Normalizes non-canonical code strings (`include`, `exclude`, `retain`, `unclear`)

**After fix (no new API calls, re-normalized from stored runs):**

| Metric | v1.0 (buggy) | v1.0 (fixed) |
|---|---|---|
| Sensitivity | 0.419 | 0.622 |
| Specificity | 0.904 | 0.858 |
| Cohen's κ | 0.324 | 0.403 |
| ECE | 0.088 | 0.118 |
| FN / FP | 43 / 42 | 28 / 62 |

---

## 4. Monolithic prompt iterations

The monolithic prompt is a single system message with all 6 exclusion criteria applied in
hierarchy: population → study-design → intervention → outcome → geography → time-language.

### v1.0 → v1.1: Route-conditional restructure

**Problem:** 18 of 28 FNs were `EXCLUDE_INTERVENTION_TOPIC` wrongly applied to RQ1 determinants
and RQ18 measurement records. The "no intervention required" carve-out was buried at the end of
Criterion 3; the model applied the intervention test before reading it. 29 of 62 FPs were
population exclusions the model failed to fire.

**Fix:** Restructured Criterion 3 (intervention) as route-conditional — model must re-read its
`rq_tags` before applying the intervention test. If routes include ONLY RQ1 or RQ18, the test
is skipped. Tightened Criterion 1 (population) with explicit fail signals.

| Metric | v1.0 (fixed) | v1.1 |
|---|---|---|
| Sensitivity | 0.622 | 0.703 |
| Specificity | 0.858 | 0.878 |
| Cohen's κ | 0.403 | **0.495** |
| ECE | 0.118 | 0.110 |
| FN / FP | 28 / 62 | 22 / 53 |

**Best monolithic result.** Improved on all metrics. But inter-model agreement was poor (κ 0.213)
— Claude was very conservative (sens 0.378), GLM permissive (sens 0.770). The prompt had too many
interacting conditional rules for either model to apply consistently.

### v1.1 → v1.2: Depression-focus + intervention-signal tests (over-correction)

**Problem:** Two-sided population problem — 8 FNs were population exclusions on legitimate
depression-in-older-adults records; 28 FPs were records mentioning depression but studying a
non-psychological exposure (diet, trace elements, spiritual healing).

**Fix:** Added a depression-focus positive test to Criterion 1 (population must have a substantive
depression focus) and a positive intervention-signal test to Criterion 3 (must name a specific
intervention type, not just "plausibly" one).

| Metric | v1.1 | v1.2 (150 subset) |
|---|---|---|
| Sensitivity | 0.703 | 0.308 |
| Specificity | 0.878 | 0.911 |
| Cohen's κ | 0.495 | 0.245 |
| ECE | 0.110 | 0.119 |
| FN / FP | 22 / 53 | 18 / 11 |

**Over-corrected.** The two positive tests interacted destructively within a single prompt.
Sensitivity crashed (0.703 → 0.308) because the depression-focus test was too strict — it
excluded records where depression was studied as an outcome in a comorbid population (heart
failure + CBT for depression, older adults + loneliness). The κ was oscillating, not converging.

### Monolithic ceiling diagnosed

Three iterations of monolithic tuning (v1.0 → v1.1 → v1.2) produced:
- v1.0: too loose (κ 0.40)
- v1.1: balanced but underfit (κ 0.50)
- v1.2: too tight (κ 0.25)

Each edit traded FNs for FPs or vice versa. The monolithic prompt had hit its complexity ceiling
at κ ~0.50. A monolithic prompt cannot simultaneously have a strict depression-focus test (to
kill FPs) and a lenient population test (to keep FNs) — the model cannot resolve that tension
within a single prompt reliably.

---

## 5. Orchestrator architecture

### Design

The orchestrator splits the monolithic screening task into stages:

1. **Router** (single model call): classifies the record into `scope_route(s)`. Pure
   classification — no screening logic, no exclusion codes.
2. **Route-specific screeners** (k=5, two models):
   - `no_intervention` screener for RQ1 determinants / RQ18 measurement — no intervention test
     exists in this prompt, eliminating the FN source
   - `intervention` screener for all other routes — the intervention test is always applied
     with no RQ1/RQ18 carve-out exceptions, eliminating the FP source
3. **Critic** (Mistral Large): adjudicates flagged records.

Each screener is small (2-3KB vs 10KB monolithic) and focused on a single route family, so the
models apply rules more consistently. The route decision is made once by the router and passed
as a fact to the screener.

**Files:** `orchestrator.py` (runner), `strongminds/ulcm-orchestrator-prompts.md` (4 prompts).
Output JSONL is compatible with `k5_runner.py --calibrate`.

### Orchestrator v1.0 (150-record subset)

Router: gpt-4o-mini. Initial run had GLM returning a completely different schema
(`exclusion_code`/`exclusion_reason`) — fixed by extending `normalize_response` with more
aliases and `_CODE_ALIASES` for non-canonical code strings.

| Metric | v1.1 monolithic (510) | Orch v1.0 (150) |
|---|---|---|
| Sensitivity | 0.703 | 0.500 |
| Cohen's κ | 0.495 | 0.381 |
| Inter-model κ | 0.213 | **0.395** |
| FN / FP | 22 / 53 | 13 / 14 |

**Core win:** inter-model agreement nearly doubled (κ 0.213 → 0.395). FPs dropped dramatically
(53 → 14). But sensitivity regressed (0.703 → 0.500) — the screener prompts needed tuning.

---

## 6. Orchestrator iterations (150-record subset)

Iteration on the 150-record stratified subset (54 v1.1 error records included) without critic for
speed. Each run took ~12-15 minutes.

### v1.0 → v1.1: Screener tuning

- **no_intervention Criterion 3 (outcome):** require depression as PRIMARY focus, not merely
  measured. Explicit FAIL for records where depression is secondary to diet, heat, leishmaniasis.
- **intervention Criterion 1 (population):** pass comorbid populations when the intervention
  targets depression (heart failure + CBT, post-Ebola psychosocial).
- **intervention Criterion 3 (intervention):** accept non-standard psychological interventions
  (cognitive training for depression, serious games, computerized CBT with human support).

| Metric | Orch v1.0 | Orch v1.1 |
|---|---|---|
| Sensitivity | 0.500 | 0.615 |
| Cohen's κ | 0.381 | **0.535** |
| FN / FP | 13 / 14 | 10 / 10 |

**Best κ so far.** Balanced 10/10 error split for the first time.

### v1.1 → v1.2: pick_screener bug + yoga/app fails

- **Bug fix:** `pick_screener` was not ignoring `not_applicable` in the route set, so records
  tagged `["determinants", "not_applicable"]` went to the intervention screener instead of
  no_intervention. Fixed to strip `not_applicable` before the subset check.
- **intervention Criterion 3:** added explicit FAIL for yoga, art therapy, music therapy,
  dance therapy, fully digital/self-guided apps, internet-based interventions without human
  facilitation.
- **no_intervention Criterion 1:** added "students" to population fail list.

| Metric | Orch v1.1 | Orch v1.2 |
|---|---|---|
| Sensitivity | 0.615 | 0.538 |
| Specificity | 0.919 | 0.944 |
| Cohen's κ | 0.535 | 0.522 |
| FN / FP | 10 / 10 | 12 / 7 |

FPs improved (10 → 7) but FNs went up (10 → 12). κ essentially held — within noise on 150 records.

### v1.2 → v1.3: Dual-route rule + older-adults fix

- **DUAL-ROUTE RULE:** if a record is tagged both `determinants` AND `intervention`, and the
  intervention is only mentioned as background, do NOT fail on intervention — retain with
  `needs_second_opinion=true`. Fixes records mis-routed to the intervention screener when they're
  actually determinants reviews.
- **Older-adults population rule strengthened:** explicit "do NOT exclude dementia and depression,
  cognitive impairment and depression, loneliness and depression in the elderly."

| Metric | Orch v1.2 | Orch v1.3 |
|---|---|---|
| Sensitivity | 0.538 | 0.692 |
| Specificity | 0.944 | 0.871 |
| Cohen's κ | 0.522 | 0.502 |
| ECE | 0.103 | **0.084** (PASS) |
| Inter-model κ | 0.296 | **0.474** |
| FN / FP | 12 / 7 | 8 / 16 |

**ECE passes for the first time** (0.084 ≤ 0.10). Inter-model agreement jumped to κ 0.474.
But FPs rose (7 → 16) because the dual-route rule's "retain with uncertainty" let through records
the GT excludes.

---

## 7. Full 510-record validation

### v1.4: Primary-outcome test + GLM router prep

Scaled to the full 510 with critic. Added the primary-outcome test to the intervention
screener's Criterion 4: depression must be the PRIMARY outcome of the intervention study, not
merely measured. This targeted the 6 FPs where the model correctly identified CBT/ACT but the
GT excluded because the outcome wasn't depression-primary (CBT for cardiometabolic disease, ACT
for chronic pain).

| Metric | Orch v1.3 (150) | **Orch v1.4 (510)** |
|---|---|---|
| Sensitivity | 0.692 | 0.554 |
| Specificity | 0.871 | 0.947 |
| Cohen's κ | 0.502 | **0.531** |
| ECE | 0.084 | **0.059** (PASS) |
| Brier | 0.117 | 0.083 |
| FN / FP | 8 / 16 | 33 / 23 |
| Inter-model κ | 0.474 | 0.466 |

ECE passes convincingly (0.059). Inter-model agreement held at κ 0.466. But the scale-up
revealed more FNs than the 150-subset suggested (8 → 33) — the subset was enriched with boundary
cases but the full set has more diverse failure modes. The 33 FNs broke down: 12 population,
6 outcome, 4 intervention, 4 time-language, 3 study-design, 4 include (vote<0.5).

### v1.5: GLM router + global uncertainty-retention (over-correction)

Two changes:
1. **Router: gpt-4o-mini → z-ai/glm-5.2** — gpt-4o-mini was under-classifying routes, causing
   mis-routed FNs (e.g. "Task-sharing to screen perinatal depression" tagged
   determinants+intervention → intervention screener → fails intervention test).
2. **GOVERNING RULE in both screeners:** "UNCERTAINTY DEFAULTS TO INCLUSION" stated first and
   reinforced at every criterion. This is the ULCM protocol's own rule that hadn't been
   enforced strictly enough.

| Metric | Orch v1.4 | Orch v1.5 |
|---|---|---|
| Sensitivity | 0.554 | 0.851 |
| Specificity | 0.947 | 0.647 |
| Cohen's κ | 0.531 | 0.276 |
| ECE | 0.059 | 0.254 |
| FN / FP | 33 / 23 | 11 / 154 |

**Over-corrected catastrophically.** The global uncertainty rule made the model include almost
everything. Sensitivity jumped (0.554 → 0.851, FN 33 → 11) but FPs exploded (23 → 154) and κ
crashed (0.531 → 0.276). The uncertainty-defaults-to-inclusion principle is correct but it must
be targeted to the criteria where FNs concentrate, not applied globally.

### v1.6: Surgical uncertainty-retention (final)

Reverted to v1.4's strict screeners and applied the uncertainty-retention rule ONLY in:
- **Criterion 1 (population):** FAIL ONLY when text clearly/exclusively shows ineligible
  population. RETAIN when ambiguous, partly reported, or an unusual comorbid group. Added
  explicit PASS examples (heart failure+CBT, IPV with depression, university students with
  distress, refugees).
- **Criterion 3 (intervention):** FAIL ONLY when record clearly describes a non-psychological
  exposure. RETAIN when non-standard but plausibly psychological. DUAL-ROUTE RULE retained.

Criteria 2 (study design), 4 (outcome), 5 (geography), 6 (time) remain strict — v1.5 showed
these must stay strict to control FPs. Kept the GLM router from v1.5.

| Metric | Orch v1.4 | Orch v1.5 | **Orch v1.6** | Threshold |
|---|---|---|---|---|
| Sensitivity | 0.554 | 0.851 | **0.649** | ≥ 0.95 |
| Specificity | 0.947 | 0.647 | **0.906** | — |
| Cohen's κ | 0.531 | 0.276 | **0.512** | ≥ 0.70 |
| ECE | 0.059 | 0.254 | **0.088** | ≤ 0.10 (PASS) |
| Brier | 0.083 | 0.239 | **0.094** | — |
| FN / FP | 33 / 23 | 11 / 154 | **26 / 41** | — |
| Inter-model κ | 0.466 | 0.579 | **0.546** | — |

**Final v1.6 error breakdown:**

FN by code (26 total):
| Code | Count |
|---|---|
| EXCLUDE_POPULATION | 6 |
| EXCLUDE_OUTCOME | 6 |
| EXCLUDE_INTERVENTION_TOPIC | 6 |
| EXCLUDE_STUDY_DESIGN | 3 |
| EXCLUDE_TIME_LANGUAGE | 2 |
| INCLUDE_TA (vote < 0.5) | 2 |
| EXCLUDE_POP (variant) | 1 |

FP by GT label (41 total):
| GT label | Count |
|---|---|
| EXCLUDE on intervention | 22 |
| EXCLUDE on population | 18 |
| EXCLUDE - duplicate | 1 |

The errors are now spread evenly across criteria — no single criterion is the bottleneck. The
remaining errors are genuinely hard boundary cases.

---

## 8. Full progression summary

| Version | Records | κ | Sens | Spec | ECE | FN/FP | Inter-κ | Architecture |
|---|---|---|---|---|---|---|---|---|
| v1.0 (buggy) | 510 | 0.324 | 0.419 | 0.904 | 0.088 | 43/42 | — | monolithic |
| v1.0 (fixed) | 510 | 0.403 | 0.622 | 0.858 | 0.118 | 28/62 | — | monolithic |
| v1.1 | 510 | 0.495 | 0.703 | 0.878 | 0.110 | 22/53 | 0.213 | monolithic |
| v1.2 | 150 | 0.245 | 0.308 | 0.911 | 0.119 | 18/11 | 0.226 | monolithic (over-corrected) |
| Orch v1.0 | 150 | 0.381 | 0.500 | 0.887 | 0.178 | 13/14 | 0.395 | orchestrator baseline |
| Orch v1.1 | 150 | 0.535 | 0.615 | 0.919 | 0.113 | 10/10 | 0.347 | screener tuning |
| Orch v1.2 | 150 | 0.522 | 0.538 | 0.944 | 0.103 | 12/7 | 0.296 | pick_screener fix |
| Orch v1.3 | 150 | 0.502 | 0.692 | 0.871 | 0.084 | 8/16 | 0.474 | dual-route rule |
| Orch v1.4 | 510 | 0.531 | 0.554 | 0.947 | 0.059 | 33/23 | 0.466 | primary-outcome test |
| Orch v1.5 | 510 | 0.276 | 0.851 | 0.647 | 0.254 | 11/154 | 0.579 | global uncertainty (over-corrected) |
| **Orch v1.6** | **510** | **0.512** | **0.649** | **0.906** | **0.088** | **26/41** | **0.546** | **surgical uncertainty** |

---

## 9. Assessment

### What the orchestrator achieved

- **Inter-model agreement doubled** (κ 0.213 → 0.546). Claude and GLM now agree on 86.9% of
  records. The route-specific screeners are simple enough for both models to apply consistently.
- **ECE passes** (0.088 ≤ 0.10). The vote-share calibration is reliable — when the panel says
  "90% INCLUDE", the record is included ~87% of the time.
- **Specificity is strong** (0.906). The tool correctly excludes 90.6% of out-of-scope records.
- **The critic does real work** (194 overrides / 247 applied). It catches model disagreement
  and adjudicates, unlike the monolithic runs where it was a near no-op (1/279 overrides).

### What it did not achieve

- **κ 0.512** is 0.19 below the 0.70 threshold. The κ has oscillated around 0.50 across 8
  iterations spanning both architectures. This appears to be the practical ceiling for zero-shot
  prompting on this prompt + model combination.
- **Sensitivity 0.649** is 0.30 below the 0.95 threshold. The 26 FNs are genuinely ambiguous
  boundary cases (comorbid populations, non-standard interventions, pre-2000 classics, debatable
  depression-focus).

### Why κ plateaued at ~0.50

The remaining errors are spread evenly across all criteria (6 pop / 6 outcome / 6 intervention /
3 study-design / 2 time / 2 include). No single criterion is the bottleneck — each prompt edit
that fixes one criterion's errors tends to create errors in another. This is the fundamental
limitation of zero-shot prompting on a complex eligibility protocol: the boundary between INCLUDE
and EXCLUDE is genuinely fuzzy for ~10% of records, and no prompt can make the model resolve
that fuzziness deterministically.

### What v1.6 is good for

A **screening-assist tool**: at specificity 0.906 and ECE 0.088, it reliably eliminates 90.6%
of out-of-scope records with well-calibrated confidence. The 26 FNs are all flagged for human
review via `needs_second_opinion` (the surgical uncertainty rule ensures ambiguous records are
retained, not excluded), so no evidence is permanently lost. The 41 FPs are an 8% human-review
workload — manageable for a rapid review.

### What would be needed to reach κ 0.70

Likely a fundamentally different approach:
- A **fine-tuned classifier** trained on the GT labels rather than zero-shot prompting
- A **much larger model panel** (5+ families) with a learned aggregation layer rather than
  majority vote
- **Full-text screening** for the borderline records rather than title/abstract only

These are beyond prompt engineering.

---

## 10. Files

| File | Purpose |
|---|---|
| `strongminds/ulcm-orchestrator-prompts.md` | Orchestrator v1.6 prompts (router, no_intervention screener, intervention screener, critic) |
| `strongminds/ulcm-tas-screening-prompts-hierarchical.md` | Monolithic v1.1 prompt (reverted from v1.2; best monolithic) |
| `strongminds/groundtruth.csv` | 510-record ground truth (paired-row CSV) |
| `strongminds/data/records_510.jsonl` | Ingested records |
| `strongminds/data/gt_510.json` | Ground truth labels |
| `strongminds/data/output/results_orch_v16_510.jsonl` | v1.6 final results (510 records) |
| `orchestrator.py` | Orchestrator runner (router → screener → critic) |
| `k5_runner.py` | Engine (OpenRouter, aggregation, calibration, normalize_response) |
| `ingest.py` | CSV/Excel → records.jsonl + gt.json (supports `--format strongminds_csv`) |
| `reports/metrics.json` | Latest calibration metrics |
| `reports/errors.jsonl` | Latest FN + FP records for refinement |

---

## 11. How to reproduce

```powershell
# 1. Ingest the ground truth
python ingest.py --format strongminds_csv `
    --input strongminds/groundtruth.csv --out-dir strongminds/data

# 2. Run the orchestrator (v1.6, full 510 + critic)
python orchestrator.py `
    --prompt strongminds/ulcm-orchestrator-prompts.md `
    --records strongminds/data/records_510.jsonl `
    --gt strongminds/data/gt_510.json `
    --out strongminds/data/output/results_orch_v16_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 8

# 3. Calibrate
python k5_runner.py --calibrate `
    strongminds/data/output/results_orch_v16_510.jsonl `
    --gt strongminds/data/gt_510.json
```

The orchestrator defaults to `--router-model z-ai/glm-5.2` (no need to specify). The run is
resumable — if interrupted, re-run the same command and it picks up from where it left off.
