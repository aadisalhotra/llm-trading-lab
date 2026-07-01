# Prompt Changelog

All changes to the universal trading prompt are logged here. Each version is saved as `vN.txt`.

## v3 — effective 2026-07-01
**Author:** Aadi
**Status:** Active (effective 2026-07-01, Phase A; built + tested 2026-06-20)
**Supersedes:** v2 (active 2026-06-01 – 2026-06-30)

Prompt text in `prompts/v3.txt` is v2 verbatim plus **shorting-enabling content only** —
no other edits, so the v2→v3 contrast is unconfounded (anything non-shorting would
muddy the before/after read on RQ1). Shorting design ratified by Research + PI; not
re-litigated here. The shorting-only delta:

1. **Operating constraints** — "Long positions only. No short selling…" becomes "Long
   and short positions are both permitted." Adds the exposure rule book: gross short
   ≤ 20% of equity, long ≤ 100%, total gross ≤ 120%, net ∈ [−20%, +100%]. Each existing
   limit (50 positions, 20% per-name, 50 trades/day, drawdown halt) gains a parenthetical
   that shorts count toward it; the limit *values* are unchanged. The per-position
   stop line adds the 10% short stop alongside the existing 15% long stop.
2. **New "Short selling" subsection** — neutral mechanics only (a short profits when
   price falls; proceeds credit cash; negative quantity; cover to close; 20% gross cap;
   10% stop; no borrow cost in this phase). Explicitly states there is no expectation
   to short — a no-short book is valid. No contrarian/differentiation framing (would
   bias RQ1).
3. **Output format** — the action enumeration "buy or sell" becomes "buy, sell, short,
   or cover". The JSON appendix (`prompt_builder.V3_OUTPUT_SCHEMA`) adds SHORT/COVER to
   the `action` enum and clarifies that `target_weight` is always a positive magnitude
   (the desired gross weight; for SHORT/COVER it is the gross short weight).

Communicated identically to all six models; API formatting is the only per-model
difference. Risk engine gates execution behind `portfolio_rules.shorting_enabled`
(FALSE in production until activation). Confidence self-calibration feedback remains
deferred (Aug 2026).

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
