# Girl Effect MHAA — Screening Metrics

**Project:** Mental Health Anywhere Anytime (MHAA) — Digital & AI-Enabled Mental Health Interventions for Young People
**Date:** 2026-07-22
**Prompt version:** v1.4.3 (monolithic hierarchical screener + critic)
**Seed:** 462-record EPPI seed (87 INCLUDE / 375 EXCLUDE)
**Models:** Claude Sonnet 4 + GLM-5.2 (k=5, temperature 0.5), critic = Mistral Large
**Protocol thresholds:** Sensitivity ≥ 0.95, Cohen's κ ≥ 0.70, ECE ≤ 0.10

## Final metrics (v1.4.3)

| Metric | Value | Threshold | Status |
|---|---|---|---|
| N | 462 | — | — |
| Sensitivity | 0.943 | ≥ 0.95 | ❌ FAIL (0.007 short) |
| Specificity | 0.891 | — | — |
| Precision | 0.667 | — | — |
| F1 | 0.781 | — | — |
| Cohen's κ | 0.719 | ≥ 0.70 | ✅ PASS |
| ECE | 0.081 | ≤ 0.10 | ✅ PASS |
| Brier | 0.082 | — | — |
| FN / FP | 5 / 41 | — | — |

## Confusion matrix

|  | Pred INCLUDE | Pred EXCLUDE |
|---|---|---|
| True INCLUDE | 82 | 5 |
| True EXCLUDE | 41 | 334 |

## Per-model breakdown

| Model | Sensitivity | Specificity | κ | ECE |
|---|---|---|---|---|
| Claude Sonnet 4 | 0.908 | 0.899 | 0.712 | 0.080 |
| GLM-5.2 | 0.977 | 0.883 | 0.725 | 0.099 |

## Inter-model agreement

| Metric | Value |
|---|---|
| Agreement | 95.7% |
| Inter-model κ | 0.889 |

## Critic adjudication

| Metric | Value |
|---|---|
| Applied to | 336 records |
| Overrides | 22 |

## Notes

- Sensitivity is 0.007 below the 0.95 threshold (5 FNs out of 87 true includes).
- Full-text screening (388 PDFs) completed; awaiting human review for calibration.

## Raw metrics JSON

See `reports/metrics.json` (overwritten by each calibration run — last run was MHAA).