# Data-extraction pipeline (`pipeline/extraction/`)

The stage *after* full-text screening. It takes the **included** studies (full
PDF text) and pulls the structured fields defined by a **declarative framework**,
using a **double-extraction + reconciliation** workflow — the evidence-synthesis
gold standard (two independent reviewers, a third resolves disagreements),
automated with LLMs and grounded in verbatim quotes.

```
included study (full text)
        │
        ├── extractor A  (Claude)  ─┐
        ├── extractor B  (GLM-5.2) ─┤─►  quote-validate ─►  reconcile  ─►  consensus
        │                           ─┘   (verbatim,               (agree / minor /
        │                                 per field)               conflict → human)
        ▼
   baseline group first → its L&MIC/SSA consensus flags gate Stream 1c
```

Same engine, any project: only the framework YAML changes (MHAA today,
StrongMinds later).

## Why this design (evidence-synthesis rationale)

| Principle | How it's implemented |
|---|---|
| **Double extraction** (Cochrane MECIR) | Two independent LLM extractors per field group; a reconciler produces consensus and flags genuine conflicts for a human. Mirrors the screening engine's dual-model + critic. |
| **Grounding / provenance** | Every `reported=true` field must carry ≥1 **verbatim quote** from the source, validated against the PDF text with the same fuzzy validator used in screening (`verify_quote`). Unverified quotes are flagged salmon in the workbook. |
| **"Not reported" is an answer** | `reported=false` is first-class and expected for most fields; the prompt forbids guessing or knowledge-transfer between studies. |
| **Conditional extraction** | Stream 1c (L&MIC/SSA implementation) is **gated** on the baseline L&MIC/SSA flags, exactly as the framework requires. |
| **Traceability to the pilot** | Each field is tagged with the evaluation-protocol section it feeds (framework Table 8); the workbook has an **Eval crosswalk** sheet. |
| **Human-in-the-loop** | The **Review queue** sheet is the reviewer's worklist: only the cells that are conflicts or have unverified quotes. |
| **Reproducible** | Framework is a versioned YAML (single source of truth); runs are resumable; the raw per-model extractions are kept alongside the consensus. |

## Files

| File | Purpose |
|---|---|
| `frameworks/mhaa_v1.yaml` | **The framework** — 88 fields across baseline + 7 streams, each with type, allowed values, extraction instruction, gate, and eval crosswalk. Edit this to change what's extracted. |
| `framework.py` | Loads + validates the YAML into typed objects; evaluates group gates. |
| `config.py` | Per-project dirs, framework registry, model + token defaults, `.env` loader. |
| `prompts.py` | Builds the grounded extraction + reconciliation prompts from the framework (no hand-maintained prompt text). |
| `extract.py` | **Main runner.** Dual-model extraction per (study × group), quote validation, gating, reconciliation, resumable JSONL. |
| `reconcile.py` | Reconciler-model call over the two extractions → consensus + agreement + `needs_human`; deterministic fallback if the call fails. |
| `export.py` | Reviewer **Excel workbook** (Index · per-stream sheets · Review queue · Eval crosswalk · Codebook) + machine-readable **long JSONL**. |

## Run it

```powershell
# 1. Extract (dual-model + reconcile) the full-text INCLUDEs.
#    Start small with --limit; drop it for the full set.
python pipeline/extraction/extract.py `
    --project girl_effect `
    --records projects/girl_effect/full_text/data/records_388.jsonl `
    --include-from-results projects/girl_effect/full_text/output/results_fts_glm_388.jsonl `
    --out projects/girl_effect/full_text/extraction/output/extraction_v1.jsonl `
    --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
    --reconciler-model anthropic/claude-sonnet-4 `
    --workers 6

# 2. Export to Excel + long JSONL for review.
python pipeline/extraction/export.py `
    --project girl_effect `
    --extraction projects/girl_effect/full_text/extraction/output/extraction_v1.jsonl `
    --records projects/girl_effect/full_text/data/records_388.jsonl
# → projects/girl_effect/full_text/extraction/reports/extraction.xlsx
#   projects/girl_effect/full_text/extraction/reports/extraction_long.jsonl
```

### Include set

Pick exactly one:
- `--include-from-results <screening.jsonl>` — extract records with
  `screening_decision == INCLUDE` (default choice until human review finalises
  the set). *Note: the GLM full-text run marked 362/388 INCLUDE and is not yet
  human-reviewed.*
- `--include-ids <file>` — a finalised list (JSON array, or one id per line).
  Use this once human review produces the final include set.
- `--all` — every record in `--records` (stress-testing).

### Useful flags

| Flag | Effect |
|---|---|
| `--groups baseline 1a 1c` | Extract only these groups (baseline auto-added when a gated group is requested). |
| `--limit N` | Cap studies (testing). |
| `--no-reconcile` | Skip the reconciler LLM; use the deterministic heuristic (agree on matches, flag the rest). Cheaper. |
| `--temperature` | Default 0 (extraction should be deterministic). |
| `--workers` | Parallel studies. |

## Output schema

**Per (study × group)** line in the extraction JSONL:

```jsonc
{
  "record_id": "KGX3897H",
  "group_id": "1a",
  "framework": "mhaa_extraction/v1",
  "gated_out": false,
  "extractors": {
    "anthropic/claude-sonnet-4": { "fields": { "1a.1": { "reported": true, "value": {...},
        "quotes": ["d = 0.42 (95% CI ..."], "location": "Results, p.7",
        "confidence": "high", "_quote_ok": true }, ... }, "_model": "...", "_ok": true },
    "z-ai/glm-5.2": { ... }
  },
  "reconciled": {                     // the consensus a human reviews
    "group_id": "1a",
    "fields": { "1a.1": { "reported": true, "value": {...}, "quotes": [...],
        "agreement": "agree", "needs_human": false, ... } },
    "_method": "llm"
  },
  "audit": { "n_reported": 5, "n_needs_human": 1, "recon_method": "llm", ... }
}
```

**Long JSONL** (`export.py`) flattens this to one row per **study × field** with
`value`, `quotes`, `location`, `agreement`, `needs_human`, `quote_ok`, and the
`eval_sections` it feeds — the analysis-ready master table.

## Extending to StrongMinds

1. Author `frameworks/ulcm_v1.yaml` (same schema: groups → fields with
   type/allowed/what/feeds, optional gates, `eval_crosswalk`).
2. Register it in `config.PROJECT_FRAMEWORKS["strongminds"]` and add the project
   dir in `config.PROJECT_DIRS` (already present).
3. Run `extract.py --project strongminds ...`. No engine code changes.
