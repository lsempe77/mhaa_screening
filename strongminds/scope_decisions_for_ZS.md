# Protocol-scope decisions needed from the review team

**From:** the ULCM TAS-screening iteration (v1.6 → v1.7)
**Date:** 2026-07-21
**Context:** After GT cleaning (10 human-confirmed label corrections), the tool sits at
sens 0.716 / spec 0.917 / κ 0.584 against the cleaned reference. Two further
improvements are blocked not by the model or the prompt, but by **unwritten scope
rules** the GT applies but the protocol does not state. These need a review-team
decision before they can be encoded into the screener prompts. Each is illustrated
with the concrete records that force the question.

---

## Decision 1: Are biological correlates (biomarkers, neuroimaging, genetic
correlations) "determinants" for RQ1?

**The question.** RQ1 covers "drivers/risk factors for adult depression." The
written criterion is broad enough to include any association with depression. But
the GT **excludes** a cluster of records that study biological correlates of
depression — neuroimaging, biomarkers, genetic polymorphisms — while **including**
social/psychological/economic risk factors. The boundary is not stated in the
protocol.

**Records the GT excludes (currently pipeline INCLUDES — false positives):**

| record_id | title | What it studies |
|---|---|---|
| 130320840 | Major depressive disorder associated alterations in effective connectivity of the face processing network | MDD neuroimaging — brain connectivity during facial expression processing |
| 130321825 | Inter-Brain Synchrony and Psychological Conditions: Stress, Anxiety, Depression, Autism | Hyperscanning / inter-brain synchrony as a correlate of depression |
| 130326298 | Genetic markers associated with stress generation in depression | 5-HTTLPR / OXTR / HPA-axis genetic correlates of depression |
| 130327136 | Mendelian Randomization for neurological and psychiatric diseases incl. MDD | Genetic instrument / drug-target MR for MDD |

**Records the GT includes (determinants the tool correctly keeps):**

| record_id | title | What it studies |
|---|---|---|
| 130312828 | Depression in the Iranian elderly: prevalence & meta-analysis | Prevalence + social planning in an LMIC sub-population |
| 130322190 | Prevalence of psychiatric disorders among tribal populations of India | Prevalence in a marginalized LMIC sub-population |

**The decision needed.** Which of these classes count as RQ1 "determinants"?
- (a) Only **modifiable / social / psychological / economic** risk factors and prevalence — exclude biological/neurobiological/genetic correlates as out-of-scope basic science.
- (b) **All correlates** of depression count, including biomarkers and neuroimaging — keep them.
- (c) Biological correlates are in-scope **only when they inform intervention or policy** (e.g. a biomarker validated for screening), not as pure mechanism research.

**Recommendation:** option (a) appears to match the GT's implicit practice and the
review's applied focus ("Ultra-Low-Cost Model"). Encoding "biological/neurobiological/
genetic mechanism studies are NOT determinants unless they validate a screening
marker or intervention target" would cut ~4–6 false positives with no sensitivity cost.

---

## Decision 2: Which sub-populations are in-scope for the population criterion?

**The question.** The written population criterion says "adults 18+ with depression
/ anxiety-depression / CMD / distress; perinatal women eligible; mixed
adult/adolescent retained unless clearly adolescent-only." The GT, however,
**excludes** a set of adult sub-populations on "population" even though they are
adults with a depression focus. The excluding principle is not written anywhere.

**Records the GT excludes on population (currently pipeline INCLUDES — false
positives), grouped by sub-population:**

| Sub-population | Example record_ids | Count |
|---|---|---|
| Prisoners / incarcerated adults | 130313498, 130317150 | 2 |
| University / medical students | 130312643, 130321297 | 2 |
| Refugees / displaced persons (non-LMIC-resident) | 130313819 | 1 |
| Disease-specific cohorts (epilepsy, CKD/dialysis, post-stroke aphasia) | 130353034*, 130340196 | 2 |
| Older adults in specific care settings | 130326299, 130335744 | 2 |
| Parents of infants / parent-infant dyads | 130335744, 130346770* | 2 |

*130353034 (epilepsy+depression, Ethiopia) and 130338268 (post-stroke aphasia) were
**human-confirmed as INCLUDE** in your review of the 12 GT-error candidates, so the
GT's "disease-specific cohort" exclusion is not consistent. This is exactly the
fuzziness that makes a written rule necessary.

**Records the GT includes on population (correctly kept) — to show the boundary is
real but unstated:**

- Older adults with dementia+depression, cognitive impairment+depression (kept)
- Heart failure + CBT for depression (kept)
- IPV in pregnancy with depression (kept)
- Post-Ebola psychosocial support (kept)

**The decision needed.** Please enumerate which adult sub-populations are
**out-of-scope** regardless of depression focus. A candidate list to confirm or edit:

- Prisoners / incarcerated populations: **IN or OUT?**
- University / medical students: **IN or OUT?** (ZS's note on 130341241 suggests
  "late adolescence 18-24 is includable" — does this extend to all student
  populations, or only when the age range is clearly 18+?)
- Refugees resettled in HIC (e.g. Southeast Asian refugees in the US): **IN or OUT?**
  (Refugees in LMIC are clearly in; the question is the resettled-in-HIC case.)
- Disease-specific cohorts (epilepsy, CKD, HIV, post-stroke): **IN when the
  intervention/study targets depression, OUT when depression is merely measured
  alongside the disease?** (This overlaps with Decision 1's "primary focus" idea.)
- Parents of infants / parent-infant dyads: **IN when the parent's depression is
  the target, OUT when the infant/attachment is the target?**

**Recommendation:** the cleanest rule, and the one that best matches your confirmed
INCLUDE decisions, is: **"a sub-population is in-scope when depression is the
target of the study (intervention, determinants, or measurement); a sub-population
is out-of-scope when depression is merely measured as one outcome of a study whose
primary subject is something else (the disease, the infant, the incarceration, the
student experience)."** This would make the excluding principle explicit and
consistent, and it would not require excluding disease-specific cohorts where
depression is the focus.

---

## Decision 3 (minor, from your EQ-5D note): Does RQ18 "depression measurement"
include generic health-status instruments with an anxiety/depression dimension?

**Your note on 130377408** ("EQ-5D population norms... could be a fit for RQ18.
Need a second opinion"): the EQ-5D is a generic health-status instrument whose
dimensions include an anxiety/depression item. The question is whether RQ18
"validity/reliability of depression measurement tools" covers:

- (a) **Depression-specific instruments only** (PHQ-9, EPDS, BDI, CES-D, HSCL,
  K10, SRQ-20, etc.) — exclude generic instruments even if they have an
  anxiety/depression dimension.
- (b) **Any instrument with a depression-relevant dimension** when the study
  validates that dimension specifically — include EQ-5D's AD dimension if the
  study is about its depression-measurement properties.

**Recommendation:** option (a) — RQ18 is about depression measurement, and a generic
health-status instrument is not a "depression measure" even if it touches the
construct. This keeps the route focused.

---

## What these decisions unlock

Once decided, these scope rules can be encoded as explicit FAIL signals in the
screener prompts (v1.8). Estimated effect on the cleaned-GT metrics:

| Decision | Est. FP reduction | Est. FN change | Est. κ Δ |
|---|---|---|---|
| 1 (biological correlates) | -4 to -6 | 0 | +0.03 |
| 2 (sub-population scope) | -6 to -8 | 0 to -2 | +0.04 |
| 3 (RQ18 instrument scope) | -1 | 0 | +0.005 |

Combined with the v1.7 prompt edits (router fix + Criterion 4 reframing), the
target after v1.8 is **sens ~0.86 / spec ~0.93 / κ ~0.66–0.68** — approaching but
not crossing the 0.70 threshold, consistent with the §12 finding that the residual
gap is irreducible fuzziness and absent-from-abstract judgment calls.
