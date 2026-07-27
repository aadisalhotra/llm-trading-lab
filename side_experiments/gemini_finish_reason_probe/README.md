# Gemini finish_reason probe — 2026-07-27

Operations diagnostic for the Gemini completeness failure (May 0.57 / June 0.79 /
July 0.51). Script: `scripts/gemini_finish_reason_probe.py`. One real production
decision prompt (assembled through the live pipeline's own code path, chart image
included) replayed against the API. ~$3.1 total spend on the lab Google key.

## Verdict: generation-budget exhaustion, confirmed by conversion

The production adapter sends `max_output_tokens: 4096` (JSON mode, no schema).
gemini-3.1-pro-preview spends thinking tokens against that same budget
invisibly (the deprecated `google.generativeai` SDK reports
`thoughts_token_count` as 0/absent), so the visible JSON answer gets cut
mid-string at ~150-200 tokens and `finish_reason` says `MAX_TOKENS`.

| Arm | Config | Calls | Parse fails | finish_reason on failure |
|-----|--------|-------|-------------|--------------------------|
| A | production replica (cap 4096) | 31 | 23 (0.74) | **22/23 MAX_TOKENS**, 1 STOP |
| B | + response_schema | 15 | 6 (0.40) | 6/6 MAX_TOKENS |
| C | gemini-3-pro-image (non-preview) | 15 | 0 | — (15/15 STOP) |
| D | **same arm-A prompts verbatim, cap 16384** | 15 | 1 (0.07) | **0 MAX_TOKENS — 15/15 STOP** |

- Arm D's one failure was `Extra data` after complete JSON at STOP — a
  cosmetic parse issue, not the truncation mode.
- Mean successful answer at the raised cap: 660 tokens — the visible answer
  never needed 4096; the budget was eaten upstream by thinking.
- Schema (arm B) reduces frequency but does not fix the mechanism; decision
  content is invariant between A and B (same top actions: BUY:GOOGL, SHORT:NVDA).
- No non-preview pro-tier text model exists at gemini-3.x
  (`gemini-3.1-pro` is preview-only; GA options are flash-tier or
  `gemini-3-pro-image`).
- Cohort cap asymmetry: DeepSeek runs `max_tokens: 16384` (raised 2026-05-21,
  explicitly because "a long high-effort trace can't truncate the decision");
  Gemini/Anthropic/OpenAI/xAI all run 4096.

June's "not token-cap truncation (failing outputs are short)" inference was
wrong about mechanism: outputs are short *because* the cap is exhausted by
hidden thinking, not because the model stops early of its own accord.

## Files
- `context.json` — the exact prompts replayed (arm D reused these verbatim)
- `results.jsonl` — one record per call, full raw response text included
- `summary.json` — per-arm aggregates (finish_reason × parse outcome)
