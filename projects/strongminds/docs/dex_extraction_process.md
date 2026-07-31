# StrongMinds ULCM — Data Extraction (DEX)

**Stage 4** of the pipeline (TAS → FTR → FTS → **DEX**). Pulls the structured ULCM schema
(~220 fields) out of each full-text include for synthesis / the Decision Brief.

**Status:** ✅ **Full run complete (2026-07-31): 2,668 / 2,670 extracted** (Sonnet, k=1).
2 residual: the two most extreme umbrella reviews (`CN2UHGBP`, `YEC958EM`) exceed Sonnet's
200k-token context even after section-pruning — flagged as errors in the review queue for
manual handling. Oversized reviews (>~200k tokens) were re-pruned to a context-safe cap and
re-run (29 → 8 → 2). Deliverables: `data/extraction/reports/dex_summary.csv`,
`dex_long.csv`, `dex_outcomes_long.csv`, `dex_review_queue.csv` (1,200 rows),
`dex_review.xlsx`. Run wrapper: [run_dex_full.ps1](../scripts/run_dex_full.ps1);
export: [dex_export.py](../../../pipeline/extraction/dex_export.py).

---

## Method

Hybrid: reuse the `pipeline/extraction/` mechanical parts (verbatim-quote check, resumable
JSONL) but drive extraction from the **protocol-signed prompt**, not a generated one.

- **Prompt:** [ulcm-extraction-prompt-v1.7.md](../prompts/ulcm-extraction-prompt-v1.7.md) —
  a working copy of `DEX/ULCM_M_1.MD` (kept pristine). v1.7 adds `eligibility_flag` +
  `eligibility_concern` (logged deviation). The `.md` carries the full typed schema, the 29
  extraction rules, controlled vocabularies, and the span-validator — so nothing is lost to
  a YAML round-trip.
- **Runner:** [run_dex.py](../../../pipeline/extraction/run_dex.py) — per record: build the
  user message (segmented full text + `{unit, tier, rq_tags_hint}`) → run k paraphrase
  variants → per-field majority merge → deterministic verbatim-quote check → resumable JSONL.
- **Input:** `data/extraction/records_extract_final_2670.jsonl` (segmented, untruncated,
  metadata-joined, route-hinted).
- **Extractor:** `anthropic/claude-sonnet-4` (review-team choice).

## k strategy (decided from pilot evidence)

The pilot showed structured fields are **stable across runs** — at k=2, 93% of
disagreements were free-text notes/spans/quotes, only ~2 CORE fields/record. So:

- **k=1 base** for the stable descriptive fields (default).
- **Deterministic quote-check** grounds every non-null value in a verbatim span; ungrounded
  values (paraphrase/hallucination) are flagged for human — this catches the failure
  k-voting can't (a wrong value both runs agree on).
- **High-stakes fields** (quantitative effects/Ns/cost/dose + AMSTAR-2/ROB verdicts, tagged
  by `HIGH_STAKES` in the runner) are the review-queue priority and are **100% human-verified**
  per protocol §4.4.1. A targeted k=2 re-extraction of just this subset is an available
  enhancement (not needed for v1).
- k=2 remains available (`--k 2`) for a cross-run disagreement signal on all fields.

This is a **logged deviation** from the protocol's blanket k=3 (justified by the pilot; a
k=1-vs-k=3 equivalence check on a sample can be added if required).

## eligibility_flag (FTS over-inclusion safety net)

The FTS screener is a high-sensitivity, permissive filter (over-includes on nuance — see
[ris_determinants_correction.md](ris_determinants_correction.md) §2b). `eligibility_flag`
(flag-only, **never excludes**) surfaces likely false-includes for human review. It is
**route-conditional**: determinants (RQ1) and measurement (RQ18) do NOT require an
intervention, so absence of one is not a concern — this avoids re-creating the determinants
miss at extraction (a bug the pilot caught and we fixed).

## Pilot results (5 studies, Sonnet)

- ✅ Valid, complete 319-field JSON; `geo_focus`/`country`/`rq_tags`/`design` correct
  (e.g. Sierra Leone scale-validation → SSA / SLE / RQ18; UK cognitive-ability → HIC / GBR / RQ1).
- ✅ Route-aware `eligibility_flag`: all 5 in-scope records → `Eligible`.
- ✅ Quote-check works: flags paraphrased / over-long spans (~5% of fields) for human.
- Data-quality carry-forward: **3 recovered records are 1-page stub PDFs** (abstract-only
  fetches) — re-fetch before trusting their extraction.

## Rigor-review additions (2026-07-30)

- **Tolerant quote matcher** (exact → reflow → fuzzy): halves PDF-reflow false quote-fails; remaining fails are real grounding issues.
- **Array grounding**: `additional_outcomes[]` / `rq_contributions[]` cells (per-timepoint effect sizes) now quote-checked — previously ungrounded.
- **Audit stamping**: every output record carries `_meta` {model, prompt version, k, timestamp}.
- **doctype/design spacing normalization** ("Journal article" → "Journal-article").
- **`comparator`** field added (headline-effect comparator — needed to interpret/pool effects).
- **`n_included_studies` + `included_study_ids`** added (reviews) — enables cross-review **overlap / double-counting** detection (corrected covered area). Confirmed extracting (e.g. 18 studies with author-year IDs).

**Cross-review overlap analysis — built + run** ([dex_overlap.py](../../../pipeline/extraction/dex_overlap.py)):
9,042 unique primary studies across 940 reviews; **1,631 (18%) shared by >1 review**; 59 clusters.
Landmark trials pooled widely (o'mahen 2013 in 19 reviews; rahman 2008 in 15). Outputs
`dex_overlap_{studies,reviews,clusters}.csv` (+ overlap columns merged into `dex_summary.csv`).
Genuinely redundant clusters (e.g. cluster 36: 7 reviews, CCA 10.9% "high") are flagged for the
review team to resolve (keep most comprehensive/highest-AMSTAR-2 review, or drop to primary-level).

Still open for the review team: **resolve the high-overlap clusters**; the **primary-study
drill-down** (source_review_ids / dedup / tier) is **not yet operational** — the 301
`primary_study` records are heuristic; and confirm **GRADE certainty** is a synthesis-stage step.

## Run it

```powershell
# Pilot (already done):
python pipeline/extraction/run_dex.py `
    --prompt  projects/strongminds/prompts/ulcm-extraction-prompt-v1.7.md `
    --records projects/strongminds/data/extraction/records_extract_final_2670.jsonl `
    --out     projects/strongminds/data/extraction/dex_pilot.jsonl `
    --extractor anthropic/claude-sonnet-4 --k 1 --ids <id1,id2,...> --workers 3

# Full run (pending go-ahead): drop --ids, add a resumable wrapper; then build the
# reviewer Excel + long-format table for human verification.
```

## Remaining before the full run

1. **Go-ahead** for the paid 2,670-record run (the one open decision).
2. Reviewer **Excel + long JSONL export** (adapt `pipeline/extraction/export.py`; materialise
   `additional_outcomes[]` / `rq_contributions[]` long-format).
3. Re-fetch the 3 stub PDFs.
