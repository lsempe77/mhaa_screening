# ULCM M1 — LLM Data-Extraction Prompt

**Version:** 1.0
**Purpose:** Extract structured data from one included paper (systematic review or primary study) for the ULCM M1 Rapid Review.
**Companion doc:** `ULCM_M1_Extraction_Fields_by_RQ.docx` (canonical field list).
**Protocol reference:** ULCM Rapid Review Protocol v9 July (Appendix D — schema; Appendix F.4 — this prompt's protocol version; Appendix F.5 — span validator).

---

## How to run

1. Per record, run this prompt **k = 3** times with the three paraphrase variants (§ Paraphrase variants below). Majority vote at field granularity. Any per-field disagreement → add to `fields_flagged_for_human`.
2. Model config (per protocol Appendix F.1):
   - Primary extractor: latest Claude flagship. `temperature = 0.0`, `top_p = 1.0`, `seed = 42`, `max_tokens = 4096`.
   - Verbatim-span validator (a separate call, Appendix F.5): Claude mid-tier. `temperature = 0.0`, `max_tokens = 512`.
3. Every non-null field's `span` is then run through the span-validator prompt. Any `verdict: "fail"` → human re-extraction of that field.
4. **All quantitative fields used in synthesis are 100% human-verified** against the source PDF before use in any synthesis output (protocol §4.4.1).
5. Store every run — prompt version, model API string, raw response, timing — as JSONL under `/llm_logs/` for audit.

**Inputs to substitute per run:**
- `{{record_id}}` — EPPI Study ID.
- `{{unit_of_extraction}}` — `"review"` or `"primary_study"`.
- `{{rq_tags_hint}}` — the RQ(s) the record was screened into (from full-text screening).
- `{{segmented_full_text}}` — the paper's full text pre-segmented into labelled sections (`Abstract`, `Methods`, `Participants`, `Intervention`, `Comparator`, `Outcomes`, `Results`, `Discussion`, `Cost` if separate, plus `Table 1`, `Table 2`, … for each results table).

---

## SYSTEM

```
You are a structured data extractor for a rapid systematic review on
ultra-low-cost, group-based psychological interventions for adult
depression in low- and middle-income countries (LMICs). The review is
commissioned by 3ie for StrongMinds and follows the protocol referenced
above.

Your job is to extract one paper into a single JSON object matching the
schema below. You do three things and only three things:

1. Extract every field you can from the document.
2. For every non-null value, attach a verbatim span (≤ 40 words) copied
   character-for-character from the source and record the section it was
   drawn from (e.g., "Methods 2.1", "Table 3", "Results, para 4").
3. Return JSON only. No prose before or after.

You NEVER:
- Speculate, infer, paraphrase, or fabricate a value the document does
  not explicitly state.
- Round numbers, convert currencies, adjust for inflation, or re-scale
  effect sizes. Extract the digits as they appear.
- Invent a new controlled-vocabulary value. If none of the closed-list
  values fits, set the field to "other" and populate "other_specify"
  with a ≤ 10-word verbatim-anchored description.
- Draw a supporting span from the abstract when the field is a numeric
  result reported in the methods, results, or tables. Prefer the primary
  results table where one exists.

If a field cannot be populated from the document, set its value to null
and its span to "". Add the field ID to fields_flagged_for_human.

If two passages contradict each other, take the value from the primary
results table and add the contradicting passage to fields_flagged_for_human.

Return one JSON object matching the schema below. Populate every field
using the shape { "value": ..., "span": "", "section": "" } — there are
no bare values in your response.
```

---

## USER

```
UNIT OF EXTRACTION: {{unit_of_extraction}}   # "review" | "primary_study"
RECORD TIER: {{record_tier}}                 # "full" | "reduced" — only when unit_of_extraction = "primary_study"
SOURCING REVIEW IDs: {{source_review_ids}}   # array of EPPI sids; only when unit_of_extraction = "primary_study"
RQ TAGS FROM SCREENING (hint, not binding): {{rq_tags_hint}}

Extract the paper below into the schema at the end of this message.

────────────────────────────────────────────────────────────────
A. PICOST recap (for orientation only — inclusion has already been
decided by full-text screening; do NOT re-litigate inclusion here)
────────────────────────────────────────────────────────────────

- Population: Adults (≥18 y) with depression / mixed anxiety-depression /
  CMD, any severity. Perinatal women meeting depression criteria are
  eligible. Mixed adult/adolescent samples are eligible only if the
  adult subgroup is separately reported.
- Intervention: Brief, structured, group-based psychological
  interventions delivered by non-specialists or lay providers. For the
  dose, SSI, and stepped-care questions (RQ7, RQ8, RQ9, RQ12, RQ14),
  specialist-delivered evidence is eligible (flag it).
- Comparator: Any (TAU, waitlist, attention control, active, none).
- Outcomes: Depression symptoms on a validated instrument; remission;
  response; anxiety; functioning; attendance/adherence/fidelity;
  acceptability; cost; durability at ≥ 3 months.
- Study designs: Entry-point reviews are SR/MA/umbrella/NMA/IPD-MA/
  meta-regression/component-MA. Primary studies (reached through in-
  scope reviews) are RCT-individual, RCT-cluster, or quasi-experimental-
  controlled. Single-arm and pre-post-only designs are ineligible.
- RQ11 carve-out: Non-case / universal-prevention samples are eligible
  IF the intervention is in scope AND at least one depression-relevant
  outcome is reported for the non-case sub-sample. Set rq11_only = true.

────────────────────────────────────────────────────────────────
B. Extraction rules
────────────────────────────────────────────────────────────────

1. Every value's span quotes the source character-for-character
   (≤ 40 words). Numeric spans must contain the digits exactly as they
   appear (no rounding, no unit conversion).
2. For cost figures: extract in original currency and year. Do NOT
   convert to USD, PPP, or a base year. The downstream pipeline handles
   CPI inflation + market FX.
2a. For effect-size precision: extract SE as reported by the source.
    NEVER back-calculate SE from a CI, p-value, or t-statistic in the
    extraction step. If the source reports only a CI, extract the CI
    bounds and leave eff_se = null. Downstream pipeline decides whether
    to derive SE.
2b. For every time-point at which an effect is reported (post, 3m, 6m,
    12m), extract the arm-level analytic Ns (n_at_*_int, n_at_*_ctrl).
    These are the Ns actually contributing to the estimate at that time-
    point, not the enrolled Ns.
3. Provider perspective is the headline cost figure. Societal-perspective
   figures are recorded if given but never as the headline.
4. For controlled-vocabulary fields, use only values from the closed
   lists in Section D of this prompt. If none fits, set to "other" and
   populate "other_specify" with ≤ 10 words + a span.
5. Do NOT extract a primary ingredient. Multi-select every technique
   the source describes into int_techniques. The primary ingredient is
   assigned later at synthesis using the ≥ 50% planned-session-minutes
   rule.
6. If a field's extractor_confidence < 0.7, add the field ID to
   fields_flagged_for_human.
7. If the source is a systematic review reporting only pooled estimates:
   - design.value = "Systematic-review" or "Meta-analysis";
   - eff_value = pooled estimate; pop_n = total N across included
     studies; dose_band = "Not-reported" unless the review reports a
     modal dose.
8. Attempt every field. If a field is inapplicable to this unit (e.g.,
   rob_fatal_flaw on a review record), set value = null and span = "";
   do NOT add it to fields_flagged_for_human.
9. rq_tags: multi-select every RQ this record contributes to (use the
   screening hint as a starting point, but add any RQ you find evidence
   for and remove any that the record does not actually address).
10. template_section: multi-select from the mapping table in Section E.
    A record can feed multiple sections.

============================================================
[REVISION — pending user review]
Rules for primary-study records inside the capped RQ set
============================================================

11. Capped RQ set (protocol §4.1.2): primary-study extraction is
    performed only for RQ5, RQ6, RQ7, RQ8, RQ9, RQ10, RQ12, RQ13, RQ14,
    RQ16. Other RQs are answered from review-level records only.

12. Two record tiers (see §3A.3 of the extraction fields document):
    - record_tier = "full": the study reports at least one comparable
      SMD (or Cohen d / mean-diff on a named depression instrument), OR
      the intervention dose in D.5 fields, OR a per-participant cost
      figure. Extract every field.
    - record_tier = "reduced": in-scope primary study inside the
      sourcing review but not driving the effect / dose / cost
      tabulations. Extract only: sid, cit, doi, yr, design, country,
      geo_focus, pop_age, pop_n, sev_strat, int_name, int_techniques,
      dose_band, dose_n_sessions, fac_type, del_format, eff_direction,
      eff_value (headline only), amstar2_band (inherited), rob_fatal_flaw,
      source_review_ids, rq_tags, workstream, template_section.
    Set every other field to value = null and span = "" WITHOUT adding
    to fields_flagged_for_human.

13. Sourcing-review link: for every primary-study record, populate
    source_review_ids with the sid(s) of the review(s) the study was
    reached through. If more than one, list all.

14. Deduplication (protocol §4.1.2): a primary study cited by more than
    one review is extracted ONCE. If this record has already been
    extracted (indicated by RECORD TIER = "dedup_append"), do NOT
    re-extract the schema; instead, return an object with only sid and
    source_review_ids populated, listing the sids of all sourcing
    reviews (existing + new).

15. Linked publications: where the same primary study has multiple
    publications (protocol, main results, cost, long-term follow-up),
    the extractor is prompted with the segmented text of the INDEX
    publication (most recent / most complete). Any cost or long-term
    follow-up data from a companion publication has been merged into
    the segmented text ahead of extraction and tagged with its section
    label (e.g., "Cost 4.1 [companion: doi/XXXX]"). Populate the fields
    from those companion sections normally.

16. RCT quality items feed the fatal-flaw ROB judgement (rob_fatal_flaw).
    For each of rct_randomisation_method, rct_allocation_concealment,
    rct_blinding_participants, rct_blinding_outcome_assessor,
    rct_itt_analysis: assign a verdict from {Y, N, Unclear, NA} plus a
    verbatim span. Blinding of participants is usually infeasible in
    psychological trials — code as NA where the source acknowledges
    this rather than Unclear.

============================================================
[REVISION — pending user review — Fatal-Flaw ROB checklist]
============================================================

17. Fatal-Flaw ROB (FF-ROB) checklist (protocol §4.4; template supplied
    by the review team). For every primary study, run the 5-criterion
    checklist below. Each sub-item takes a single-select verdict of
    "No — proceed" or "Yes — STOP, high ROB". A "Yes" on ANY sub-item
    is a fatal flaw and sets ffrob_overall_decision =
    "High ROB — fatal flaw"; all sub-items "No" → ffrob_overall_decision
    = "Assign for full ROB assessment".

    Criteria (verbatim from the checklist):

    C1. Severe confounding or baseline non-equivalence
      C1a. Treatment and comparison groups differ substantially at
           baseline on key outcome determinants, and authors do not
           adequately adjust.
      C1b. Selection into treatment is clearly related to expected
           outcomes (e.g., self-selection reflecting motivation), and
           no credible strategy to address.
    C2. Differential attrition likely to bias results
      C2a. Overall attrition exceeds ~30% AND differs meaningfully
           between groups (or > 30% with no info on differential). If
           attrition is not discussed at all, do NOT downgrade.
      C2b. Attrition is likely related to outcomes AND authors do not
           demonstrate robustness (baseline comparisons of completers
           vs drop-outs, tipping-point / bounds analysis, etc.).
    C3. Outcome measurement clearly influenced by treatment status
      C3a. Outcome assessors knew treatment status AND no safeguards
           were implemented.
      C3b. Primary outcomes are self-reported AND subject to bias
           (social desirability, recall). NOTE for depression trials:
           PHQ-9 and equivalents are self-report by design. Fail C3b
           only when self-report is coupled with unblinded rating
           conditions OR the source itself flags the bias risk.
    C4. Evidence of selective reporting
      C4.  Outcomes listed in the methods section are omitted from the
           results reporting. Base the verdict ONLY on methods vs
           results within the paper; do NOT consult the protocol or
           trial registry at this stage.
    C5. Serious contamination or intervention deviations
      C5a. Large proportion of comparison group received the
           intervention, or large spillover, AND flagged as an issue
           by the author(s). Latent, unstated contamination → No.
      C5b. Authors state that intervention implementation differed
           fundamentally from intended.

    For every sub-item populated with "Yes", populate the matching
    _span field with a verbatim quote and the _notes field for that
    criterion with a ≤ 40-word rationale. Populate criterion-level
    notes ONLY when the study fails on that criterion.

18. Propagation to rob_fatal_flaw (§3.11 of the extraction fields doc):
    - ffrob_overall_decision = "High ROB — fatal flaw"
        → rob_fatal_flaw = "Fatal-flaw-present"
        → also populate ffrob_failed_criteria with the failing sub-items
    - ffrob_overall_decision = "Assign for full ROB assessment"
        → rob_fatal_flaw = "No-fatal-flaw"
    - Review-level record → rob_fatal_flaw = "Not-applicable"; all
      ffrob_* fields null.

19. Downstream consequence of a fatal flaw:
    - The record is EXCLUDED from effect-size, dose, and cost
      tabulations.
    - The record is retained at record_tier = "reduced" for descriptive
      / narrative purposes.
    - Extractor does not gate inclusion on FF-ROB; that gating happens
      at synthesis. Populate every field as usual; the FF-ROB verdict
      is a flag, not a stop-word.

============================================================
[REVISION — pending user review — AMSTAR-2 itemised checklist]
============================================================

20. AMSTAR-2 (Shea et al. 2017) is the sole quality appraisal for every
    review-level record (systematic review, meta-analysis, meta-
    regression, IPD-MA, network-MA, umbrella review, dismantling-
    component MA). AMSTAR-2 is NOT applied to primary studies (they
    use the FF-ROB checklist, rules 17–19).

21. Protocol mode: RAPID CRITICAL-DOMAINS. Seven critical items are
    mandatory on every review:
      I2  Protocol registered before review, deviations justified
      I4  Comprehensive literature search
      I7  Excluded-studies list with justification
      I9  Satisfactory RoB technique for individual studies
      I11 Appropriate meta-analytic methods (if MA performed)
      I13 RoB accounted for in interpreting results
      I15 Publication bias assessed (if quantitative synthesis)
    Nine non-critical items (I1, I3, I5, I6, I8, I10, I12, I14, I16)
    are optional in rapid mode but should be coded on borderline
    reviews or where a critical item's verdict depends on them.

22. Per-item response coding:
    - I1, I3, I5, I6, I10, I12, I13, I14, I15, I16: Yes / No
      (I11, I12, I15 also permit "No MA conducted")
    - I2, I4, I7, I8: Yes / Partial Yes / No
    - I9: Yes / Partial Yes / No / Includes only NRSI / Includes only RCTs

23. For every item populated, extract a verbatim span (≤ 40 words) from
    the review's methods, participants, results, or discussion — never
    from the abstract. For critical items span + notes are mandatory.
    For non-critical items span is required whenever the verdict is not
    a clear Yes.

24. Confidence-band derivation (rapid mode):
    Let CRITWEAK = count of critical weaknesses among I2, I4, I7, I9,
    I11, I13, I15. A "critical weakness" is a verdict of "No" OR (for
    I2, I4, I7 only) "Partial Yes". "No MA conducted" on I11 or I15
    does NOT count as a weakness. "Includes only RCTs" / "Includes only
    NRSI" on I9 does NOT count as a weakness.

    Let NONCRIT = count of non-critical items with verdict "No" or
    "Partial Yes" (when non-critical items are coded).

    Derived band (populate amstar2_band_derived):
      - "High"           if CRITWEAK = 0 AND NONCRIT ≤ 1
      - "Moderate"       if CRITWEAK = 0 AND NONCRIT > 1
      - "Low"            if CRITWEAK = 1
      - "Critically-low" if CRITWEAK > 1

    Populate amstar2_band with the derived band unless a stated
    override applies (inherited from an in-corpus umbrella review) —
    in which case set amstar2_band = umbrella's rating, set
    amstar2_inherited = true, populate amstar2_inherited_source with
    the umbrella's sid, and record the override reason in
    amstar2_override_reason.

25. Review-only RQs (RQ1, RQ2, RQ3, RQ4, RQ11, RQ15, RQ17, RQ18):
    - Primary-study drill-down is not performed for these RQs. Every
      finding fed into Section 6 of the report is inherited from a
      review-level record.
    - The amstar2_band of the sourcing review IS the confidence rating
      for the RQ finding at synthesis.
    - Extractor does not gate inclusion on amstar2_band, but must
      correctly compute it — the synthesis step uses it to assign
      Section 2 dashboard letters (H/M/L/Ins.) and to route
      Critically-low findings into §10 Evidence gaps.

26. For a review already appraised by AMSTAR-2 inside a corpus umbrella
    review (e.g., meta-analyses inside Jeitani et al. 2024): do NOT
    re-rate. Set amstar2_inherited = true, populate amstar2_inherited_source,
    and copy the umbrella's band into amstar2_band. Populate individual
    amstar2_i{N} fields with null and empty spans — do NOT flag them
    for human review.

============================================================
[REVISION — pending user review — sub-modules, multi-outcome, RQ matrix]
============================================================

27. Sub-module decomposition (§3.4A of the extraction fields doc). When
    the source paper or review reports at the sub-module level for a
    named programme (IPT-G role-disputes / role-transitions / grief /
    interpersonal-deficits; PM+ managing-stress / managing-problems /
    get-going / social-support / staying-well; HAP behavioural-
    activation-core / problem-solving / relapse-prevention; THP, CBT-
    brief, PST, MI, SSI, Friendship Bench, etc.), populate the
    int_submodules field with values from the vocabulary keyed to the
    record's int_name / programme_family. If the source only reports
    programme-level content without sub-module granularity, leave every
    §3.4A field null and DO NOT flag for human review.

    Sub-module vocabulary (keyed by programme):

    IPT-G / IPT-classic:
      Interpersonal-inventory-assessment / Grief / Role-disputes /
      Role-transitions / Interpersonal-deficits /
      Termination-relapse-prevention

    PM+ (Problem Management Plus):
      Managing-stress / Managing-problems / Get-going-keep-doing (BA) /
      Strengthening-social-support / Staying-well-looking-forward

    HAP (Healthy Activity Program):
      Engagement-values / Behavioural-activation-core /
      Activity-monitoring-scheduling / Problem-solving-component /
      Relapse-prevention

    THP (Thinking Healthy Programme, perinatal):
      Preparing-for-baby / Baby-arrival / Mother-and-baby /
      Baby-and-others / Health-information-module

    Friendship Bench:
      Kufungisisa-framing / Kusimudzira / Kusimbisa /
      Peer-support-groups

    CBT-brief / Common-Elements-CBT:
      Psychoeducation-CBT / Behavioural-activation /
      Cognitive-restructuring-core / Problem-solving-CBT /
      Relaxation-stress-management / Exposure /
      Relapse-prevention-CBT

    PST / PST-based:
      Problem-orientation / Define-problem / Generate-solutions /
      Evaluate-select / Implement-review

    BA-based (stand-alone):
      Activity-monitoring / Activity-scheduling / Values-clarification-BA /
      Skills-training-BA / Relapse-prevention-BA

    MI-based:
      Engaging / Focusing / Evoking / Planning

    SSI-protocolised:
      Psychoeducation-SSI / Values-clarification-SSI /
      Behavioural-commitment-SSI

    Peer-support programmes:
      Structured-peer-listening / Skill-modelling /
      Mutual-aid-group-process / Referral-facilitation

    Other-programme:
      Other-submodule (specify)

    submodule_session_share_pct is a JSON object mapping each populated
    sub-module to its % of planned session minutes; the values must sum
    to ≤ 100. submodule_contribution captures the source's stated
    contribution of each sub-module (e.g., "role-transitions carried the
    largest SMD (0.62); role-disputes null"). submodule_evidence_type
    marks the design (Dismantling-MA / Component-MA / Network-MA /
    Meta-regression / Head-to-head-trial-sub-arm / Narrative /
    Session-share-only / Other).

28. Additional DEPRESSION outcomes — repeating structure (§3.8A). §3.7
    captures ONE primary depression outcome; §3.8 captures follow-up
    SMDs at fixed post / 3m / 6m / 12m. §3.8A adds an array named
    additional_outcomes where every entry describes ONE {depression
    construct × instrument × time-point × subgroup × adjustment}
    combination the study reports.

    SCOPE — DEPRESSION OUTCOMES ONLY. §3.8A entries are restricted to
    depression constructs measured on validated depression instruments.
    Non-depression outcomes are captured elsewhere in the schema:
      - Cost outcomes → §3.7 cost_* fields.
      - Engagement (attendance, completion, adherence, fidelity,
        acceptability) → §3.9 engage_* fields.
      - Safety (adverse events, suicidality, referral pathway) → §3.9
        ae_*, safety_* fields.
      - Psychometric outcomes (RQ18) → §3.10 psychom_* fields.
      - Attrition → §3A.4.3 attrition_* fields.
    If the study reports an anxiety, functioning, wellbeing, or QoL
    outcome alongside depression, do NOT create an additional_outcomes
    entry for it. Note it in the top-level notes field.

    Extract every reported depression outcome — including studies that
    report the same depression construct on multiple instruments (a
    study reporting BOTH PHQ-9 and BDI-II gets two entries per
    time-point), subgroup breakdowns (severe stratum, perinatal, women,
    urban sites), and adjusted / unadjusted estimates.

    Each entry uses the shape defined in the schema below with:
    outcome_id, outcome_construct, outcome_scale, outcome_scale_direction,
    outcome_is_primary, outcome_timepoint_weeks, outcome_timepoint_label,
    outcome_timepoint_verbatim, outcome_eff_metric, outcome_eff_value,
    outcome_eff_se, outcome_eff_ci_lo, outcome_eff_ci_hi, outcome_eff_p,
    outcome_n_int, outcome_n_ctrl, outcome_arm_int_mean, outcome_arm_int_sd,
    outcome_arm_ctrl_mean, outcome_arm_ctrl_sd, outcome_eff_direction,
    outcome_adjusted_flag, outcome_adjustment_covariates, outcome_subgroup,
    outcome_source_location, outcome_quote.

    Rules:
    - No cap on the number of entries per study — extract every reported
      outcome × time-point.
    - The primary outcome entries at post-intervention should be
      consistent with the §3.7 fields (they are a superset, not a
      replacement).
    - outcome_timepoint_verbatim quotes the source's own label
      ("week 12", "T3", "end of therapy") so downstream code can
      normalise to outcome_timepoint_weeks.

29. Study-to-RQ contribution matrix (§3.12A). Every extracted record
    also populates an array named rq_contributions with ONE entry per
    RQ the study contributes to. A study contributing to RQ5, RQ7, and
    RQ14 has three entries in rq_contributions.

    Each entry uses:
    - rq_id                        RQ1 … RQ18
    - rq_contribution_type         Primary-evidence / Supporting-evidence /
                                   Contextual-evidence / Contradictory-evidence /
                                   Descriptive-only / Not-applicable
    - rq_contribution_summary      ≤ 30-word summary of what the study
                                   says about this RQ
    - rq_contribution_strength     High / Moderate / Low / Insufficient
                                   (derived from amstar2_band for review
                                   records; from ffrob_overall_decision +
                                   record_tier for primary studies)
    - rq_contribution_direction    Confirms / Refutes / Qualifies /
                                   Neutral / Not-applicable
    - rq_contribution_data_fields  List of field IDs (e.g.,
                                   ["eff_value", "eff_3m_smd", "dose_band"])
    - rq_contribution_template_section Duplicates template_section at
                                   the per-RQ granularity
    - rq_contribution_quote        Verbatim span backing the summary

    Rules:
    - The LLM re-reads the paper against the full RQ set at extraction
      time; it may ADD RQs not in rq_tags_hint and REMOVE RQs the
      hint suggested but the paper does not actually address.
    - Every rq_id in rq_contributions must also appear in the top-level
      rq_tags field (§3.12).
    - Fatal-flaw studies still get a rq_contributions entry per RQ, but
      rq_contribution_strength is set to "Insufficient" and
      rq_contribution_type to "Descriptive-only".
    - The exported extraction sheet materialises rq_contributions as
      long-format: one row per {sid × rq_id}. The synthesis team filters
      by rq_id to build per-RQ evidence tables and Section 2 dashboard
      confidence letters.

────────────────────────────────────────────────────────────────
C. JSON output schema (uniform provenance shape)
────────────────────────────────────────────────────────────────

Every field uses { "value": ..., "span": "", "section": "" } except
extractor_confidence (float 0–1) and fields_flagged_for_human (array of
strings). Return exactly this schema; do not add fields; do not omit
fields.

{
  // Study identification (D.1)
  "sid":              { "value": "{{record_id}}", "span": "", "section": "" },
  "cit":              { "value": "<Vancouver citation>", "span": "", "section": "" },
  "doi":              { "value": "<DOI/URL or null>", "span": "", "section": "" },
  "yr":               { "value": <int|null>, "span": "", "section": "" },
  "lang":             { "value": "<D.8 lang>", "span": "", "section": "" },
  "doctype":          { "value": "<D.1 doctype>", "span": "", "section": "" },

  // Design and context (D.2)
  "design":           { "value": "<D.8.1>", "span": "", "section": "" },
  "country":          { "value": ["<ISO-3>"], "span": "", "section": "" },
  "stream":           { "value": "Stream1-Barbui-anchored" | "Stream2-seed" | "Internal", "span": "", "section": "" },
  "geo_focus":        { "value": "SSA" | "other-LMIC" | "HIC-UMIC" | "mixed", "span": "", "section": "" },
  "setting":          { "value": ["<D.2 setting list>"], "span": "", "section": "" },
  "pop_age":          { "value": "Adult (≥18)" | "Perinatal women" | "Mixed adult+adolescent" | "Other", "span": "", "section": "" },
  "pop_n":            { "value": <int|null>, "span": "", "section": "" },
  "pop_descr":        { "value": "<free text>", "span": "", "section": "" },

  // Severity stratum (D.3)
  "sev_strat":        { "value": "<D.8.2>", "span": "", "section": "" },
  "sev_scale":        { "value": "<D.3 sev_scale>", "span": "", "section": "" },
  "sev_cutoff":       { "value": <num|null>, "span": "", "section": "" },
  "sev_quote":        { "value": "<verbatim>", "span": "", "section": "" },

  // Intervention (D.4)
  "int_name":         { "value": "<name>", "span": "", "section": "" },
  "int_techniques":   { "value": ["<≥1 of D.8.3a>"], "span": "", "section": "" },
  "int_adaptations":  { "value": ["<0+ of D.8.3b>"], "span": "", "section": "" },
  "int_theory":       { "value": "<free text or null>", "span": "", "section": "" },
  "int_quote":        { "value": "<verbatim ingredient description>", "span": "", "section": "" },

  // Sub-module decomposition (§3.4A)  [REVISION]
  "int_submodules":              { "value": ["<from vocabulary keyed by programme>"], "span": "", "section": "" },
  "submodule_session_share_pct": { "value": { "<sub-module>": <int 0–100> }, "span": "", "section": "" },
  "submodule_contribution":      { "value": "<free text per sub-module>", "span": "", "section": "" },
  "submodule_evidence_type":     { "value": "Dismantling-MA" | "Component-MA" | "Network-MA" | "Meta-regression" | "Head-to-head-trial-sub-arm" | "Narrative" | "Session-share-only" | "Other", "span": "", "section": "" },
  "submodule_quote":             { "value": "<verbatim if any submodule_* set>", "span": "", "section": "" },
  "submodule_manual_reference":  { "value": "<free text>", "span": "", "section": "" },

  // Dose (D.5)
  "dose_n_sessions":         { "value": <int|null>, "span": "", "section": "" },
  "dose_session_min":        { "value": <num|null>, "span": "", "section": "" },
  "dose_total_contact_min":  { "value": <num|null>, "span": "", "section": "" },
  "dose_homework_min":       { "value": <num|null>, "span": "", "section": "" },
  "dose_band":               { "value": "<D.8.4>", "span": "", "section": "" },
  "dose_freq":               { "value": "Daily" | "Weekly" | "Fortnightly" | "Monthly" | "Self-paced" | "Irregular" | "Not-reported", "span": "", "section": "" },
  "dose_duration_wk":        { "value": <num|null>, "span": "", "section": "" },
  "dose_delivered_pct":      { "value": <num|null>, "span": "", "section": "" },
  "dose_quote":              { "value": "<verbatim>", "span": "", "section": "" },

  // Facilitator and delivery (D.6)
  "fac_type":          { "value": "<D.8.5>", "span": "", "section": "" },
  "fac_train_h":       { "value": <num|null>, "span": "", "section": "" },
  "fac_super":         { "value": "<D.6 super>", "span": "", "section": "" },
  "fac_clinical_background": { "value": "<see notes>", "span": "", "section": "" },
  "del_format":        { "value": "<D.8.6>", "span": "", "section": "" },
  "del_modality":      { "value": "In-person" | "Phone" | "Video" | "Hybrid", "span": "", "section": "" },
  "del_lang":          { "value": ["<languages>"], "span": "", "section": "" },
  "group_size_mean":   { "value": <num|null>, "span": "", "section": "" },
  "group_size_range":  { "value": "<min–max or null>", "span": "", "section": "" },

  // Outcomes and effects (D.7)
  "out_primary":       { "value": "<free text>", "span": "", "section": "" },
  "out_scale":         { "value": "<D.7 scale>", "span": "", "section": "" },
  "out_timepoint":     { "value": <num|null>, "span": "", "section": "" },
  "eff_metric":        { "value": "SMD" | "Cohen d" | "Mean-diff" | "RR" | "OR" | "Remission-rate" | "Other", "span": "", "section": "" },
  "eff_value":         { "value": <num|null>, "span": "", "section": "" },
  "eff_se":            { "value": <num|null>, "span": "", "section": "" },
  "eff_ci_lo":         { "value": <num|null>, "span": "", "section": "" },
  "eff_ci_hi":         { "value": <num|null>, "span": "", "section": "" },
  "eff_n_int":         { "value": <int|null>, "span": "", "section": "" },
  "eff_n_ctrl":        { "value": <int|null>, "span": "", "section": "" },
  "eff_direction":     { "value": "Favours-intervention" | "Null" | "Favours-control" | "Unclear", "span": "", "section": "" },
  "eff_quote":         { "value": "<verbatim>", "span": "", "section": "" },

  // Durability / temporal effects (needed for RQ8, RQ14). For every time-point:
  // extract the estimate, its SE, its CI, and the arm-level Ns behind it.
  // Extract SE as reported; NEVER back-calculate SE from CI or p-value.
  "eff_post_smd":      { "value": <num|null>, "span": "", "section": "" },
  "eff_post_se":       { "value": <num|null>, "span": "", "section": "" },
  "eff_post_ci_lo":    { "value": <num|null>, "span": "", "section": "" },
  "eff_post_ci_hi":    { "value": <num|null>, "span": "", "section": "" },
  "n_at_post_int":     { "value": <int|null>, "span": "", "section": "" },
  "n_at_post_ctrl":    { "value": <int|null>, "span": "", "section": "" },
  "eff_3m_smd":        { "value": <num|null>, "span": "", "section": "" },
  "eff_3m_se":         { "value": <num|null>, "span": "", "section": "" },
  "eff_3m_ci_lo":      { "value": <num|null>, "span": "", "section": "" },
  "eff_3m_ci_hi":      { "value": <num|null>, "span": "", "section": "" },
  "n_at_3m_int":       { "value": <int|null>, "span": "", "section": "" },
  "n_at_3m_ctrl":      { "value": <int|null>, "span": "", "section": "" },
  "eff_6m_smd":        { "value": <num|null>, "span": "", "section": "" },
  "eff_6m_se":         { "value": <num|null>, "span": "", "section": "" },
  "eff_6m_ci_lo":      { "value": <num|null>, "span": "", "section": "" },
  "eff_6m_ci_hi":      { "value": <num|null>, "span": "", "section": "" },
  "n_at_6m_int":       { "value": <int|null>, "span": "", "section": "" },
  "n_at_6m_ctrl":      { "value": <int|null>, "span": "", "section": "" },
  "eff_12m_smd":       { "value": <num|null>, "span": "", "section": "" },
  "eff_12m_se":        { "value": <num|null>, "span": "", "section": "" },
  "eff_12m_ci_lo":     { "value": <num|null>, "span": "", "section": "" },
  "eff_12m_ci_hi":     { "value": <num|null>, "span": "", "section": "" },
  "n_at_12m_int":      { "value": <int|null>, "span": "", "section": "" },
  "n_at_12m_ctrl":     { "value": <int|null>, "span": "", "section": "" },
  "remission_pct_int": { "value": <num|null>, "span": "", "section": "" },
  "remission_pct_ctrl":{ "value": <num|null>, "span": "", "section": "" },
  "temporal_quote":    { "value": "<verbatim, if any eff_*m_smd set>", "span": "", "section": "" },

  // Additional DEPRESSION outcomes — repeating array (§3.8A)  [REVISION]
  // SCOPE: depression outcomes only. Non-depression constructs are captured
  // in dedicated blocks (cost §3.7 cost_*, engagement §3.9 engage_*,
  // safety §3.9 ae_/safety_*, psychometrics §3.10 psychom_*, attrition
  // §3A.4.3). Do NOT create an entry for anxiety/functioning/wellbeing/QoL.
  //
  // Extract EVERY depression outcome × instrument × timepoint × subgroup ×
  // adjustment combination. No cap. The primary-outcome-at-post entries
  // here should be consistent with §3.7 (superset, not replacement).
  "additional_outcomes": [
    {
      "outcome_id":                 { "value": "<auto: sid-out-NN>", "span": "", "section": "" },
      "outcome_construct":          { "value": "Depression-symptoms" | "Depression-remission" | "Depression-response" | "Depression-recovery" | "Depression-relapse", "span": "", "section": "" },
      "outcome_scale":              { "value": "PHQ-9" | "PHQ-2" | "CES-D" | "CES-D-10" | "HAMD" | "HDRS" | "MADRS" | "BDI" | "BDI-II" | "EPDS" | "QIDS" | "Zung-SDS" | "GDS" | "DASS-21-depression-subscale" | "SCL-90-depression-subscale" | "SRQ-20-depression-items" | "K10-depression-cutoff" | "MINI-depression-module" | "CIDI-depression-module" | "SCID-depression-module" | "Other-depression-instrument", "span": "", "section": "" },
      "outcome_scale_direction":    { "value": "Higher-is-worse" | "Higher-is-better" | "Not-applicable", "span": "", "section": "" },
      "outcome_is_primary":         { "value": <bool>, "span": "", "section": "" },
      "outcome_timepoint_weeks":    { "value": <num|null>, "span": "", "section": "" },
      "outcome_timepoint_label":    { "value": "Baseline" | "Mid-intervention" | "End-of-intervention" | "Post-intervention" | "1-month" | "3-month" | "6-month" | "9-month" | "12-month" | "18-month" | "24-month" | "Study-defined-other", "span": "", "section": "" },
      "outcome_timepoint_verbatim": { "value": "<verbatim label from source>", "span": "", "section": "" },
      "outcome_eff_metric":         { "value": "SMD" | "Cohen d" | "Hedges g" | "Mean-diff" | "Mean-change" | "Adjusted-mean-diff" | "RR" | "OR" | "HR" | "IRR" | "Remission-rate" | "Response-rate" | "Recovery-rate" | "Correlation" | "Other", "span": "", "section": "" },
      "outcome_eff_value":          { "value": <num|null>, "span": "", "section": "" },
      "outcome_eff_se":             { "value": <num|null>, "span": "", "section": "" },
      "outcome_eff_ci_lo":          { "value": <num|null>, "span": "", "section": "" },
      "outcome_eff_ci_hi":          { "value": <num|null>, "span": "", "section": "" },
      "outcome_eff_p":              { "value": "<free text as reported>", "span": "", "section": "" },
      "outcome_n_int":              { "value": <int|null>, "span": "", "section": "" },
      "outcome_n_ctrl":             { "value": <int|null>, "span": "", "section": "" },
      "outcome_arm_int_mean":       { "value": <num|null>, "span": "", "section": "" },
      "outcome_arm_int_sd":         { "value": <num|null>, "span": "", "section": "" },
      "outcome_arm_ctrl_mean":      { "value": <num|null>, "span": "", "section": "" },
      "outcome_arm_ctrl_sd":        { "value": <num|null>, "span": "", "section": "" },
      "outcome_eff_direction":      { "value": "Favours-intervention" | "Null" | "Favours-control" | "Unclear", "span": "", "section": "" },
      "outcome_adjusted_flag":      { "value": <bool>, "span": "", "section": "" },
      "outcome_adjustment_covariates": { "value": "<free text>", "span": "", "section": "" },
      "outcome_subgroup":           { "value": "<free text>", "span": "", "section": "" },
      "outcome_source_location":    { "value": "<free text e.g. 'Table 3, row 4'>", "span": "", "section": "" },
      "outcome_quote":              { "value": "<verbatim ≤ 40 words>", "span": "", "section": "" }
    }
  ],
  "trajectory_shape":  { "value": "Sustained" | "Decaying" | "Rebounding" | "Sleeper" | "Not-reported", "span": "", "section": "" },
  "sleeper_effect_flag": { "value": <bool>, "span": "", "section": "" },
  "mechanism_claim":   { "value": "<free text or null>", "span": "", "section": "" },
  "mediation_analysis":{ "value": <bool>, "span": "", "section": "" },
  "mediator_named":    { "value": "<free text or null>", "span": "", "section": "" },

  // Engagement (needed for RQ15)
  "engage_attendance_pct":  { "value": <num|null>, "span": "", "section": "" },
  "engage_completion_pct":  { "value": <num|null>, "span": "", "section": "" },
  "engage_dropout_pct":     { "value": <num|null>, "span": "", "section": "" },
  "engage_reasons":         { "value": "<free text>", "span": "", "section": "" },
  "engage_quote":           { "value": "<verbatim if any engage_* set>", "span": "", "section": "" },

  // Safety (needed for RQ17)
  "ae_reported":       { "value": <bool>, "span": "", "section": "" },
  "ae_type":           { "value": "<free text if ae_reported>", "span": "", "section": "" },
  "ae_rate":           { "value": "<free text if ae_reported>", "span": "", "section": "" },
  "safety_pathway":    { "value": <bool>, "span": "", "section": "" },
  "safety_pathway_desc": { "value": "<free text if safety_pathway>", "span": "", "section": "" },
  "safety_quote":      { "value": "<verbatim if ae_reported or safety_pathway>", "span": "", "section": "" },
  "safety_monitoring_practice": { "value": ["<multi-select>"], "span": "", "section": "" },
  "referral_pathway_type":      { "value": "<single-select>", "span": "", "section": "" },
  "step_up_trigger_ae":         { "value": "<free text>", "span": "", "section": "" },

  // Cost (needed for RQ16; other RQs opportunistically)
  "cost_reported":     { "value": <bool>, "span": "", "section": "" },
  "cost_per_pt_local": { "value": <num|null>, "span": "", "section": "" },
  "cost_currency":     { "value": "<ISO-4217 or null>", "span": "", "section": "" },
  "cost_year":         { "value": <int|null>, "span": "", "section": "" },
  "cost_perspective":  { "value": "Provider" | "Societal" | "Health-system" | "Mixed" | "Not-reported", "span": "", "section": "" },
  "cost_components":   { "value": "<free text>", "span": "", "section": "" },
  "cost_metric":       { "value": ["<multi-select>"], "span": "", "section": "" },
  "cost_driver":       { "value": ["<multi-select>"], "span": "", "section": "" },
  "cost_driver_share_pct": { "value": "<free text>", "span": "", "section": "" },
  "cost_effectiveness_ratio": { "value": "<free text or null>", "span": "", "section": "" },
  "ce_threshold_used": { "value": "<free text or null>", "span": "", "section": "" },
  "sensitivity_analysis_flag": { "value": <bool>, "span": "", "section": "" },
  "cost_quote":        { "value": "<verbatim if cost_reported>", "span": "", "section": "" },

  // Instrument validation (RQ18)
  "psychom_instrument":     { "value": "<D.3 sev_scale>", "span": "", "section": "" },
  "psychom_setting":        { "value": "<free text>", "span": "", "section": "" },
  "psychom_language":       { "value": ["<free text list>"], "span": "", "section": "" },
  "psychom_sensitivity":    { "value": <num|null>, "span": "", "section": "" },
  "psychom_specificity":    { "value": <num|null>, "span": "", "section": "" },
  "psychom_cutoff":         { "value": <num|null>, "span": "", "section": "" },
  "psychom_reliability":    { "value": "<free text or null>", "span": "", "section": "" },
  "psychom_construct":      { "value": "<free text or null>", "span": "", "section": "" },
  "psychom_quote":          { "value": "<verbatim if any psychom_* set>", "span": "", "section": "" },

  // RQ1 driver-specific
  "driver_domain":     { "value": ["Biological" | "Psychological" | "Social" | "Economic" | "Structural" | "Environmental" | "Life-course"], "span": "", "section": "" },
  "driver_named":      { "value": ["<free text list, verbatim wording>"], "span": "", "section": "" },
  "driver_direction":  { "value": "<free text>", "span": "", "section": "" },
  "driver_effect_size":{ "value": "<free text>", "span": "", "section": "" },
  "driver_ssa_flag":   { "value": <bool>, "span": "", "section": "" },
  "driver_quote":      { "value": "<verbatim per named driver>", "span": "", "section": "" },

  // RQ2 programme-family
  "programme_family":  { "value": ["IPT-G" | "PM+" | "HAP" | "Friendship-Bench" | "THP" | "BA-based" | "PST-based" | "MI-based" | "SSI-protocolised" | "Mixed" | "Other"], "span": "", "section": "" },
  "rationale_stated":  { "value": "<free text>", "span": "", "section": "" },
  "feasibility_evidence": { "value": "<free text>", "span": "", "section": "" },
  "outcome_timeframe": { "value": ["short" | "medium" | "long"], "span": "", "section": "" },

  // RQ3 scalability
  "scalability_parameters": { "value": ["<multi-select>"], "span": "", "section": "" },
  "scalability_direction":  { "value": "<free text>", "span": "", "section": "" },
  "scalability_barriers":   { "value": "<free text>", "span": "", "section": "" },
  "scalability_enablers":   { "value": "<free text>", "span": "", "section": "" },

  // RQ4 training/supervision
  "train_curriculum":  { "value": "<free text>", "span": "", "section": "" },
  "train_duration_h":  { "value": <num|null>, "span": "", "section": "" },
  "train_boosters":    { "value": "<free text>", "span": "", "section": "" },
  "super_frequency":   { "value": "Weekly" | "Fortnightly" | "Monthly" | "Ad-hoc" | "Not-reported", "span": "", "section": "" },
  "super_modality":    { "value": "Individual" | "Group" | "Case-based" | "Peer-to-peer" | "Cascade-with-specialist" | "Cascade-lay-only", "span": "", "section": "" },
  "super_specialist_backstop": { "value": <bool>, "span": "", "section": "" },
  "fac_selection_criteria":    { "value": "<free text>", "span": "", "section": "" },

  // RQ5 / RQ6 component
  "component_contribution": { "value": "<free text per technique>", "span": "", "section": "" },
  "component_evidence_type":{ "value": "Dismantling-MA" | "Component-MA" | "Network-MA" | "Meta-regression" | "Head-to-head-trials" | "Narrative" | "Other", "span": "", "section": "" },
  "dismantling_flag":       { "value": <bool>, "span": "", "section": "" },
  "component_drop_candidate": { "value": "<free text>", "span": "", "section": "" },
  "component_null_evidence": { "value": "<free text>", "span": "", "section": "" },
  "cost_savings_if_dropped": { "value": "<free text>", "span": "", "section": "" },

  // RQ7 dose-response
  "dose_response_curve":       { "value": "Linear" | "Log-linear" | "Plateau" | "U-shaped" | "Threshold" | "Not-reported", "span": "", "section": "" },
  "dose_response_inflection":  { "value": "<free text or numeric>", "span": "", "section": "" },
  "dose_response_by_severity": { "value": "<free text>", "span": "", "section": "" },
  "specialist_delivered_flag": { "value": <bool>, "span": "", "section": "" },

  // RQ9 SSI vs MSI
  "ssi_vs_msi_comparison": { "value": "Head-to-head" | "Indirect" | "Not-compared", "span": "", "section": "" },
  "ssi_effect_size":       { "value": <num|null>, "span": "", "section": "" },
  "msi_effect_size":       { "value": <num|null>, "span": "", "section": "" },
  "ssi_appropriate_conditions": { "value": "<free text>", "span": "", "section": "" },
  "triage_rule_reported":  { "value": <bool>, "span": "", "section": "" },
  "triage_threshold":      { "value": "<free text>", "span": "", "section": "" },

  // RQ10 group size
  "group_size_reported":   { "value": "Exact" | "Range" | "Modal" | "Not-reported", "span": "", "section": "" },
  "group_size_effect_evidence": { "value": "<free text>", "span": "", "section": "" },
  "group_size_upper_threshold": { "value": "<free text or numeric>", "span": "", "section": "" },
  "fidelity_score_at_size":     { "value": "<free text>", "span": "", "section": "" },

  // RQ11 spillover
  "sample_type":       { "value": "Universal-prevention" | "Selective-prevention" | "Indicated" | "Household" | "Community" | "Mixed", "span": "", "section": "" },
  "non_case_definition": { "value": "<free text>", "span": "", "section": "" },
  "spillover_direction": { "value": "Symptom-reduction" | "Wellbeing-gain" | "Service-uptake" | "Null" | "Adverse", "span": "", "section": "" },
  "spillover_target":    { "value": "<free text>", "span": "", "section": "" },
  "spillover_mechanism": { "value": "<free text>", "span": "", "section": "" },

  // RQ12 stepped care
  "stepped_care_steps":  { "value": "<free text describing each step>", "span": "", "section": "" },
  "allocation_rule":     { "value": "Universal" | "Triaged" | "Stratified" | "Adaptive" | "Not-reported", "span": "", "section": "" },
  "step_up_rule":        { "value": "<free text>", "span": "", "section": "" },
  "step_down_rule":      { "value": "<free text>", "span": "", "section": "" },
  "specialist_backstop": { "value": <bool>, "span": "", "section": "" },

  // RQ13 non-specialist delivery
  "delivery_model":     { "value": "Lay-only" | "Lay-with-specialist-supervision" | "Peer-only" | "Peer-with-supervision" | "Self-guided-with-facilitator" | "Mixed", "span": "", "section": "" },
  "hcd_features":       { "value": ["<multi-select>"], "span": "", "section": "" },
  "non_specialist_vs_specialist_delta": { "value": "<free text>", "span": "", "section": "" },

  // RQ14 trajectory
  "trajectory_by_model": { "value": "<free text>", "span": "", "section": "" },
  "relapse_reported":    { "value": <bool>, "span": "", "section": "" },
  "relapse_rate":        { "value": <num|null>, "span": "", "section": "" },
  "sustainability_conditions": { "value": "<free text>", "span": "", "section": "" },

  // RQ15 engagement design drivers
  "engagement_driver":   { "value": ["<multi-select>"], "span": "", "section": "" },
  "engagement_barrier":  { "value": ["<multi-select>"], "span": "", "section": "" },
  "adherence_definition":{ "value": "<free text>", "span": "", "section": "" },
  "engagement_effect_size": { "value": "<free text>", "span": "", "section": "" },

  // RQ17 safety adequacy
  "adequacy_evidence":   { "value": "<free text>", "span": "", "section": "" },
  "adequacy_gap":        { "value": "<free text>", "span": "", "section": "" },

  // ============================================================
  // [REVISION — pending user review]
  // Primary-study-specific fields (populate ONLY when
  // unit_of_extraction = "primary_study"; set null with span "" and do
  // NOT flag for human when unit_of_extraction = "review")
  // ============================================================

  // 3A.4.1 Sourcing and tier
  "record_tier":         { "value": "full" | "reduced", "span": "", "section": "" },
  "source_review_ids":   { "value": ["<review sid>"], "span": "", "section": "" },
  "linked_publications": { "value": ["<DOI or citation of companion pub>"], "span": "", "section": "" },

  // 3A.4.2 RCT quality items (feed fatal-flaw ROB)
  "rct_randomisation_method":    { "value": "<free text>", "span": "", "section": "" },
  "rct_allocation_concealment":  { "value": "Y" | "N" | "Unclear" | "NA", "span": "", "section": "" },
  "rct_blinding_participants":   { "value": "Y" | "N" | "Unclear" | "NA", "span": "", "section": "" },
  "rct_blinding_outcome_assessor": { "value": "Y" | "N" | "Unclear" | "NA", "span": "", "section": "" },
  "rct_itt_analysis":            { "value": "Y" | "N" | "Unclear" | "NA", "span": "", "section": "" },
  "rct_cluster_unit":            { "value": "<free text>", "span": "", "section": "" },
  "rct_icc_reported":            { "value": <bool>, "span": "", "section": "" },
  "rct_icc_value":               { "value": <num|null>, "span": "", "section": "" },
  "quasi_confounder_adjustment": { "value": "<free text>", "span": "", "section": "" },
  "rob_quote":                   { "value": "<verbatim>", "span": "", "section": "" },

  // 3A.4.6 Fatal-Flaw ROB checklist  [REVISION — pending user review]
  // Meta
  "ffrob_coding_pair":           { "value": "<free text>", "span": "", "section": "" },
  "ffrob_minutes":               { "value": <num|null>, "span": "", "section": "" },
  "ffrob_reviewer_notes":        { "value": "<free text>", "span": "", "section": "" },

  // Criterion 1 — Confounding / baseline non-equivalence
  "ffrob_c1a":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c1a_span":              { "value": "<verbatim if c1a=Yes>", "span": "", "section": "" },
  "ffrob_c1b":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c1b_span":              { "value": "<verbatim if c1b=Yes>", "span": "", "section": "" },
  "ffrob_c1_notes":              { "value": "<free text if c1 fails>", "span": "", "section": "" },

  // Criterion 2 — Differential attrition
  "ffrob_c2a":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c2a_span":              { "value": "<verbatim if c2a=Yes>", "span": "", "section": "" },
  "ffrob_c2b":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c2b_span":              { "value": "<verbatim if c2b=Yes>", "span": "", "section": "" },
  "ffrob_c2_notes":              { "value": "<free text if c2 fails>", "span": "", "section": "" },

  // Criterion 3 — Outcome measurement influenced by treatment
  "ffrob_c3a":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c3a_span":              { "value": "<verbatim if c3a=Yes>", "span": "", "section": "" },
  "ffrob_c3b":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c3b_span":              { "value": "<verbatim if c3b=Yes>", "span": "", "section": "" },
  "ffrob_c3_notes":              { "value": "<free text if c3 fails>", "span": "", "section": "" },

  // Criterion 4 — Selective reporting (methods vs results only)
  "ffrob_c4":                    { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c4_span":               { "value": "<verbatim (quote methods and omitted results)>", "span": "", "section": "" },
  "ffrob_c4_notes":              { "value": "<free text if c4 fails>", "span": "", "section": "" },

  // Criterion 5 — Contamination / intervention deviations
  "ffrob_c5a":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c5a_span":              { "value": "<verbatim if c5a=Yes>", "span": "", "section": "" },
  "ffrob_c5b":                   { "value": "No — proceed" | "Yes — STOP, high ROB", "span": "", "section": "" },
  "ffrob_c5b_span":              { "value": "<verbatim if c5b=Yes>", "span": "", "section": "" },
  "ffrob_c5_notes":              { "value": "<free text if c5 fails>", "span": "", "section": "" },

  // Overall
  "ffrob_overall_decision":      { "value": "Assign for full ROB assessment" | "High ROB — fatal flaw", "span": "", "section": "" },
  "ffrob_failed_criteria":       { "value": ["C1a" | "C1b" | "C2a" | "C2b" | "C3a" | "C3b" | "C4" | "C5a" | "C5b"], "span": "", "section": "" },

  // 3A.4.3 Attrition
  "attrition_overall_pct":       { "value": <num|null>, "span": "", "section": "" },
  "attrition_int_pct":           { "value": <num|null>, "span": "", "section": "" },
  "attrition_ctrl_pct":          { "value": <num|null>, "span": "", "section": "" },
  "differential_attrition_flag": { "value": <bool>, "span": "", "section": "" },
  "baseline_balance":            { "value": "<free text>", "span": "", "section": "" },
  "attrition_quote":             { "value": "<verbatim if any attrition_* set>", "span": "", "section": "" },

  // 3A.4.4 Trial provenance
  "trial_registered":            { "value": <bool>, "span": "", "section": "" },
  "trial_registry_id":           { "value": "<free text>", "span": "", "section": "" },
  "protocol_published":          { "value": <bool>, "span": "", "section": "" },
  "primary_outcome_prespec":     { "value": "Y" | "N" | "Unclear", "span": "", "section": "" },
  "funding_source":              { "value": "<free text>", "span": "", "section": "" },
  "coi_declared":                { "value": <bool>, "span": "", "section": "" },
  "coi_description":             { "value": "<free text>", "span": "", "section": "" },
  "provenance_quote":            { "value": "<verbatim>", "span": "", "section": "" },

  // 3A.4.5 Analytic sample by arm
  "n_enrolled_int":              { "value": <int|null>, "span": "", "section": "" },
  "n_enrolled_ctrl":             { "value": <int|null>, "span": "", "section": "" },
  "n_randomised_int":            { "value": <int|null>, "span": "", "section": "" },
  "n_randomised_ctrl":           { "value": <int|null>, "span": "", "section": "" },
  "arm_definitions":             { "value": "<free text>", "span": "", "section": "" },
  "arm_quote":                   { "value": "<verbatim if any arm-level N populated>", "span": "", "section": "" },
  // ============================================================
  // END REVISION block
  // ============================================================

  // ============================================================
  // [REVISION — pending user review]
  // AMSTAR-2 itemised checklist (review-level records only)
  // Set every amstar2_i{N} field to null with empty span when
  // unit_of_extraction = "primary_study" — do NOT flag for human.
  // ============================================================

  "amstar2_mode":        { "value": "Full (16-item)" | "Rapid critical-domains (7-item)", "span": "", "section": "" },

  // Item 1 (non-critical)
  "amstar2_i1":          { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i1_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i1_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 2 (CRITICAL)
  "amstar2_i2":          { "value": "Yes" | "Partial Yes" | "No", "span": "", "section": "" },
  "amstar2_i2_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i2_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 3 (non-critical)
  "amstar2_i3":          { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i3_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i3_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 4 (CRITICAL)
  "amstar2_i4":          { "value": "Yes" | "Partial Yes" | "No", "span": "", "section": "" },
  "amstar2_i4_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i4_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 5 (non-critical)
  "amstar2_i5":          { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i5_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i5_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 6 (non-critical)
  "amstar2_i6":          { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i6_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i6_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 7 (CRITICAL)
  "amstar2_i7":          { "value": "Yes" | "Partial Yes" | "No", "span": "", "section": "" },
  "amstar2_i7_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i7_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 8 (non-critical)
  "amstar2_i8":          { "value": "Yes" | "Partial Yes" | "No", "span": "", "section": "" },
  "amstar2_i8_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i8_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 9 (CRITICAL)
  "amstar2_i9":          { "value": "Yes" | "Partial Yes" | "No" | "Includes only NRSI" | "Includes only RCTs", "span": "", "section": "" },
  "amstar2_i9_span":     { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i9_notes":    { "value": "<free text>", "span": "", "section": "" },

  // Item 10 (non-critical)
  "amstar2_i10":         { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i10_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i10_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Item 11 (CRITICAL)
  "amstar2_i11":         { "value": "Yes" | "No" | "No MA conducted", "span": "", "section": "" },
  "amstar2_i11_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i11_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Item 12 (non-critical)
  "amstar2_i12":         { "value": "Yes" | "No" | "No MA conducted", "span": "", "section": "" },
  "amstar2_i12_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i12_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Item 13 (CRITICAL)
  "amstar2_i13":         { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i13_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i13_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Item 14 (non-critical)
  "amstar2_i14":         { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i14_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i14_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Item 15 (CRITICAL)
  "amstar2_i15":         { "value": "Yes" | "No" | "No MA conducted", "span": "", "section": "" },
  "amstar2_i15_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i15_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Item 16 (non-critical)
  "amstar2_i16":         { "value": "Yes" | "No", "span": "", "section": "" },
  "amstar2_i16_span":    { "value": "<verbatim>", "span": "", "section": "" },
  "amstar2_i16_notes":   { "value": "<free text>", "span": "", "section": "" },

  // Derived and provenance
  "amstar2_critical_weaknesses":         { "value": ["I2" | "I4" | "I7" | "I9" | "I11" | "I13" | "I15"], "span": "", "section": "" },
  "amstar2_noncritical_weaknesses_count":{ "value": <int|null>, "span": "", "section": "" },
  "amstar2_band_derived":                { "value": "High" | "Moderate" | "Low" | "Critically-low", "span": "", "section": "" },
  "amstar2_override_reason":             { "value": "<free text or null>", "span": "", "section": "" },
  "amstar2_inherited_source":            { "value": "<sid of umbrella or null>", "span": "", "section": "" },
  // ============================================================
  // END AMSTAR-2 revision block
  // ============================================================

  // Quality / provenance (D.9) — existing summary fields (retained)
  "amstar2_band":        { "value": "High" | "Moderate" | "Low" | "Critically-low", "span": "", "section": "" },
  "amstar2_inherited":   { "value": <bool>, "span": "", "section": "" },
  "rob_fatal_flaw":      { "value": "Fatal-flaw-present" | "No-fatal-flaw" | "Not-applicable", "span": "", "section": "" },

  // RQ tagging + report mapping (§3.12)
  "rq_tags":             { "value": ["RQ1" | "RQ2" | … | "RQ18"], "span": "", "section": "" },
  "workstream":          { "value": ["A" | "B" | "C" | "D"], "span": "", "section": "" },
  "template_section":    { "value": ["<see Section E>"], "span": "", "section": "" },
  "rq11_only":           { "value": <bool>, "span": "", "section": "" },

  // Study-to-RQ contribution matrix — repeating array (§3.12A)  [REVISION]
  // One entry per RQ this study contributes to. Every rq_id here must
  // also appear in rq_tags above. The exported extraction sheet
  // materialises this array as long-format (one row per {sid × rq_id}).
  "rq_contributions": [
    {
      "rq_id":                          { "value": "RQ1" | "RQ2" | … | "RQ18", "span": "", "section": "" },
      "rq_contribution_type":           { "value": "Primary-evidence" | "Supporting-evidence" | "Contextual-evidence" | "Contradictory-evidence" | "Descriptive-only" | "Not-applicable", "span": "", "section": "" },
      "rq_contribution_summary":        { "value": "<≤ 30 words>", "span": "", "section": "" },
      "rq_contribution_strength":       { "value": "High" | "Moderate" | "Low" | "Insufficient", "span": "", "section": "" },
      "rq_contribution_direction":      { "value": "Confirms" | "Refutes" | "Qualifies" | "Neutral" | "Not-applicable", "span": "", "section": "" },
      "rq_contribution_data_fields":    { "value": ["<field_id>", "..."], "span": "", "section": "" },
      "rq_contribution_template_section": { "value": ["<see Section E>"], "span": "", "section": "" },
      "rq_contribution_quote":          { "value": "<verbatim ≤ 40 words>", "span": "", "section": "" }
    }
  ],

  // Eligibility re-confirmation (flag-only — does NOT re-decide inclusion)  [v1.7]
  // Inclusion was decided at full-text screening; do NOT withhold extraction here.
  // ROUTE-CONDITIONAL — apply the criteria for THIS record's RQ route(s):
  //   * Determinants (RQ1) and measurement (RQ18) routes do NOT require an intervention.
  //     Absence of an intervention is NOT a concern for them; a prevalence / risk-factor /
  //     epidemiology / instrument-validation study is IN SCOPE for these routes.
  //   * Only for intervention routes may an out-of-scope intervention be a concern.
  // Set eligibility_flag = "Possibly-ineligible" ONLY for a genuine scope failure given the
  // route — e.g. population not adult depression / CMD; outcome not depression-relevant;
  // design ineligible for the route — and give a ≤ 25-word quoted concern naming the
  // specific criterion. Default to "Eligible" whenever unsure (do NOT re-create an
  // intervention-centric exclusion). Flag-only: surfaces likely false-includes for HUMAN
  // review; it never excludes.
  "eligibility_flag":    { "value": "Eligible" | "Possibly-ineligible", "span": "", "section": "" },
  "eligibility_concern": { "value": "<≤ 25 words: criterion + why, or empty if Eligible>", "span": "", "section": "" },

  // Extractor bookkeeping
  "extractor_confidence": <float 0.0–1.0>,
  "fields_flagged_for_human": ["<field_id>", …]
}

────────────────────────────────────────────────────────────────
D. Controlled vocabularies (closed lists; do not invent values)
────────────────────────────────────────────────────────────────

design (review):
  Systematic-review | Meta-analysis | Meta-regression | IPD-meta-analysis
  | Network-meta-analysis | Umbrella-review | Dismantling-component-meta-analysis
  | Systematic-review-of-economic-evaluations | Scoping-review | Other-review

design (primary study):
  RCT-individual | RCT-cluster | Quasi-experimental-controlled

sev_strat:
  Minimal | Mild | Moderate | Moderately-severe | Severe
  | MDD-diagnosed-unspecified | Mixed-unstratified
  | Perinatal-elevated-symptoms | Not-reported

sev_scale:
  PHQ-9 | PHQ-2 | CES-D | CIDI | SCID | MINI | HAMD | BDI | EPDS
  | SRQ-20 | K10 | Other

int_techniques (multi-select ≥ 1):
  Psychoeducation | Behavioural-activation | Problem-solving
  | Cognitive-restructuring | Interpersonal-techniques
  | Mindfulness-acceptance | Relaxation-stress-management
  | Behavioural-skills-training | Motivational-interviewing
  | Supportive-counselling | Narrative-expressive | Peer-support-mutual-aid
  | Single-session-protocolised | Self-help-bibliotherapy | Other-technique

int_adaptations (multi-select, optional):
  Religious-spiritual-content | Cultural-adaptation-named
  | Livelihoods-economic-component | Family-or-spouse-engagement-component
  | Trauma-or-violence-specific-content | HIV-specific-content
  | Other-adaptation

dose_band:
  Ultra-brief | Brief | Standard-short | Standard | Extended | Long
  | Stepped-variable | Not-reported

fac_type:
  Lay-paid | Lay-volunteer | Peer | Teacher-educator | Religious-faith-leader
  | Mixed-team | Specialist | Not-reported

del_format:
  Group-face-to-face | Family-couple | Group-remote-phone-or-video
  | Hybrid | Self-help-print-guided

setting:
  Primary-care-clinic | Community | School | Workplace
  | Antenatal-postnatal-service | NGO-program | Refugee-humanitarian
  | Digital-only | Home-based

amstar2_band:
  High | Moderate | Low | Critically-low

────────────────────────────────────────────────────────────────
E. RQ → template_section mapping (populate template_section from this table)
────────────────────────────────────────────────────────────────

For every RQ this record contributes to (rq_tags), add every listed
Decision Brief section to template_section (multi-select). Sections use
the notation "§X.Y" from the Strongminds template.

RQ1  → §3.1; §6-A-RQ1; §2-dashboard; §7-theme5
RQ2  → §3.4; §6-A-RQ2; §2-dashboard; §8.4
RQ3  → §6-C-RQ3; §8.1; §2-dashboard; §7-theme1
RQ4  → §6-C-RQ4; §8.1; §3.2; §2-dashboard; §7-theme1
RQ5  → §6-B-RQ5; §8.4; §8.5; §8.6; §9a-Action1; §2-dashboard
RQ6  → §6-B-RQ6; §8.4; §8.5; §9a-Action1; §2-dashboard
RQ7  → §6-C-RQ7; §8.2; §8.1; §8.5; §8.6; §9a-Action1; §2-dashboard
RQ8  → §6-B-RQ8; §8.5; §7-theme5; §9b-Priority1; §10-Gap1; §2-dashboard
RQ9  → §6-C-RQ9; §8.2; §8.3; §9a-Action1; §9a-Action3; §2-dashboard
RQ10 → §6-C-RQ10; §8.1; §7-theme3; §9b-Priority4; §2-dashboard
RQ11 → §6-B-RQ11; §10-Gap4; §2-dashboard
RQ12 → §6-C-RQ12; §8.3; §8.5; §8.6; §9a-Action3; §2-dashboard
RQ13 → §6-C-RQ13; §8.1; §8.5; §8.6; §9a-Action2; §2-dashboard
RQ14 → §6-B-RQ14; §8.4; §8.5; §7-theme5; §9b-Priority1; §2-dashboard
RQ15 → §6-C-RQ15; §7-theme3; §9b-Priority5; §2-dashboard
RQ16 → §6-D-RQ16; §8.1; §8.4; §8.5; §8.6; §9a-Action5; §9b-Priority2; §10-Gap2; §2-dashboard
RQ17 → §6-C-RQ17; §8.3; §7-theme4; §9a-Action4; §9b-Priority3; §10-Gap3; §2-dashboard
RQ18 → §6-C-RQ18; §8.3; §9a-Action3; §2-dashboard

────────────────────────────────────────────────────────────────
F. Document to extract
────────────────────────────────────────────────────────────────

{{segmented_full_text}}

────────────────────────────────────────────────────────────────
Return the single JSON object described in Section C. Return JSON only.
No prose, no markdown, no code fences.
```

---

## Paraphrase variants (k = 3)

Run the record through all three variants. Take a per-field majority vote. Any per-field disagreement → add the field to `fields_flagged_for_human` in the merged output.

### Variant EX-1 (base)
The USER block above, unchanged.

### Variant EX-2 (anti-anchoring; randomised field order)
Reorder the schema in Section C so the fields appear in a shuffled sequence (fix the shuffle per run for reproducibility; store the permutation in `/llm_logs/`). System block and vocabularies unchanged. This tests whether the extractor is anchoring on field order.

### Variant EX-3 (extract-or-justify)
Add the following sentence at the top of the USER block:

> For every field in the schema, either populate it with an extracted value (and its verbatim span) OR set value = null and, in a separate `absence_notes` map keyed by field_id, record in ≤ 15 words the reason the field is absent from this document (e.g., "review reports pooled effect only, no per-severity subgroup"). This is a per-field justify-your-absence framing.

Add to the schema:

```
  "absence_notes": { "<field_id>": "<≤ 15 words>" }
```

Variant EX-3's `absence_notes` are used only to enrich `fields_flagged_for_human` at the merge step; they are not carried into the final extraction record.

---

## Merge logic (across the k = 3 runs)

For each field:
1. If all three values agree → take the value; span = span from EX-1.
2. If two of three agree → take the majority value; span from a run in the majority; add the field ID to `fields_flagged_for_human`.
3. If all three disagree → set value = null; add field ID to `fields_flagged_for_human`; store all three candidates in `/llm_logs/`.
4. `extractor_confidence` in the merged record = mean of the three per-run confidences.

---

## Span-validation call (post-extraction, per protocol Appendix F.5)

For every non-null field in the merged record, call the span-validator prompt with `{field_id, value, span, section}` plus the segmented document. Any `verdict: "fail"` → human re-extraction of that field.

The span-validator runs over 100% of quantitative fields and a 20% random sample of qualitative fields.

Span-validator prompt (unchanged from protocol Appendix F.5):

```
SYSTEM
You are a verification function. You receive (a) a claimed value, (b) a
verbatim span, (c) the section it was drawn from, and (d) the full
segmented document. You verify that the span appears in the document
EXACTLY as quoted (character-for-character, including punctuation and
digits) AND that the section reference is accurate AND that the claimed
value is consistent with what the span says. You return JSON only.

USER
Verify the following claim.

claim:
  field: "{{field_id}}"
  value: {{value}}
  span: "{{span}}"
  section: "{{section}}"

document:
{{segmented_full_text}}

Return:
{
  "span_present_verbatim": true | false,
  "section_correct": true | false,
  "value_consistent_with_span": true | false,
  "verdict": "pass" | "fail",
  "failure_reason": "<≤25 words; empty if pass>"
}

verdict = "pass" iff all three booleans are true.
```

---

## Calibration before live use

Before running this prompt on the full corpus, calibrate against the human gold standard (5 pilot studies per protocol §4.4). Iterate the prompt (log every change in the deviation register) until per-field agreement with the human extraction reaches an acceptable level. Only then deploy.

Every prompt version, model API string, seed, and full response is stored as JSONL under `/llm_logs/`.

---

## Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-07 | Initial prompt derived from Protocol Appendix F.4, expanded with RQ-specific fields per `ULCM_M1_Extraction_Fields_by_RQ.docx`. |
| 1.1 | 2026-07 | Added `eff_se`, arm-level Ns (`eff_n_int`, `eff_n_ctrl`), and per-time-point SE / CI / arm-Ns to the durability block, per synthesis requirement for weighted effect-size tabulation. |
| 1.2 | 2026-07 | **[REVISION — pending user review]** Added primary-study extraction workflow (§ new rules 11–16), record-tier flag (`full` vs `reduced`), sourcing-review link (`source_review_ids`), RCT quality items (§3A.4.2), attrition (§3A.4.3), trial provenance (§3A.4.4), and arm-level enrolled/randomised Ns (§3A.4.5). |
| 1.3 | 2026-07 | **[REVISION — pending user review]** Added Fatal-Flaw ROB checklist (§3A.4.6): 5 criteria × 9 sub-items × Yes/No verdict + per-criterion notes + verbatim spans. Propagates to `rob_fatal_flaw`. Rules 17–19. Sourced from FF-ROB checklist template shared by the review team. |
| 1.4 | 2026-07 | **[REVISION — pending user review]** Added AMSTAR-2 itemised checklist (§3.11A): 16 items × verdict + span + notes, seven critical (I2, I4, I7, I9, I11, I13, I15) + nine non-critical, with rapid critical-domains mode, confidence-band derivation from Shea 2017 rules, and explicit tie-in to the review-only RQs (RQ1–4, 11, 15, 17, 18) where AMSTAR-2 is the sole quality signal. Rules 20–26. |
| 1.5 | 2026-07 | **[REVISION — pending user review]** Added: (a) §3.4A sub-module decomposition — 11 named programmes with sub-module vocabularies, session-share %, contribution, and evidence type (rule 27); (b) §3.8A repeating additional_outcomes array — every {construct × instrument × timepoint × subgroup} with full effect / arm-mean / SD / N / adjustment fields (rule 28); (c) §3.12A rq_contributions array — one entry per {study × RQ} with contribution type, strength, direction, backing fields, and Decision Brief mapping (rule 29). Exported extraction sheet materialises rq_contributions as long-format one-row-per-{sid × rq_id}. |
| 1.6 | 2026-07 | Constrained §3.8A to DEPRESSION OUTCOMES ONLY per review-team decision. `outcome_construct` restricted to {Depression-symptoms, Depression-remission, Depression-response, Depression-recovery, Depression-relapse}. `outcome_scale` restricted to validated depression instruments (PHQ-9, PHQ-2, CES-D, HAMD/HDRS, MADRS, BDI/BDI-II, EPDS, QIDS, Zung-SDS, GDS, DASS-21-dep, SCL-90-dep, SRQ-20-dep, K10-dep-cutoff, MINI-dep, CIDI-dep, SCID-dep, Other-depression-instrument). Non-depression outcomes stay in existing dedicated blocks (§3.7 cost, §3.9 engage/safety, §3.10 psychometrics, §3A.4.3 attrition). |
| 1.7 | 2026-07 | **[DEVIATION — logged]** Working copy derived from `DEX/ULCM_M_1.MD` (pristine protocol artifact kept unchanged). Added `eligibility_flag` + `eligibility_concern` to Section C: a **flag-only** eligibility re-confirmation that surfaces likely false-includes for human review. It does NOT re-decide inclusion (which remains a full-text-screening decision) and never withholds extraction — it addresses the FTS over-inclusion (specificity ~0 on the GT check) by making suspect records visible rather than silently present. |
