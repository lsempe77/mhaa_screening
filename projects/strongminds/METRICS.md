# StrongMinds ULCM — Screening Metrics

**Project:** Ultra-Low-Cost Model (ULCM) for Adult Depression — Title & Abstract Screening
**Date:** 2026-07-22
**Prompt version:** v1.9 (orchestrator: router → route-specific screener → critic)
**Seed:** 510-record EPPI seed (59 INCLUDE / 451 EXCLUDE after 23 GT corrections)
**Models:** Claude Sonnet 4 + GLM-5.2 (k=5, temperature 0.3), critic = Mistral Large
**Protocol thresholds:** Sensitivity ≥ 0.95, Cohen's κ ≥ 0.70, ECE ≤ 0.10

## Final metrics (v1.9, all thresholds PASSED)

| Metric | Value | Threshold | Status |
|---|---|---|---|
| N | 509 | — | — |
| Sensitivity | 0.966 | ≥ 0.95 | ✅ PASS |
| Specificity | 0.949 | — | — |
| Precision | 0.709 | — | — |
| F1 | 0.818 | — | — |
| Cohen's κ | 0.790 | ≥ 0.70 | ✅ PASS |
| ECE | 0.042 | ≤ 0.10 | ✅ PASS |
| Brier | 0.038 | — | — |
| FN / FP | 2 / 23 | — | — |

## Confusion matrix

|  | Pred INCLUDE | Pred EXCLUDE |
|---|---|---|
| True INCLUDE | 56 | 2 |
| True EXCLUDE | 23 | 428 |

## Per-model breakdown

| Model | Sensitivity | Specificity | κ | ECE |
|---|---|---|---|---|
| Claude Sonnet 4 | 0.879 | 0.959 | 0.775 | 0.042 |
| GLM-5.2 | 0.879 | 0.944 | 0.725 | 0.030 |

## Inter-model agreement

| Metric | Value |
|---|---|
| Agreement | 89.8% |
| Inter-model κ | 0.589 |

## Critic adjudication

| Metric | Value |
|---|---|
| Applied to | 278 records |
| Overrides | 206 |

## Full iteration history

See `projects/strongminds/docs/ITERATION_LOG.md` (Parts I–VI, §1–§41).

## Raw metrics JSON

See `reports/metrics.json` (overwritten by each calibration run).