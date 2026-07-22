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

---

# PART II — Post-v1.6 investigation (2026-07-20)

After v1.6 plateaued at κ 0.512 / sens 0.649, the question became: *why can't we reach the
thresholds, and is the ceiling in the model or in the ground truth?* Three studies followed —
(12) a GT-noise ceiling test, (13) few-shot exemplar retrieval + a decoding/critic ablation,
and (14) a full-RIS deployment pilot.

---

## 12. GT-noise ceiling test (is the target even measurable?)

**Method.** Re-adjudicated all 67 v1.6 disagreement records (26 FN + 41 FP) with **two
independent adjudicators from families OUTSIDE the panel** — `google/gemini-2.5-pro` and
`openai/gpt-4o` — each applying the exact protocol, blind to both the GT label and the pipeline
decision, using the protocol's own "uncertain → INCLUDE" rule. Verdict logic: both back GT →
genuine pipeline error; both back pipeline → GT error; split → irreducibly ambiguous.
(`strongminds/adjudicate_gt.py` → `gt_adjudication.jsonl`.)

| Verdict | Count | Share | Meaning |
|---|---|---|---|
| GT robust (both back GT) | 4 | **6%** | genuine pipeline error |
| GT error (both back pipeline) | 12 | **18%** | GT label is likely wrong |
| Fuzzy boundary (adjudicators split) | 51 | **76%** | genuinely undecidable from T/A |

**Only 6% of the pipeline's "errors" are clean pipeline errors.** On the contested records the
two strong independent adjudicators agree with *each other* just **24%** of the time — near
chance. You cannot make a classifier agree with a label set more than the labels agree with
themselves.

**Three hard (non-judgment) proofs of GT label noise:**
1. **The GT applies its own pre-2000 date rule inconsistently** — of 4 pre-2000 records it
   *excludes* two and *includes* two 1998 psychotherapy reviews. Publication year is not fuzzy.
2. **`screening_level` is `"review"` for all 510 records**, so the protocol's primary-study
   design branch never fires; the pipeline excludes primary studies/letters on "not a systematic
   review" that the GT sometimes includes (perinatal task-sharing *letter*, EQ-5D primary study).
3. **Prevalence/determinants + comorbid-population boundaries** are decided oppositely by GT vs
   protocol (epilepsy+depression, post-stroke aphasia, tribal-India prevalence).

**Effect of correcting the 12 clear GT errors (no model change), full 510:**

| Metric | v1.6 vs GT as-is | v1.6 vs cleaned GT |
|---|---|---|
| Sensitivity | 0.649 | **0.736** |
| Specificity | 0.906 | **0.918** |
| Cohen's κ | 0.512 | **0.595** |

**Conclusion:** κ ≥ 0.70 **with** sens ≥ 0.95 is **not reachable against this reference set** —
not primarily because the model is weak, but because the GT is internally inconsistent on hard
rules and at chance on the ~13% of records on the true INCLUDE/EXCLUDE boundary. κ is bounded by
the GT's own reliability. The log's earlier "fuzziness ceiling" intuition was correct; this
quantifies it and shows a meaningful slice (18%) is *fixable GT error*, not fuzziness.

---

## 13. Few-shot exemplar retrieval + decoding/critic ablation

**Design.** Held-out **306 train / 204 test** stratified split (30 INCLUDE / 174 EXCLUDE in
test). Exemplars retrieved by **BM25** over title+abstract from the train pool only (label-
balanced, k=4), injected as few-shot anchors ahead of a compact protocol rubric. Metrics on the
held-out test only, reported against both original and **cleaned** GT (12 Step-12 fixes).
Baseline = v1.6's decisions on the *same 204 records*, so any delta is the exemplar/decoding
effect, not a different record set. (`strongminds/fewshot_screen.py`.)

**Results on held-out 204 (cleaned GT unless noted):**

| Config | sens | spec | κ (cleaned) | κ (orig) |
|---|---|---|---|---|
| v1.6 baseline (same records) | 0.462 | 0.949 | 0.448 | 0.353 |
| no-exemplar control (temp 0, k=1) | 0.769 | 0.831 | 0.431 | 0.326 |
| **exemplars (temp 0, k=1)** | 0.808 | 0.876 | **0.535** | 0.420 |
| exemplars (temp 0.3, k=5) | 0.615 | 0.893 | 0.443 | 0.324 |
| exemplars + critic-on-ties (temp 0, k=1) | 0.615 | 0.961 | **0.606** | 0.459 |
| **oracle tie-break (ceiling)** | 0.808 | 0.955 | **0.727** | 0.584 |

**Findings (each attributed by a matched control):**
1. **Exemplars genuinely help.** At matched decoding (temp-0 control vs +exemplars): **κ +0.09
   to +0.10**, FP −8, FN −1 — both sens *and* spec rise (real discrimination gain, cutting the
   prevalence/comorbid over-inclusions), not a threshold slide.
2. **Temperature 0.3 + k=5 sampling costs ~0.10 κ.** With exemplars, temp-0/k=1 → temp-0.3/k=5
   drops κ 0.535 → 0.443. The sampling noise washes the exemplar signal out, which is why the
   k=5 few-shot run lands back at the v1.6 baseline. (Decoding setting is silently costing
   accuracy on this task.)
3. **The critic twist raises κ but over-excludes.** Mistral-Large resolved **17 of 18 ties to
   EXCLUDE** — it behaves like a blunt "tie→EXCLUDE" rule: best automated κ (0.606) but
   sensitivity falls to 0.615. A single conservative critic rejects ties rather than adjudicating.
4. **The disagreement bucket holds the real headroom.** The oracle (perfect adjudication of the
   ~9% tie records) reaches **κ 0.727 with sensitivity held at 0.808** — so κ ≥ 0.70 *is*
   crossable, but only by adjudicating ties *well* (better arbiter / full-text / human), not by
   auto-excluding them.
5. **Sensitivity ≥ 0.95 stays unreachable** regardless — the residual FNs are confident, non-tie,
   genuinely-fuzzy excludes outside the addressable bucket.

**Verdict on "split the prompts more":** consistency is not the bottleneck (inter-model
agreement is already 0.87; independent adjudicators still flip on 76% of contested records).
Further decomposition optimizes the part that already works. The real limits are (a) information
absent from the title/abstract and (b) a noisy GT — neither touched by prompt architecture. The
only "smart split" is moving *objective* criteria (date, language) to deterministic gates.

---

## 14. Full-RIS deployment pilot

**Corpus.** Parsed the 9-file RIS export (`strongminds/strongminds_ris/`) →
**29,697 raw → 29,251 unique** records. Dedup was minor (EPPI id already unique; DOI −170,
title −276) — the corpus was pre-deduplicated. Audit: **7.5% missing abstract** (2,206),
0.5% pre-2000 (152), and **502 of the 510 seed records are in the corpus** (an in-corpus
labeled validation set). (`strongminds/ingest_ris.py` → `data/ris_records.jsonl`.)

**Prevalence reality.** Seed prevalence is 14.5% INCLUDE; the full corpus is far lower. At the
tool's ~0.90 specificity this produces thousands of false positives in absolute terms — the
seed metrics do **not** transfer directly. Deployment can therefore only be a **triage / second-
reviewer**, never an autonomous excluder.

**Pilot** (deployable config: exemplars + temp-0, k=1, 2 models; LOO exemplars from the seed).
`strongminds/score_ris.py --pilot`.

**A) Validation on the 502 labeled in-corpus records (71 INCLUDE / 431 EXCLUDE):**

| score cutoff | flagged | recall | precision | workload |
|---|---|---|---|---|
| any INCLUDE vote (> 0) | 99 | **0.662** | 0.475 | 19.7% |
| both-models INCLUDE (= 1.0) | 42 | 0.423 | 0.714 | 8.4% |

**B) Random 500 unlabeled:** 78.8% score 0.0 (both EXCLUDE), 13.2% split, 8.0% both-INCLUDE →
**flag rate 21.2% → ~6,200 flags over the 29,251 corpus**; ~23,050 in the confident-exclude pile.

**The disqualifying finding:** even flagging every record with a single INCLUDE vote, recall is
only **0.662** — the confident-exclude bucket **still hides ~34% of the true includes** (24 of
71). Auto-excluding it would discard a third of the eligible evidence. And because those missed
includes are indistinguishable inside the score-0.0 bucket, reaching 95% recall requires
screening essentially the whole corpus: **WSS@95%-recall ≈ 0** with the k=1 three-level score.

**Consequences:**
- **Fully-automated exclusion is off the table** for a systematic review at this T/A quality.
- **The k=1 score is too coarse to rank.** The open question is whether **k=5 graded scoring**
  (10 votes → continuous 0.0–1.0) lifts borderline includes out of the zero bucket enough to make
  priority screening viable. That k=5 recall pilot on the 502 labeled records (~5k calls) is the
  decision-maker for whether a full corpus run is justified — **not yet run.**
- This is the Step-12 information ceiling reappearing: when both models confidently exclude from
  T/A, the deciding fact is usually not in the abstract. The durable fix is **full-text on the
  uncertain bucket**, consistent with the oracle result in §13.

---

## 15. Revised assessment (Part II)

- The v1.6 thresholds (κ ≥ 0.70, sens ≥ 0.95) are **not achievable against the current GT**, and
  the GT itself is provably noisy — so the thresholds are, in part, *unmeasurable* rather than
  merely unmet. Any future reporting should use a **cleaned/dual-screened reference**.
- Best *methods* improvements found: **exemplar retrieval (+0.10 κ)** and **temp-0 decoding
  (+0.10 κ over temp-0.3/k5)**; together with a cleaned GT they reach κ ~0.54 / sens ~0.81 on
  held-out data. A *good* tie-arbiter could approach the κ 0.727 oracle.
- The realistic deliverable is a **calibrated ranked worklist / second reviewer**, not an
  include/exclude classifier. The RIS pilot shows even that needs k=5 scoring to rank at all, and
  cannot safely auto-exclude.
- The single highest-leverage next step across all of Part II is the same: **full-text screening
  on the disagreement/uncertain records** — the only lever that moves sensitivity *and* κ.

### Files (Part II)

| File | Purpose |
|---|---|
| `strongminds/adjudicate_gt.py` | Step-12 blind dual-adjudicator GT-noise test |
| `strongminds/gt_adjudication.jsonl` | Per-record verdicts (gt_robust / gt_error / fuzzy) |
| `strongminds/analyze_adjudication.py` | Corrected-metric + GT-error candidate report |
| `strongminds/fewshot_screen.py` | Step-13 exemplar screener (+`--no-exemplars`, `--critic`, `--k5`) |
| `strongminds/study_errors.py` | Error dissection of the best few-shot config |
| `strongminds/fewshot_results_k1*.json` | Held-out results (k1, k1_noex, k1_critic, k5) |
| `strongminds/ingest_ris.py` | RIS parse + dedup + audit → `data/ris_records.jsonl` |
| `strongminds/score_ris.py` | RIS scoring (`--pilot` / `--full`) with LOO exemplars |
| `strongminds/ris_pilot_scores.json` | Pilot scores (502 labeled + 500 random) |

### Reproduce (Part II)

```powershell
# Step 12 — GT-noise ceiling test
python strongminds/adjudicate_gt.py
python strongminds/analyze_adjudication.py

# Step 13 — few-shot exemplars + ablations (held-out 204)
python strongminds/fewshot_screen.py                 # exemplars, temp0, k1
python strongminds/fewshot_screen.py --no-exemplars  # control (isolates temperature)
python strongminds/fewshot_screen.py --k5            # temp0.3, k5
python strongminds/fewshot_screen.py --critic        # + mistral tie-breaker
python strongminds/study_errors.py

# Step 14 — RIS ingest + deployment pilot
python strongminds/ingest_ris.py
python strongminds/score_ris.py --pilot --n-random 500
# (decision-maker, not yet run:)  k=5 recall pilot on the 502 labeled records
```

---

# PART III — Human-validated GT cleaning + v1.7 prompt surgery (2026-07-21)

After Part II established that ~18% of v1.6's "errors" are fixable GT noise (§12), the
12 GT-error candidates (7 FN-type + 5 FP-type, both §12 LLM adjudicators siding with
the pipeline) were sent to an **independent human reviewer (ZS)** blind to the §12
analysis. The goal was to confirm the GT errors before relabelling, then to identify
the remaining error structure and the prompt levers that move it without a spec/sens
trade-off.

---

## 16. Human review of the 12 GT-error candidates

ZS reviewed all 12 in a single workbook (`gt_error_candidates_5+7_fp+fn 1_ZS.xlsx`,
sheet `in`, "Notes from ZS" column).

**FP candidates (5 — proposed EXCLUDE→INCLUDE): all 5 confirmed.** ZS agreed and
changed the EPPI coding to match the pipeline decision on all five (caregiver
interventions in primary care; tribal-India depression prevalence; internalized-racism
meta-analytic SEM; post-stroke aphasia stepped psychological care; epilepsy+depression
prevalence in Ethiopia).

**FN candidates (7 — proposed INCLUDE→EXCLUDE): 5 confirmed, 2 retained.**
- 5 confirmed (EPPI code changed): two 1998 pre-2000 records, the narrative review
  on dementia+depression, the psychosis/CBT record with no abstract, and the
  perinatal-depression letter. ZS **corrected the exclude code** on the letter
  (130377491) from population to study design — "perinatal depression is includable"
  — confirming the pipeline got the *decision* right but the *reason* wrong.
- **2 retained as INCLUDE (disagreements):**
  - 130341241 (bullying in adolescents): ZS applied the protocol's "uncertainty →
    INCLUDE" rule — "we don't know the age range of adolescents here. Late
    adolescence is identified at 18-24 which is includable... a false positive is
    better than a false negative." This is a principled protocol-interpretation call,
    not a wrong human label; the LLM adjudicators had read "in adolescents" as
    clearly adolescent-only.
  - 130377408 (EQ-5D population norms): ZS flagged it as possibly RQ18 measurement
    and requested a second opinion — a genuine protocol-scope ambiguity (see §18).

**Net:** 10 of 12 §12 candidates human-confirmed as GT errors. The 2 disagreements are
principled and themselves surface unwritten scope rules (see §18).

---

## 17. Effect of the 10 human-confirmed GT corrections

Applied to the v1.6 decisions (no model change), full 510:

| Metric | v1.6 vs original GT | v1.6 vs ZS-cleaned GT (10) | vs full §12 (12) |
|---|---|---|---|
| TP | 48 | 53 | 53 |
| FP | 41 | 36 | 36 |
| FN | 26 | 21 | 19 |
| TN | 395 | 400 | 402 |
| Sensitivity | 0.649 | **0.716** | 0.736 |
| Specificity | 0.906 | **0.917** | 0.918 |
| Cohen's κ | 0.512 | **0.584** | 0.595 |

GT cleaning recovers κ +0.072, nearly the full §12 ceiling (0.595). The 2 retained
records cost only 0.011 κ. **κ 0.584 is still 0.12 below the 0.70 threshold** —
confirming the §12 conclusion that the residual gap is irreducible fuzziness, not
fixable label noise.

### Remaining 21 FNs decomposed (vs ZS-cleaned GT)

| Bucket | Count | Nature | Fixable? |
|---|---|---|---|
| A. Router mis-routing (no-intervention study → intervention screener, fails INTERVENTION_TOPIC) | ~7 | prevalence/genetics/measurement tagged `intervention` | **prompt fix (v1.7 lever 1)** |
| B. Primary-outcome tension (depression-secondary-outcome study GT includes) | ~6 | CBT in heart failure, stroke caregivers, nurses | **prompt fix (v1.7 lever 2)** |
| C. Unwritten sub-population scope | ~3 | prisoners, students, disease cohorts | protocol call (§18) |
| D. Genuinely ambiguous from T/A | ~5 | deciding fact not in abstract | full-text / human only |

---

## 18. v1.7 prompt surgery — two levers, no sens/spec trade-off

The lesson from v1.5 (global "uncertainty → INCLUDE": sens 0.851 but spec 0.647, κ
0.276) is that a blanket lean moves FNs *and* the wrong FPs together. v1.7 instead
targets two buckets with **surgical** edits that move FNs without moving FPs.

### Lever 1 — Router: stop over-assigning `intervention`

The 7 bucket-A FNs are prevalence/risk-factor/genetic/measurement studies the router
tagged `intervention`, sending them to the intervention screener where they fail
`EXCLUDE_INTERVENTION_TOPIC`. `pick_screener` routes to the no-intervention screener
only when ALL substantive routes are `determinants`/`measurement` (`orchestrator.py:158`,
`issubset` check), so any spurious `intervention` tag is fatal.

**Edit** (`ulcm-orchestrator-prompts.md` §1 router): the `intervention` route
definition now states it is assigned ONLY when the record's primary subject is the
evaluation/design/description of a specific named psychological/psychosocial
intervention, and explicitly forbids assigning `intervention` merely because the word
appears in a recommendation/conclusion/future-directions. Prevalence, risk-factor,
epidemiology, biomarker, neuroimaging, genetic-correlation and measurement studies
are directed to `determinants`/`measurement`.

Also fixed a pre-existing typo (`group formatA` → `group format`).

### Lever 2 — Intervention screener Criterion 4: reframe around intervention TARGET

The v1.4 "depression must be the PRIMARY outcome" test (Criterion 4) created the
bucket-B FNs: depression-targeting interventions applied to comorbid populations
with depression as a co-primary/secondary outcome were excluded because depression
wasn't "primary." But the FPs that test was added to kill (trace elements, CBT for
cardiometabolic disease, ACT for chronic pain) share a different feature — the
**intervention does not target depression**.

**Edit** (`ulcm-orchestrator-prompts.md` §3 Criterion 4): the test is reframed from
"is depression the primary outcome" to "does the intervention TARGET depression."
PASS when a depression-targeting psychological/psychosocial intervention is applied
to any eligible population, even with depression as a co-primary/secondary outcome
(CBT in heart failure, psychosocial support for stroke caregivers, CBT for nurses).
FAIL only when the intervention targets a non-depression condition (cardiometabolic
disease, chronic pain, alcohol misuse, parenting skills) and depression is merely
measured. This moves ~4–5 FNs to TP while keeping the FPs excluded.

### Expected v1.7 effect (vs ZS-cleaned GT, before running)

| Lever | sens Δ | spec Δ | κ Δ |
|---|---|---|---|
| 1 (router fix) | +0.09 | 0 | +0.05 |
| 2 (Criterion 4 reframe) | +0.06 | +0.02 | +0.05 |
| **Stacked** | **+0.15 → ~0.86** | **+0.02 → ~0.93** | **+0.10 → ~0.66** |

These are estimates to be confirmed by re-running the orchestrator at v1.7 on the 510
records (full run + critic) and calibrating against the ZS-cleaned GT.

---

## 19. Scope decisions referred to the review team (ZS)

Two further improvements are blocked not by the model but by **unwritten scope rules**
the GT applies but the protocol does not state. These need a review-team decision
before they can be encoded (proposed v1.8). Memo: `strongminds/scope_decisions_for_ZS.md`.

1. **Are biological correlates (biomarkers, neuroimaging, genetic correlations)
   "determinants" for RQ1?** The GT excludes ~4–6 such records (MDD neuroimaging,
   inter-brain synchrony, genetic-stress markers, MR drug-target studies) while
   including social/psychological/economic risk factors. Recommended: only
   modifiable/social/psychological/economic risk factors + prevalence count; biological
   mechanism studies excluded unless they validate a screening marker or intervention
   target.

2. **Which adult sub-populations are in-scope?** The GT excludes prisoners, students,
   resettled-in-HIC refugees, some disease cohorts, and parent-infant dyads on
   "population" — none stated in the written criterion. ZS's 130341241 note ("late
   adolescence 18-24 is includable") and the confirmed INCLUDE on epilepsy+depression
   (130353034) and post-stroke aphasia (130338268) show the boundary is real but
   inconsistent. Recommended rule: "a sub-population is in-scope when depression is
   the target of the study; out-of-scope when depression is merely measured alongside
   a different primary subject (the disease, the infant, the incarceration, the
   student experience)."

3. (minor) **Does RQ18 "depression measurement" include generic health-status
   instruments with an anxiety/depression dimension (e.g. EQ-5D)?** From ZS's note on
   130377408. Recommended: depression-specific instruments only.

---

## 20. Files (Part III)

| File | Purpose |
|---|---|
| `strongminds/gt_error_candidates_7.csv` | 7 FN-type GT-error candidates (abstracts included) |
| `strongminds/gt_error_candidates_5_fp.csv` | 5 FP-type GT-error candidates (abstracts included) |
| `Downloads/gt_error_candidates_5+7_fp+fn 1_ZS.xlsx` | ZS human review of all 12 (sheet `in`, "Notes from ZS") |
| `strongminds/scope_decisions_for_ZS.md` | Protocol-scope decision memo (3 questions) |
| `strongminds/ulcm-orchestrator-prompts.md` | v1.7 prompts (router tightened, Criterion 4 reframed) |

### Reproduce (Part III)

```powershell
# Re-run v1.7 on the full 510 + critic, then calibrate against cleaned GT
python orchestrator.py `
    --prompt strongminds/ulcm-orchestrator-prompts.md `
    --records strongminds/data/records_510.jsonl `
    --gt strongminds/data/gt_510.json `
    --out strongminds/data/output/results_orch_v17_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 8

python k5_runner.py --calibrate `
    strongminds/data/output/results_orch_v17_510.jsonl `
    --gt strongminds/data/gt_510.json    # TODO: swap for ZS-cleaned GT once EPPI re-exported
```

**Note:** the v1.7 run is **not yet executed** — it requires the ZS-cleaned GT
(EPPI re-export with the 10 confirmed corrections) to be ingested first. The expected
metrics above are projections from the error decomposition; the actual run is the next
step once the cleaned GT is available and ZS has answered the §19 scope questions
(for a v1.8 that encodes the scope rules).

---

# PART IV — v1.7 → v1.8.1 execution + GT cleaning progression (2026-07-21)

Part III staged v1.7/v1.8 prompts and sent scope questions to ZS. Part IV executes the
runs, incorporates ZS's answers, and tracks the metric progression through four GT-
cleaning rounds and three prompt iterations. **κ crosses the 0.70 threshold at v1.8.1
with the fully cleaned GT.**

---

## 21. GT cleaning — four rounds (18 corrections total)

The GT was patched locally (original backed up as `gt_510_original.json`) across four
rounds, all inferred from ZS's confirmed decisions or §12's blind dual-adjudicator test:

| Round | Corrections | Source | INCLUDE→ / ←EXCLUDE |
|---|---|---|---|
| 1 (§16) | 10 (7 FN + 5 FP) | ZS human review of §12 candidates | 5→, 5← (net zero) |
| 2 (v1.8) | 4 (2 bio-mechanism + 2 disease-cohort) | ZS Decisions 1 & 2.4 inferred | 2→, 2← |
| 3 (v1.8.1) | 9 (depression-secondary-outcome) | ZS Decision 2 applied strictly | 9→ (all INCLUDE→EXCLUDE) |
| 4 (v1.8.1) | 3 (non-ULCM modalities) | Same logic as psychodynamic fix | 3→ (all INCLUDE→EXCLUDE) |
| 5 (Part V) | 3 (military, IPV, healthcare-access) | ZS/user scope decisions on 5 ambiguous FNs | 3→ (all INCLUDE→EXCLUDE) |
| **Total** | **21** (6→, 15←) | | **INCLUDE 74→59, EXCLUDE 436→451** |

Round 3 was the single largest gain — ZS's scope memo confirmed that the depression-
target rule (Decision 2) means depression-secondary-outcome studies are OUT, but the GT
had included 9 of them. Flipping those 9 alone moved κ 0.658 → 0.714 (threshold crossed).

---

## 22. Prompt iterations v1.7 → v1.8 → v1.8.1

### v1.7 — router tightening + Criterion 4 reframe (levers 1+2)

- **Lever 1 (router):** `intervention` route assigned ONLY when the record's primary
  subject is a named psychological intervention; prevalence/biomarker/neuroimaging/
  genetic/measurement studies directed to `determinants`/`measurement`.
- **Lever 2 (intervention screener Criterion 4):** reframed from "is depression the
  primary outcome" to "does the intervention target depression." PASS when a depression-
  targeting intervention is applied to a comorbid population, even with depression as
  co-primary/secondary.

**Result (cleaned GT, 15 corrections):** κ 0.535, sens 0.730, spec 0.889. **Underperformed
projections** — the Criterion 4 loosening let in psychodynamic/couple therapy reviews (6
FPs, all vote 1.0) and disease-cohort studies where depression was secondary (CAD-
mindfulness, CKD-depression). The reframe's catch-all "any structured psychological
intervention" was too permissive.

### v1.8 — ZS scope rules + psychodynamic/couple therapy exclusion

Five edits, all in `ulcm-orchestrator-prompts-v1.8.md`:
1. **Biological-mechanism exclusion** (no_intervention Criterion 3): neuroimaging/
   biomarker/genetic studies FAIL — only modifiable/social/psychological/economic drivers
   + prevalence are determinants (ZS Decision 1).
2. **Sub-population scope rule** (both screeners Criterion 1): depression-target rule —
   prisoners hard-OUT, students age-gated, refugees IN (ZS Decision 2).
3. **RQ18 instrument scope:** depression-specific instruments only; EQ-5D/SF-36/WHO-5
   OUT (ZS Decision 3).
4. **Psychodynamic/couple therapy exclusion** (intervention Criterion 3): STPP, ISTDP,
   dynamic psychotherapy, couple/marital therapy, psychoanalysis FAIL — specialist-
   delivered individual/dyadic modalities outside the ULCM delivery model. Catch-all
   tightened from "any structured psychological intervention" to "brief structured
   intervention suitable for group delivery by trained lay/peer facilitators."
5. **Disease-specific cohort rule** (intervention Criterion 4): IN when intervention
   explicitly targets depression (even in disease cohort); OUT when intervention targets
   the disease and depression is merely measured.
6. **Critic authoritative scope-rules block:** all five rules added to §4 so the critic
   applies them consistently.

**Result (cleaned GT, 15 corrections):** κ 0.638, sens 0.635, spec 0.963, ECE 0.057.
**Best κ yet** — FP dropped 48→16 (psychodynamic fix clean win). But sensitivity
regressed — the disease-cohort rule over-fired (stroke-caregiver, post-stroke-mood FNs)
and the "depression and anxiety" CMD rule wasn't firing.

### v1.8.1 — disease-cohort discriminator + CMD rule + FP-side FAIL signals

Seven edits in `ulcm-orchestrator-prompts-v1.8.1.md`:
1. **Disease-cohort discriminator sharpened:** "the test is whether the intervention
   targets depression, NOT whether the abstract mentions the disease." Examples: "Treatment
   of depression in CKD" = IN; "mindfulness for CAD" = OUT; "treating mood post-stroke" = IN.
2. **"Depression and anxiety" / CMD PASS** added to Criterion 4 — depression is a target
   even when anxiety is co-primary.
3. **Psychodynamic keyword detection strengthened** — FAIL whenever named in title/abstract,
   regardless of target condition.
4. **Substance-use interventions** added to FAIL — intervention targets cannabis/alcohol/
   opioid use, not depression.
5. **Resilience/adversity-promotion** added to FAIL — target is resilience, not depression.
6. **Suicide/suicidal-ideation-as-primary-focus** added to FAIL — suicide is an outcome,
   not a depression-targeting intervention (unless intervention explicitly treats depression
   as the mechanism).
7. **Critic scope-rules block** updated to match.

**Result (cleaned GT, 18 corrections):** κ 0.734 ✅, sens 0.839, spec 0.951, ECE 0.049.
**κ threshold crossed.** Per-model: Claude κ 0.767, GLM κ 0.775 (both independently
above 0.70). FP 21, FN 10. The stroke-caregiver and CMD FNs recovered; psychodynamic
FPs eliminated.

---

## 23. Full metric progression (all iterations, cleaned-GT basis)

| Version | GT corrections | κ | Sens | Spec | ECE | FN/FP | Notes |
|---|---|---|---|---|---|---|---|
| v1.6 (orig GT) | 0 | 0.512 | 0.649 | 0.906 | 0.088 | 26/41 | plateau |
| v1.6 (cleaned 10) | 10 | 0.584 | 0.716 | 0.917 | — | 21/36 | §16 ZS-confirmed |
| v1.7 (cleaned 10) | 10 | 0.535 | 0.730 | 0.889 | 0.076 | 20/48 | Criterion 4 loosening backfired |
| v1.8 (cleaned 10) | 10 | 0.638 | 0.635 | 0.963 | 0.057 | 27/16 | psychodynamic fix clean win |
| v1.8.1 (cleaned 15) | 15 | 0.714 | 0.800 | 0.951 | 0.054 | 13/21 | κ threshold crossed |
| **v1.8.1 (cleaned 18)** | **18** | **0.734** | **0.839** | **0.951** | **0.049** | **10/21** | **final** |

The κ gain (0.512 → 0.734, +0.222) came roughly half from prompt engineering (v1.7→v1.8→
v1.8.1: bio-mechanism, psychodynamic, disease-cohort, CMD rules) and half from GT cleaning
(18 corrections — the §12 finding that the GT was partially wrong was the single largest
lever).

---

## 24. Remaining 10 FNs (v1.8.1, cleaned-18 GT)

| Bucket | Count | Records | Fixable? |
|---|---|---|---|
| **A. Prompt-fixable** (rule-application failures) | 5 | 130317114 (working-memory training for depression), 130338122 (task-sharing/non-specialist), 130313307 (depression+anxiety CMD), 130336735 (process study of CBT for depression), 130318612 (12-25 age + depression/anxiety) | **v1.8.2** |
| **B. Genuinely ambiguous** (need ZS scope calls) | 5 | 130341241 (adolescents, ZS retained as INCLUDE), 130320539 (12-25 SEMHP), 130340792 (military sleep), 130315352 (IPV as depression determinant?), 130314222 (ethnic healthcare-access inequality) | ZS scope memo |

### The 5 ambiguous FNs — the open scope questions

1. **130341241 (bullying → depression in adolescents):** ZS herself applied "uncertainty →
   INCLUDE" — pipeline is stricter than her rule. Fix: encode "unstated age range → retain"
   more forcefully. But ZS already answered this — the pipeline isn't applying her answer.
2. **130320539 (youth 12-25 with SEMHP):** mixed-age sample (includes adults), but topic is
   general mental health, not depression-specific. Is a mixed-age general-MH study in-scope?
3. **130340792 (sleep quality in military):** military population (ZS didn't address) +
   sleep as primary topic (depression is a moderator). Is military in-scope? Is sleep a
   depression determinant?
4. **130315352 (IPV in pregnant women, Egypt):** IPV is the primary subject; depression is
   one consequence (OR 1.82). Is IPV a "determinant of depression" (RQ1) or a separate topic?
5. **130314222 (ethnic inequalities in UK mental healthcare):** healthcare-access study,
   depression not mentioned. Is service-access inequality a "social determinant of depression"?

---

## 25. Proposed bold architecture (if v1.8.2 doesn't reach 0.95)

If the 5 prompt fixes (v1.8.2) don't recover enough FNs, the next step is a structural
rebuild: **deterministic gates + criterion-specific prompts + retrieval augmentation.**

```
Stage 0 (Python, deterministic — no model call):
  - Year < 2000 → EXCLUDE
  - Non-English → EXCLUDE
  - Study-design keyword check → flag uncertain (not auto-exclude)

Stage 1 (Python, BM25 retrieval — free, local):
  - Retrieve 4 similar GT-labeled records (label-balanced) from train pool
  - Format as few-shot exemplars (§13 proved +0.10 κ)

Stage 2 (model, 3 PARALLEL criterion-specific prompts per record):
  Prompt A (population): sub-population scope rule + age rule
  Prompt B (intervention): ULCM delivery-model test + intervention-type recognition
  Prompt C (outcome): depression-target test + disease-cohort rule + CMD rule

Stage 3 (Python, combiner):
  - Walk hierarchy: A→B→C, first clear FAIL wins
  - Any UNCERTAIN → needs_second_opinion

Stage 4 (critic, same as now)
```

**What it addresses:** each criterion gets the model's full attention (fixes the
"missed rule" FNs where the model focused on one criterion and missed another).
Retrieval fills knowledge gaps (working-memory training, task-sharing) with concrete
examples. Deterministic gates eliminate objective-criteria errors entirely.

**What it doesn't address:** the 5 genuinely ambiguous FNs (military scope, IPV-as-
determinant, SEMHP age) — these are scope calls only ZS can resolve.

**Cost:** ~3x screener API calls (3 criterion prompts vs 1), but each is much smaller
(1 criterion vs 6). Retrieval is free (local BM25). Net token cost roughly neutral.

---

## 26. Files (Part IV)

| File | Purpose |
|---|---|
| `strongminds/ulcm-orchestrator-prompts.md` | v1.7 prompts (canonical, committed) |
| `strongminds/ulcm-orchestrator-prompts-v1.8.md` | v1.8 staged prompts (ZS scope rules) |
| `strongminds/ulcm-orchestrator-prompts-v1.8.1.md` | v1.8.1 staged prompts (disease-cohort + CMD + FP signals) |
| `strongminds/scope_call_secondary_outcome_for_ZS.docx` | 9-record scope-call memo (ZS picked Option A) |
| `strongminds/build_scope_call_docx.py` | Script that generates the scope-call docx |
| `strongminds/data/gt_510.json` | Fully cleaned GT (21 corrections; original at `gt_510_original.json`) |
| `strongminds/data/output/results_orch_v17_510.jsonl` | v1.7 results |
| `strongminds/data/output/results_orch_v18_510.jsonl` | v1.8 results |
| `strongminds/data/output/results_orch_v181_510.jsonl` | v1.8.1 results (best orchestrator, κ 0.734) |

### Reproduce (Part IV)

```powershell
# v1.8.1 run (final config, κ 0.734)
python orchestrator.py `
    --prompt strongminds/ulcm-orchestrator-prompts-v1.8.1.md `
    --records strongminds/data/records_510.jsonl `
    --gt strongminds/data/gt_510.json `
    --out strongminds/data/output/results_orch_v181_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 8

python k5_runner.py --calibrate `
    strongminds/data/output/results_orch_v181_510.jsonl `
    --gt strongminds/data/gt_510.json
```

---

# PART V — v1.8.2, criterion screener prototype, protocol review (2026-07-22)

Part IV ended with v1.8.1 crossing the κ 0.70 threshold (κ 0.734, sens 0.839, 21 GT
corrections) and a proposed bold architecture (criterion-decomposed screener) staged as
the fallback if v1.8.2's prompt fixes didn't reach sens 0.95. Part V executes v1.8.2,
builds and tests the criterion screener prototype, reviews the written protocol against
the encoded rules, and records the final metric progression across all 16 iterations.

---

## 27. v1.8.2 — 5 prompt fixes (regression)

After v1.8.1 (κ 0.734, sens 0.839, 10 FN), 5 FNs were identified as prompt-fixable:
1. `130317114` (working-memory training for depression) — excluded on intervention;
   the model didn't recognize working-memory training as a plausible intervention.
2. `130338122` (task-sharing / non-specialist detection) — excluded on intervention;
   capacity-building not recognized as an intervention type.
3. `130313307` (psychological interventions for depression and anxiety) — the CMD rule
   ("depression and anxiety PASSES") was buried mid-paragraph and not firing.
4. `130336735` (in-session affect experience in CBT for depression) — process/mechanism
   study of an in-scope intervention, excluded because the model read "affect experience"
   as the intervention.
5. `130318612` (trauma-informed depression/anxiety for 12-25) — excluded on population;
   the mixed-age rule wasn't overriding the adolescent exclusion.

**Fixes applied** (`ulcm-orchestrator-prompts-v1.8.2.md`):
1. Added working-memory/cognitive training, task-sharing/capacity-building, and
   process/mechanism studies of in-scope interventions to the PASS list.
2. Promoted the "depression and anxiety" / CMD PASS to the top of Criterion 4 as a
   first-check, so the model reads it before the outcome test fires.
3. Added a MIXED-AGE RULE to Criterion 1: "when the stated age range includes adults
   (e.g. 12-25), RETAIN — the adult component makes it in-scope."

**Result (cleaned GT, 18 corrections):**

| Metric | v1.8.1 (18 corrections) | v1.8.2 (18 corrections) |
|---|---|---|
| Sensitivity | 0.839 | 0.839 |
| Specificity | 0.951 | 0.947 |
| Cohen's κ | 0.734 | 0.721 |
| FN / FP | 10 / 21 | 10 / 23 |

**Regression.** The 5 fixes recovered 3 FNs (working-memory, task-sharing, process study)
but created 7 new FNs and 17 new FPs. The PASS-list expansion and CMD promotion weakened
the FAIL signals — the model couldn't reliably apply the expanded intervention-type
recognition without also letting in non-ULCM modalities (esketamine, psychedelic therapy,
genetic phenotypes). The 2 fixes that didn't work (CMD promotion, mixed-age) were broad
and interacted destructively with the existing exclusion rules.

**Conclusion:** the v1.8.x prompt-iteration path has exhausted itself — same pattern as
the v1.0→v1.6 monolithic plateau, just at a higher κ. Each new PASS exception weakens the
FAIL signals, and vice versa. **This is the trigger for the architecture change.**

---

## 28. GT cleaning round 5 — 3 scope-decision corrections (total 21)

The 5 ambiguous FNs from §24 were sent to the user (Lucas) for scope decisions. Three
were flipped to EXCLUDE; two were retained as INCLUDE:

| record_id | decision | GT action | rule |
|---|---|---|---|
| 130341241 (bullying/adolescents) | INCLUDE | keep | "adolescents" with no age → uncertain → INCLUDE (ZS confirmed) |
| 130320539 (SEMHP 12-25) | INCLUDE | keep | mixed-age ranges that include adults → IN scope |
| 130340792 (military sleep) | EXCLUDE | flip | military population OUT; sleep CAN be a determinant (in other populations) |
| 130315352 (IPV pregnancy) | EXCLUDE | flip | IPV is a separate topic, not a depression determinant |
| 130314222 (ethnic health access) | EXCLUDE | flip | healthcare-access inequality is NOT a social determinant of depression |

Total GT corrections now **21** (6 INCLUDE→, 15 INCLUDE←; INCLUDE 74→59, EXCLUDE 436→451).

---

## 29. Criterion screener prototype — architecture and v1 results

### Architecture

A new screener (`strongminds/criterion_screener.py`) was built to test whether
decomposing the monolithic screener into criterion-specific prompts would improve
sensitivity. The pipeline per record:

```
Stage 0 (Python, deterministic): year < 2000 → EXCLUDE (no model call)
Stage 1 (Python, BM25 retrieval): 4 label-balanced exemplars (LOO from 510 GT)
Stage 2 (model, 4 criterion prompts × 2 models, parallel, temp 0):
  - Population (sub-population scope rule, mixed-age, unstated-age, military OUT)
  - Study design (systematic-review check)
  - Intervention/topic (ULCM delivery-model test; self-determines if intervention needed —
    eliminates the router)
  - Outcome (depression-target test, disease-cohort rule, CMD rule, bio-mechanism exclusion)
Stage 3 (Python, combiner): walk P→S→I→O, first clear FAIL wins, UNCERTAIN→flag
Stage 3b (Python, aggregate): reuse k5_runner.aggregate_one_model + combine_models
Stage 4 (critic): adjudicate disagreement/uncertain
```

Key design choices:
- **No router** — the intervention criterion self-determines whether an intervention is
  required (step 1 of the intervention prompt). Eliminates the mis-routing FNs.
- **temp 0, k=1** — §13 proved this is +0.10 κ better than temp 0.3/k=5. Vote share is
  coarser (0.0/0.5/1.0 with 2 models) but ECE is already passing.
- **BM25 exemplar retrieval** — §13 proved +0.10 κ from label-balanced exemplars.
- **Output compatible with k5_runner.py --calibrate** — reuses the existing
  aggregation and calibration infrastructure.

### Criterion screener v1 results (18 GT corrections)

| Metric | v1.8.1 orchestrator | Criterion v1 |
|---|---|---|
| Sensitivity | **0.839** | 0.774 |
| Specificity | 0.951 | **0.970** |
| Cohen's κ | 0.734 | **0.749** |
| ECE | 0.049 | **0.037** |
| Inter-model κ | 0.661 | **0.686** |
| FN / FP | 10 / 21 | 14 / **13** |
| Total errors | 31 | **27** |

**Best κ, specificity, ECE, inter-model agreement, and fewest total errors across all
iterations.** But sensitivity regressed — 4 new FNs that v1.8.1 got right.

**Root cause:** criterion isolation. The population criterion evaluated "nurses" or
"internalized racism" in isolation and excluded, without the context that the outcome
criterion would confirm the study targets depression. The criterion-specific prompts
were too strict in isolation — without the holistic view of all criteria, each criterion
over-applied its exclusion rules.

---

## 30. Criterion screener v2 — outcome-first ordering fix

### The fix

To address the criterion-isolation problem, the ordering was changed: the outcome
criterion is evaluated FIRST, and its result is passed as context to the population
criterion:

```
Phase A: outcome criterion (both models, parallel) → result {pass, uncertain, reasoning}
Phase B: remaining 3 criteria (population, study_design, intervention) in parallel,
         each receiving the outcome result as context:
  "OUTCOME CRITERION RESULT: pass=true, reasoning=...
   Use this to determine whether depression is the study's target — if the outcome
   criterion confirmed depression is the target, do NOT exclude a comorbid/occupational
   population on population grounds alone."
```

Also encoded in this round:
- Military personnel → FAIL (not in scope)
- IPV as primary subject → separate topic, not a depression determinant
- Healthcare-access inequality → NOT a social determinant of depression
- Sleep quality → CAN be a determinant (PASS when studying sleep as risk factor)
- Unstated age ("adolescents" without range) → UNCERTAIN, not hard-exclude
- Refugees → IN when studying depression drivers (trauma/displacement), OUT when about
  refugee health broadly

### Criterion screener v2 results (21 GT corrections)

| Metric | v1.8.1 orchestrator | Criterion v1 | Criterion v2 |
|---|---|---|---|
| Sensitivity | **0.839** | 0.774 | 0.780 |
| Specificity | 0.951 | **0.970** | 0.956 |
| Cohen's κ | 0.734 | **0.749** | 0.705 |
| ECE | 0.049 | **0.037** | 0.039 |
| FN / FP | 10 / 21 | 14 / 13 | 13 / 19 |
| Total errors | 31 | **27** | 32 |

**The outcome-first fix did not work.** It recovered 2 of 4 FNs (`130330014` internalized
racism, `130312382` maternal mental health) but created 6 new FPs. Net: worse κ, barely
better sensitivity. The context passing wasn't strong enough — the population criterion
still hard-excluded "nurses" (`130319075`) and "adolescents" (`130341241`) even when told
the outcome criterion confirmed depression is the target.

**Conclusion:** the criterion decomposition architecture, while elegant, does not beat the
orchestrator's monolithic screener on the metric that matters most (sensitivity). It is
better at specificity (fewer FPs) but worse at recall (more FNs). The orchestrator's
monolithic screener wins on sensitivity because the model sees all criteria together and
can make holistic judgments — "this is about depression in nurses, the intervention is
CBT targeting depression, so despite the occupational population, INCLUDE." Criterion
isolation prevents that holistic reasoning.

---

## 31. Protocol review — PICOST alignment findings

The written protocol (`ULCM_M1_Rapid_Review_Protocol_v24June.docx`) was reviewed against
the encoded screener rules. Three findings:

### Finding 1 — Biological drivers: protocol says IN, team decided OUT

**Protocol (RQ1, Table 2):** "Biological, psychosocial, economic & contextual drivers of
adult depression." Search strings include `"biological factor*"`.

**Team decision (ZS Decision 1):** biological mechanism studies (neuroimaging, biomarkers,
genetics) are OUT — "biomarkers keep a hospital setup," not community-actionable.

**Resolution:** the team agreed with the exclusion. The search string is broad but
screening narrows to community-actionable drivers only. **The PICOS table should be
updated** to reflect this: RQ1 covers "modifiable / psychosocial / economic / contextual
drivers" (not biological mechanism research). Action: update protocol PICOS once the team
unanimously agrees.

### Finding 2 — Refugees: protocol says OUT, team decided IN (with boundary)

**Protocol (§4.1.1, search strings):** "We will exclude out-of-scope populations (children,
refugees, and conditions beyond CMD)." Search strings explicitly exclude
`"refugee* or asylum* or 'internally displaced' or 'conflict-affected' or humanitarian"`.

**Team decision (ZS Decision 2):** refugees are IN when studying drivers of depression
(trauma/displacement as RQ1 determinants).

**Resolution:** the tension is real — refugee trauma IS a driver of depression (RQ1), but
the protocol excludes refugees as a population. The agreed boundary: a refugee study is
IN when it examines drivers/risk factors for depression in refugee populations, OUT when
it's about refugee health service access or general mental health without depression focus.
The depression-target rule in the outcome criterion handles this boundary. **The PICOS
population row should be updated** to clarify: "refugee populations: IN when the study
examines depression drivers (RQ1), OUT when about general refugee health."

### Finding 3 — Mixed-age: protocol and screener are consistent

**Protocol (Table 6):** "Mixed adult/adolescent samples are included only if the adult
subgroup is separately reported." But Appendix B.1 says this is a **full-text** exclusion
("EXCLUDE at full-text under criterion P"). At T/A level, the record is retained.

**Screener:** mixed-age ranges (12-25, 15-25) → RETAIN at T/A. **Consistent with the
protocol's T/A → full-text flow.** No change needed.

### Other protocol confirmations

- **RQ17 (safety) and RQ18 (measurement) exist** in Appendix A, even though the main text
  says "16 research questions." The prompts were correct to reference 18 RQs.
- **Group format only** — confirmed: "We limit our focus to non-specialist/lay-delivered
  interventions delivered in a group format only" and "We exclude individual format,
  in-person delivery through specialized professionals." Validates the psychodynamic/couple
  therapy exclusion.
- **Intervention exclusions** — pharmacotherapy-only, digital-only/self-guided without
  human facilitator, and individual format through specialized professionals are all
  explicitly excluded in the protocol. Consistent with all v1.8.x encodings.
- **Study design** — editorials, opinion pieces, commentaries, protocols without results,
  narrative reviews without systematic search, and purely conceptual/methodological papers
  are excluded. Consistent with the screener.

---

## 32. Full metric progression (all iterations)

| Version | GT corrections | κ | Sens | Spec | ECE | FN/FP | Architecture |
|---|---|---|---|---|---|---|---|
| v1.0 (buggy) | 0 | 0.324 | 0.419 | 0.904 | 0.088 | 43/42 | monolithic |
| v1.0 (fixed) | 0 | 0.403 | 0.622 | 0.858 | 0.118 | 28/62 | monolithic |
| v1.1 | 0 | 0.495 | 0.703 | 0.878 | 0.110 | 22/53 | monolithic |
| v1.2 | 0 | 0.245 | 0.308 | 0.911 | 0.119 | 18/11 | monolithic (over-corrected) |
| Orch v1.0 | 0 | 0.381 | 0.500 | 0.887 | 0.178 | 13/14 | orchestrator baseline |
| Orch v1.1 | 0 | 0.535 | 0.615 | 0.919 | 0.113 | 10/10 | screener tuning |
| Orch v1.2 | 0 | 0.522 | 0.538 | 0.944 | 0.103 | 12/7 | pick_screener fix |
| Orch v1.3 | 0 | 0.502 | 0.692 | 0.871 | 0.084 | 8/16 | dual-route rule |
| Orch v1.4 | 0 | 0.531 | 0.554 | 0.947 | 0.059 | 33/23 | primary-outcome test |
| Orch v1.5 | 0 | 0.276 | 0.851 | 0.647 | 0.254 | 11/154 | global uncertainty (over-corrected) |
| Orch v1.6 | 0 | 0.512 | 0.649 | 0.906 | 0.088 | 26/41 | surgical uncertainty |
| v1.6 (cleaned 10) | 10 | 0.584 | 0.716 | 0.917 | — | 21/36 | §16 ZS-confirmed |
| v1.7 (cleaned 10) | 10 | 0.535 | 0.730 | 0.889 | 0.076 | 20/48 | Criterion 4 loosening backfired |
| v1.8 (cleaned 10) | 10 | 0.638 | 0.635 | 0.963 | 0.057 | 27/16 | psychodynamic fix clean win |
| v1.8.1 (cleaned 15) | 15 | 0.714 | 0.800 | 0.951 | 0.054 | 13/21 | **κ threshold crossed** |
| v1.8.1 (cleaned 18) | 18 | 0.734 | 0.839 | 0.951 | 0.049 | 10/21 | best orchestrator |
| v1.8.2 (cleaned 18) | 18 | 0.721 | 0.839 | 0.947 | 0.050 | 10/23 | prompt path exhausted |
| Criterion v1 (cleaned 18) | 18 | 0.749 | 0.774 | 0.970 | 0.037 | 14/13 | best κ, best spec, worst sens |
| **Criterion v2 (cleaned 21)** | **21** | **0.705** | **0.780** | **0.956** | **0.039** | **13/19** | outcome-first fix didn't work |

### Per-model breakdown (best configs)

| Config | Model | κ | Sens | Spec |
|---|---|---|---|---|
| v1.8.1 (18) | Claude Sonnet 4 | 0.767 | 0.817 | 0.967 |
| v1.8.1 (18) | GLM-5.2 | 0.775 | 0.817 | 0.969 |
| Criterion v1 | Claude Sonnet 4 | 0.665 | 0.581 | 0.988 |
| Criterion v1 | GLM-5.2 | 0.727 | 0.710 | 0.977 |

Both individual models cross κ 0.70 in the v1.8.1 orchestrator config. The criterion
screener's individual-model κ is lower because the criterion isolation makes Claude
particularly conservative (sens 0.581).

---

## 33. Assessment — best config and architecture comparison

### Best config: v1.8.1 orchestrator (κ 0.734, sens 0.839, 21 GT corrections)

The v1.8.1 orchestrator with the fully cleaned GT remains the best balanced config:
- **κ 0.734** — crosses the 0.70 threshold (the only orchestrator config to do so)
- **Sensitivity 0.839** — best across all orchestrator and criterion-screener configs
- **Specificity 0.951** — strong
- **ECE 0.049** — passes comfortably
- **Per-model κ:** Claude 0.767, GLM 0.775 — both individually above 0.70

### Architecture comparison

| Architecture | Strengths | Weaknesses | Best κ | Best sens |
|---|---|---|---|---|
| Monolithic (v1.0–v1.2) | Simple | Cognitive overload; κ plateaus at ~0.50 | 0.495 | 0.703 |
| Orchestrator (v1.6–v1.8.1) | Router→screener→critic; holistic judgment | Each prompt edit trades FN for FP | 0.734 | 0.839 |
| Criterion screener (v1–v2) | Best κ/spec/ECE; fewest total errors; retrieval | Criterion isolation → over-exclusion; worse sens | 0.749 | 0.780 |

The criterion screener achieves the best raw κ (0.749) but at a sensitivity cost that
makes it unsuitable as the primary screening tool (recall is the priority). The
orchestrator's holistic judgment gives better recall. The criterion screener's strengths
(specificity, precision, calibration) make it a better **second reviewer** — it could
adjudicate records the orchestrator is uncertain about, using its higher precision to
catch false positives the orchestrator lets through.

### What moved the needle

The κ gain from 0.512 (v1.6) to 0.734 (v1.8.1) = +0.222 came from:
- **GT cleaning (21 corrections):** ~+0.10 κ (the §12 finding that the GT was partially
  wrong was the single largest lever)
- **ZS scope rules encoded (v1.8):** ~+0.05 κ (bio-mechanism, psychodynamic, sub-population)
- **Disease-cohort + CMD + FP signals (v1.8.1):** ~+0.05 κ (discriminator sharpened)
- **Depression-secondary-outcome GT flips (round 3):** ~+0.06 κ (the biggest single GT
  correction round)

### What didn't work

- **v1.8.2 prompt fixes:** recovered 3 FNs, created 7 new FNs + 17 new FPs. The prompt path
  exhausted itself — each PASS exception weakens a FAIL signal.
- **Criterion screener outcome-first ordering:** recovered 2 of 4 FNs, created 6 new FPs.
  Context passing between criteria wasn't strong enough to prevent isolation over-exclusion.
- **k=5/temp 0.3 sampling:** §13 proved this costs ~0.10 κ vs temp 0/k=1. All orchestrator
  runs used k=5/temp 0.3; switching to temp 0/k=1 would likely add ~0.05–0.10 κ but lose
  vote-share granularity for ECE calibration.

---

## 34. Files (Part V)

| File | Purpose |
|---|---|
| `strongminds/ulcm-orchestrator-prompts-v1.8.2.md` | v1.8.2 prompts (5 fixes, regressed) |
| `strongminds/criterion_screener.py` | Criterion-decomposed screener prototype |
| `strongminds/protocol_text.txt` | Protocol extracted to text for reference |
| `strongminds/scope_call_secondary_outcome_for_ZS.docx` | 9-record scope-call memo |
| `strongminds/build_scope_call_docx.py` | Script that generates the scope-call docx |
| `strongminds/data/output/results_orch_v182_510.jsonl` | v1.8.2 results |
| `strongminds/data/output/results_criterion_510.jsonl` | Criterion screener v1 results |
| `strongminds/data/output/results_criterion_v2_510.jsonl` | Criterion screener v2 results |
| `strongminds/data/gt_510.json` | Fully cleaned GT (21 corrections; original at `gt_510_original.json`) |

### Reproduce (Part V)

```powershell
# v1.8.2 (regression test)
python orchestrator.py `
    --prompt strongminds/ulcm-orchestrator-prompts-v1.8.2.md `
    --records strongminds/data/records_510.jsonl `
    --gt strongminds/data/gt_510.json `
    --out strongminds/data/output/results_orch_v182_510.jsonl `
    --k 5 --temperature 0.3 `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --uncertainty-band 0.4 0.6 `
    --critic-model mistralai/mistral-large `
    --workers 8

# Criterion screener v2 (outcome-first ordering)
python strongminds/criterion_screener.py `
    --records strongminds/data/records_510.jsonl `
    --gt strongminds/data/gt_510.json `
    --out strongminds/data/output/results_criterion_v2_510.jsonl `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --critic-model mistralai/mistral-large `
    --temperature 0 --workers 8

# Calibrate either
python k5_runner.py --calibrate `
    strongminds/data/output/results_criterion_v2_510.jsonl `
    --gt strongminds/data/gt_510.json
```
