# ULCM rapid-review evidence-synthesis book

A Quarto **book** that presents the StrongMinds ULCM rapid review one research question
at a time (RQ1–RQ18), for the design of the Ultra-Low-Cost Model. It renders to **HTML,
PDF and Word** and is a *descriptive* synthesis of **machine-extracted, not-yet-human-verified**
evidence — confidence is capped at Low, effects are not pooled, and the vote-count /
publication-bias caveats are stated in the Methods chapter.

## How it is built

| File | Role |
|---|---|
| `_quarto.yml` | Book config: chapter order (front matter → four workstream Parts → back matter), the three output formats, `execute-dir: project`. |
| `chapter_helpers.py` | Imported as `H` in every chapter. Loads the extraction CSVs **once** and exposes all data/prose builders (see below). |
| `figures.py` | Imported as `F`. The matplotlib figure engine (CVD-safe palette), one function per chart. |
| `index.qmd` | Executive summary + candidate-design-space recommendation box. |
| `background.qmd`, `methods.qmd` | Scope; and the methods/caveats (AMSTAR-2, vote-count rule, GRADE-lite, CCA, publication bias, population heterogeneity). |
| `rq01.qmd … rq18.qmd` | One chapter per question. Each is thin: it sets `N = <rq>` and calls `H.*` / `F.*`. |
| `discussion.qmd`, `limitations.qmd` | Cross-cutting synthesis and limitations. |

Each chapter runs with the book folder as its working directory (`execute-dir: project`),
so `import chapter_helpers as H` / `import figures as F` resolve locally; `chapter_helpers`
anchors the data path to its own location, so `quarto render` works argument-free.

## Data inputs

The book reads **only** these files, produced by the extraction-report scripts
(`../../data/extraction/reports/`, gitignored — regenerate with the steps in the repo-root
README):

- `dex_summary.csv` — one row per included record (`summ`)
- `dex_long.csv` — one row per record × field (`long`; only `record_id, field, value` are loaded)
- `dex_wide.csv` — record × field pivoted wide (`wide`)
- `dex_rq_contributions_long.csv` — one row per study×RQ contribution (`contrib`)
- `dex_overlap_by_rq.csv` — per-RQ CCA / discordance (`ov_by_rq`)

### Columns consumed, and the vocabularies the code assumes

The code hard-codes category strings; these were **audited against the data** and match:

| Column (source) | Values the code uses | Notes |
|---|---|---|
| `geo_focus` (summ, wide) | `SSA`, `other-LMIC`, `HIC-UMIC`, `mixed` | exact match |
| `unit` | `review`, `primary_study` | exact match |
| `amstar2_band` | `High`, `Moderate` (= hq) / `Low`, `Critically-low` (= lowq) | `Not-applicable` correctly excluded from "rated reviews" |
| `eff_direction` | `Favours-intervention`, `Null`, `Favours-control`, `Unclear` | `Not-applicable` / `Not-reported` correctly excluded — the vote-count denominator is "records with a **codable** direction" |
| `specialist_delivered_flag` | `"False"` = non-specialist, `"True"` = specialist | string values |
| `eff_metric` | `"SMD"` (only, in the now-unused `smd_magnitude`) | |
| `rq_contribution_direction` | `Confirms`, `Qualifies`, `Neutral`, `Refutes` | `Not-applicable` excluded |
| `rq_contribution_strength` | `High`…`Critically-low` | |
| `rq_contribution_data_fields` | stringified list → per-RQ field set (`FIELDSETS`) | drives the parameter tables/prose |
| RQ-specific fields | `dose_*`, `driver_*`, `psychom_*`, `cost_*`, `engage_*`, `int_techniques`, `trajectory_shape`, … | surfaced via `field_report` / `numeric_profile` / `category_bar` |
| `ov_by_rq` | `n_reviews_with_studies`, `cca_pct`, `overlap_rating`, `n_unique_studies`, `shared_pairs`, `discordance_pct` | |

**Data-fidelity audit result:** every referenced column exists, every hard-coded vocabulary
matches the data, and spot-checked prose claims match the numbers (dose ≈ weekly/brief/linear;
durability sustained > decaying; ~¾ non-specialist group delivery; stigma the top engagement
barrier; PHQ-9 the leading named instrument, cut-off ≈ 10; mixed cost currencies).

### Known, intentional data notes

- **"Records" ≠ "directional records".** A chapter's record count (`rqstats`, from `summ`,
  2,670 includes) is deliberately larger than its harvest denominator (`wide`, records with a
  codable `eff_direction`). These are different quantities; the prose says "of the records
  reporting a direction" precisely for this reason. `summ` (2,670) also carries 2 extraction-error
  rows absent from `wide` (2,668) — a ≤2-record, negligible corpus-wide difference.
- **`stream` is unreliable** (≈94% `Stream2-seed`, only 44 `Stream1`), so the protocol's
  stream-stratified harvest (§4.5.1) is **not** reproduced; geography stratification is clean.
- **RQ18** raw `psychom_instrument` top value is the `Other` catch-all; it is filtered from the
  figure and the prose refers to PHQ-9 as the leading *named* instrument.
- **`smd_magnitude()` is retained but unused** — the |SMD| median was removed from the prose
  (it averaged mixed-sign, mixed-instrument values); the function is kept as a utility only.

## How the report was written

The document was produced **iteratively and data-first**, not written up front and back-filled:

1. **Foundations.** Built the shared engine (`chapter_helpers.py`, `figures.py`) reading the
   extraction reports, so all computation is centralised and every figure in the report traces
   to code rather than hand-transcription.
2. **High-level summary first.** Produced a cross-RQ overview (evidence volume, quality,
   geography, per-RQ status) and an initial per-RQ profiles pass, checking counts against the
   data at each step.
3. **Data check, per question.** For each RQ, queried its populated fields directly — which
   fields are rich, their value distributions, medians, subset shares — to establish what the
   data actually supported *before* writing prose.
4. **Expand to chapters.** Wrote each chapter around those queries: evidence base, direction
   (harvest + vote-count), robustness across subsets, question-specific detail, overlap,
   contributing records, representative quotes — choosing RQ-appropriate figures and fields.
5. **Data check again.** Re-queried and spot-checked the rendered output; corrected figures and
   prose wherever the data said otherwise.
6. **Key messages & RQ answers — a second/third pass.** After the descriptive body was solid,
   developed each chapter's **key-message box** (3–4 headline takeaways) and its ~150-word
   **answer box** (bottom line + confidence + implication for the ULCM), cross-referencing
   related questions.
7. **Protocol alignment.** Read the rapid-review protocol and adopted its framing: the four
   workstreams (A–D), harvest plots (Ogilvie 2008), the pre-specified vote-count rule
   (≥75% = consistent / 50–74% = mixed / <50% = inconsistent), GRADE-lite, the answer boxes,
   and the §5.2 report structure.
8. **Critical-review pass + corrections.** Audited the draft as an evidence-synthesis reviewer,
   then corrected the substantive weaknesses and did a prose pass (see below).
9. **Column-content audit.** Verified the code's hard-coded vocabularies and the prose claims
   against the data (the audit summarised above).

## Critical-review corrections (what changed and why)

The first draft over-sold weak evidence; the following were corrected so the report is faithful:

- **Counting / independence.** The harvest counts *reviews*, which re-analyse overlapping trials,
  so it is not independent evidence. The false "consistency is not an artefact of the same trials
  counted repeatedly" was reversed in every chapter; the Methods now explain non-independence and
  that a near-zero CCA is a scale artefact, not independence.
- **GRADE-lite capped at Low.** Was mostly "Moderate", which contradicted the ~84% low-quality
  review base; now Low throughout (Very low where direction is mixed).
- **Publication bias.** Added — near-universal positivity is treated as a warning (Methods
  section + a "Why is almost everything positive?" Discussion analysis), not a green light.
- **Meaningless numbers removed.** The |SMD| median (mixed sign/instrument) and the mixed-currency
  cost median were dropped; magnitude is described as unrecoverable, cost as a range far above USD 1.
- **RQ1 rebuilt** as a full determinants chapter (named-determinant figure, four themes, the
  perinatal/pandemic population skew, mechanism, modifiable-vs-fixed cut); **RQ3** given a real
  moderator/effect-modification section; a **population/severity-heterogeneity** caveat added to
  Methods.
- **Prose pass.** Softened over-statements, cut LLM crutch phrases, and roughly halved em-dashes.

## Reproduce

Prerequisites: `pandas`, `matplotlib`, `openpyxl`, `tabulate`; [Quarto](https://quarto.org);
TinyTeX for PDF (`quarto install tinytex`). Then, once the `reports/` CSVs exist (see the
repo-root `README.md` for the extraction → reports steps):

```bash
quarto render        # -> _book/index.html (+ chapters), _book/*.pdf, _book/*.docx
```

The rendered `_book/` is gitignored and fully regenerated by that command.
