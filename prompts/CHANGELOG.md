# Prompt Changelog

All changes to the universal trading prompt are logged here. Each version is saved as `vN.txt`.

## v1 — 2026-04-08
**Author:** Aadi
**Status:** Active

Initial universal trading prompt for Phase 1 build.

- JSON-only output enforced via schema in prompt + provider-side `response_format` where supported.
- Neutral framing — no bullish/bearish language, no leading data presentation.
- Hard rules listed explicitly so models filter their own outputs against constraints.
- Required fields: action, ticker, target_weight, confidence, reasoning.
- Confidence is a 1-10 integer to allow calibration analysis later.
- HOLD is allowed and not penalized.
- Cash is explicitly stated as a valid choice to avoid forcing trades.
