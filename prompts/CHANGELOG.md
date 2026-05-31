# Prompt Changelog

All changes to the universal trading prompt are logged here. Each version is saved as `vN.txt`.

## v2 — 2026-06-01
**Author:** Aadi
**Status:** Active (effective 2026-06-01, Phase A)
**Supersedes:** v1 (in effect 2026-04-09 – 2026-05-31)

Prompt text in `prompts/v2.txt` is the locked source verbatim (the marked section of
`LLM-Trading-Lab-Decision-Prompt-v2.0.md`). v2 adds context and calibration only — it
does not prescribe strategy, sizing, or holding period (required to protect RQ1
cross-model herding). Six changes from v1:

1. **Confidence scale rebuilt** — every 1–10 band gets behavior-tied anchors, plus two
   hard gates: confidence ≥ 8 must name a specific driver; speculative/hedge/"worth a
   try" trades cap at ≤ 4. Anti-clustering self-check added.
2. **Anti-reversal / churn friction** — the model's own most-recent action per ticker
   is surfaced inline (`YOUR_LAST_ACTION`); reversing it requires a concrete nameable
   change; re-entry of a recently exited name counts as a reversal; per-trade "why now".
3. **Cash as two-sided opportunity cost** — acknowledgment required at near-zero or
   unusually high cash. No prescribed floor.
4. **Loss review** — open positions down ≥ 5% require an explicit thesis review
   (intact / weakened / invalidated). All outcomes legitimate.
5. **Neutral objective** — maximize risk-adjusted return over the 18-month horizon; no
   single-period evaluation.
6. **Pre-market briefing** integrated as carried-forward daily context.

Permanent exclusion: no contrarian / differentiation / consensus-orientation framing
(would bias RQ1). Deferred: shorting (Jul 2026), confidence self-calibration feedback
(Aug 2026).

Schema reconciliation (additive — no v1 field renamed): the parser, `DecisionResult`,
and decision logs gain period-level `cash_rationale` (nullable), `position_reviews`
(`{ticker, thesis_status, implication}`), `no_trade_reason` (nullable), and per-trade
`confidence_justification`, `why_now`, `reversal_justification` (nullable). Trade size
stays `target_weight` (0.0–0.20). Period reasoning reuses the existing
`overall_reasoning`. Because v2.txt states the output format in prose, the pipeline
appends the authoritative JSON field-name contract to the system prompt for v2+
(`prompt_builder.V2_OUTPUT_SCHEMA`), mirroring the existing v2 last-action append.

## v1 — 2026-04-08
**Author:** Aadi
**Status:** Superseded by v2 on 2026-06-01

Initial universal trading prompt for Phase 1 build.

- JSON-only output enforced via schema in prompt + provider-side `response_format` where supported.
- Neutral framing — no bullish/bearish language, no leading data presentation.
- Hard rules listed explicitly so models filter their own outputs against constraints.
- Required fields: action, ticker, target_weight, confidence, reasoning.
- Confidence is a 1-10 integer to allow calibration analysis later.
- HOLD is allowed and not penalized.
- Cash is explicitly stated as a valid choice to avoid forcing trades.
