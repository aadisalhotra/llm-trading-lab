# Prompt Changelog

| Version | Date | Author | Change |
|---|---|---|---|
| v1 | 2026-04-08 | Aadi | Initial universal trading prompt. JSON-only output, neutral framing, full portfolio context, hard constraints listed. |
| v2 | 2026-06-01 | Aadi | Context + calibration only (no strategy prescription). Six changes from v1: behavior-anchored confidence scale with ≥8-driver and ≤4-speculative gates; per-ticker anti-reversal/churn friction + why-now; cash as two-sided opportunity cost; ≥5% loss thesis review; neutral 18-month risk-adjusted objective; pre-market briefing as carried context. Additive schema: cash_rationale, position_reviews, no_trade_reason, per-trade confidence_justification/why_now/reversal_justification (target_weight size unchanged). Permanent exclusion of contrarian/differentiation framing. |
