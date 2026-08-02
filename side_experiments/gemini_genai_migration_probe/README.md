# Gemini SDK-migration probe — 2026-08-02

Pre-cutover validation for the `google-generativeai` 0.8.6 → `google-genai`
2.16.0 migration (Research integration-path rule §8). Scripts:
`scripts/gemini_genai_migration_probe.py` (new SDK, through the migrated
production adapter end-to-end) and `scripts/gemini_genai_legacy_control.py`
(same-day legacy-SDK control for attribution). Both replay the 2026-07-27
probe's saved production prompts verbatim — the same prompts arm D validated
at 16384 — with the chart image regenerated from current closed daily data.
~$0.55 total spend on the lab Google key (est., list rates; recorded cost
fields were computed under the rate table in force at run time and are
budget-guard estimates only).

## Verdict: budget semantics preserved; telemetry intact; residual mode SDK-independent

| Arm | SDK | Calls | finish_reason | Parse fails | Mean out-tok (ok) | thoughts_tokens |
|-----|-----|-------|---------------|-------------|-------------------|-----------------|
| E-genai (incl. smoke) | google-genai 2.16.0 | 16 | **16/16 STOP, 0 MAX_TOKENS** | 3 (all `extra_data`) | 735 | populated 16/16 (mean 4,501) |
| D-control (same day) | google-generativeai 0.8.6 | 10 | **10/10 STOP, 0 MAX_TOKENS** | 1 (`extra_data`) | 824 | 0/absent (legacy SDK artifact) |

Reference points: arm D 2026-07-27 (legacy, same prompts, 16384): 15/15 STOP,
1/15 `extra_data`, mean 660. Live baseline 2026-07-28..07-31 (legacy, 16384):
52/52 STOP, completeness 0.9231, mean 852.

- **Budget**: `GenerateContentConfig(max_output_tokens=16384)` serializes to
  the identical REST field (`generationConfig.maxOutputTokens`) on the same
  v1beta endpoint — verified at the wire level; zero MAX_TOKENS across all 26
  same-day calls on both SDKs. The 7/28 equalization survives the migration
  through the same parameter path, not a different one.
- **Nothing injected**: the request body carries ONLY `systemInstruction`,
  `responseMimeType`, `maxOutputTokens` — no sampling params, no
  `thinking_config`/`thinking_level` (provider-default reasoning preserved);
  no SDK-default HTTP retries (`stop_after_attempt(1), reraise`).
- **④b telemetry**: finish_reason / token counts / model_version echo all
  captured through the migrated adapter's own metadata path (this probe calls
  `_call_api` directly — nothing mocked). `modelVersion` is present on the
  raw google-genai response and echoes the alias verbatim, same as legacy.
  New: `thoughts_token_count` now populates (legacy reported 0/absent) —
  a telemetry improvement dated at the boundary, not a model change.
- **`extra_data` residual mode**: a stray trailing `}` after a complete
  decision object, byte-identical tails on the legacy failures (arm D 7/27,
  live 7/29 ×2, 7/31 ×2) and on both of today's arms. Present on BOTH SDKs
  same-day ⇒ SDK-independent, model-side, budget-independent (finish STOP).
  Not created and not fixed by this migration; the post-cutover continuity
  diagnostic watches its RATE (baseline ~8%, small-sample band up to ~19%).
- **Forced difference documented**: google-genai's `response.text` returns
  `None` (not an exception) on a no-usable-part response — an empty soft-stop
  now takes the parse-retry path with forensics attached instead of the
  legacy fail-fast API-error path. 0 such shapes observed anywhere to date.

## Files
- `results.jsonl` — new-SDK arm, one record per call, full raw text (first
  record is the single smoke call; `i` restarts at 1 for the full run)
- `results_legacy_control.jsonl` — same-day legacy control arm
- `summary.json` — aggregates + arm D baseline + in-situ baseline + the raw
  `modelVersion` field check

Ledger: `scripts/phase_a_integrity_ledger.json` →
`operational_events.2026-08[gemini_sdk_migration_2026_08]`.
