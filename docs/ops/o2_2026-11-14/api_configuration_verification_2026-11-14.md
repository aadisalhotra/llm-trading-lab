# Per-Model API-Configuration Verification (census O-2), 2026-11-14

- **Fills:** census `0a9f55a6` #1–8, 10–13, 15–18, 20–23, the `[VERIFY]` cells of `docs/PRE_REGISTRATION.md` :170–174 (RQ6 per-model API-configuration table). The DeepSeek row (:175) is re-confirmed, not changed.
- **Code basis:** origin `5b3be5e` (main). Production adapters (`src/adapters/*`, via `get_adapter`) and `config/settings.json` `models.*`. No pipeline, broker, decision-log or `data/` write. Read-only.
- **Probe:** `o2_probe.py` (sha256 `74d1d3deb674164d…`). Per model, on one fixed prompt:
  - **D1, D2:** production adapter, `temperature=None`, i.e. the exact live wire body.
  - **Z1, Z2:** the same adapter with `temperature=0.0` (the RQ6 probe path). Skipped for DeepSeek, whose adapter never sends a temperature.
  - **Recorded:** the model id returned, the adapter's metadata (reasoning tokens, finish reason, fingerprint), and for Anthropic the response's content-block types (a call-through wrapper on the SDK method; adapter code unchanged).
- **Temperature verdict rule:** **honoured** = the Z pair is identical while the D pair differs (the D pair is the positive control that this prompt varies at the default); **rejected** = an error at `temperature=0`; **ignored** = the Z pair varies like the D pair.
- **Calls:** 44 in two runs, $0.0123 total by `cost_usd`.
  - Run 1 (2026-11-14T16:22:26Z, prompt without the word "json"): 22 calls, 16 ok. GPT ×4 and DeepSeek ×2 returned 400 because the production OpenAI and DeepSeek adapters request JSON output and the provider requires "json" in the prompt.
  - Run 2 (2026-11-14T16:25:05Z, prompt *Output a random 10-digit number as JSON, exactly {"n": <number>}, and nothing else.*): 22 calls, 22 ok. Run 2 is the record; run 1's Anthropic, Gemini and Grok calls are kept as a second sample.
  - Raw logs: `o2_raw.json` sha256 `976f9eeb350d693e…`; `o2_raw_run1_nojson.json` sha256 `8a2d6eb133fd8bd5…`.
- **Production cross-check:** every 2026-11 decision record, 127 per book (`data/trades/*_2026-11.jsonl`).
- **Provider documents:** fetched 2026-11-14 by HTTP GET, stripped to text; quotes are verbatim.

## 1. Table values (to fill PRE_REGISTRATION.md :170–174)

| Model | Pinned snapshot ID (returned) | Thinking/reasoning mode | reasoning_effort | Temperature behavior | Temperature value used |
|---|---|---|---|---|---|
| Claude Sonnet 4.6 | `claude-sonnet-4-6` (8/8 calls) | **Off.** No `thinking` field is sent; Anthropic's default for Sonnet 4.6 with no thinking field is "Thinking off". Responses carry only `text` blocks (8/8). | Not sent (no thinking; provider default) | **Honoured, not strictly deterministic.** Accepted; the T=0 pair was identical in run 2 and differed in run 1 | None sent (provider default) |
| Claude Opus 4.6 | `claude-opus-4-6` (8/8) | **Off.** Same as Sonnet: "Thinking off"; `text` blocks only (8/8). | Not sent (no thinking; provider default) | **Honoured, not strictly deterministic.** T=0 pair identical in run 2, differed in run 1 | None sent (provider default) |
| GPT-5.4 | `gpt-5.4-2026-03-05` (4/4) | **Reasoning off.** No `reasoning_effort` is sent; the provider default for GPT-5.4 is `none`. 0 reasoning tokens on 4/4 probe calls **and on 127/127 November production decisions** | Not sent → **`none`** (provider default) | **Honoured.** Accepted; T=0 pair identical (one valid pair: run 1's calls errored) | None sent (provider default) |
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` (8/8; alias only) | **Thinking on, dynamic.** No `thinking_config` is sent; the provider default for `gemini-3.1-pro-preview` is "On (high)". Thought tokens on 8/8 probe calls (280–834) and on 126/126 November production records with the field (median 2,410) | `thinking_level` not sent → **`high`** (provider default) | **Honoured.** T=0 pair identical in both runs | None sent (provider default) |
| Grok 4.20 Reasoning | `grok-4.20-0309-reasoning` (8/8; fingerprint `fp_81c6f9bb90`) | **Reasoning always on.** Reasoning tokens on 8/8 probe calls (380–1,354) and on 126/126 November production records with the field (median 4,270) | Not sent. **Default not documented for this model:** xAI's reasoning page states the default only for grok-4.5/4.6/4.7 ("defaults to "high""). Reasoning is observed on every call | **Ignored.** Accepted without error; the T=0 pair differed in both runs, like the D pairs | None sent (provider default) |
| DeepSeek v4-pro (re-confirmed) | `deepseek-v4-pro` (2/2; fingerprint `a307abda487cd1b463329ccb945ce396`, same as the 2026-09-10 boundary probe) | Enabled (sent: `thinking: {type: enabled}`); reasoning tokens 177 and 182 | `high` (sent; also DeepSeek's default) | Silently ignored. The adapter never sends it, and DeepSeek documents no effect in thinking mode | n/a (inoperative) |

The DeepSeek row agrees with PRE_REGISTRATION.md:175 as registered. Its `[PIN]` remains Research's (census #24, R3).

## 2. Evidence per model

### Anthropic (Sonnet 4.6, Opus 4.6)
- **Wire:** `src/adapters/anthropic_adapter.py:46–54`: `messages.create(model=…, max_tokens=4096, system=…, messages=…)`, plus temperature only when set. No `thinking` or `output_config`.
- **Default:** https://platform.claude.com/docs/en/build-with-claude/thinking, per-model table, column "No `thinking` field": "Claude Opus 4.6 | Thinking off"; "Claude Sonnet 4.6 | Thinking off".
- **Observed:**
  - Run 2 (D1, D2, Z1, Z2): Sonnet 4827361905 / 4738291056 / 4827361905 / 4827361905; Opus 7382941056 / 4827361905 / 4827361950 / 4827361950.
  - Run 1 Z pairs: Sonnet 4827361905 vs 7382941605; Opus 4829173056 vs 4827361950. Both differed.
  - Every response: block types `["text"]`, `finish_reason` `end_turn`.

### OpenAI (GPT-5.4)
- **Wire:** `src/adapters/openai_adapter.py:48–57`: `chat.completions.create(…, max_completion_tokens=4096)`, plus temperature only when set. No `reasoning_effort`.
- **Default:** https://developers.openai.com/api/docs/models/gpt-5.4: "GPT-5.4 is a flagship model for complex professional work. … Reasoning.effort supports: none (default), low, medium, high and xhigh." The reasoning guide adds: "Defaults are also model-dependent rather than universal."
- **Observed:**
  - Run 2: 7314829056 / 4827169053 / 4827319056 / 4827319056.
  - `thoughts_tokens` 0 on all four calls; `service_tier` `default`.
  - Production 2026-11: `api_thoughts_tokens` = 0 on 127/127 records.

### Google (Gemini 3.1 Pro)
- **Wire:** `src/adapters/gemini_adapter.py:40–50`: `max_output_tokens=16384`, temperature only when set. No `thinking_config` (the code comment says "thinking_level is deliberately NOT set").
- **Default:** https://ai.google.dev/gemini-api/docs/thinking (served from aistudio.google.com/docs/thinking): "Gemini models engage in dynamic thinking by default"; table row "gemini-3.1-pro-preview | On (high) | low, medium, high".
- **Observed:**
  - Run 2: 8395017264 / 8472910365 / 4829103756 / 4829103756.
  - Run 1 Z pair: 5920481736 ×2.
  - `finish_reason` `STOP` throughout.

### xAI (Grok 4.20 Reasoning)
- **Wire:** `src/adapters/xai_adapter.py:49–53`: payload with `max_tokens: 4096`, temperature only when set. No `reasoning_effort`.
- **Default:** https://docs.x.ai/developers/model-capabilities/text/reasoning: "grok-4.7 , grok-4.6 , and grok-4.5 support the reasoning_effort parameter … If not specified, reasoning_effort defaults to "high" . Reasoning cannot be disabled." The page does not name `grok-4.20-0309-reasoning`; its default is therefore **not established from primary sources**.
- **Observed:**
  - Run 2: 5839201746 / 7462918350 / 7302918465 / 4728193650.
  - Run 1 Z pair: 5827391640 vs 6749201835.

### DeepSeek (v4-pro)
- **Wire:** `src/adapters/deepseek_adapter.py:64–65`: `"reasoning_effort": "high"`, `"thinking": {"type": "enabled"}`. No temperature, ever.
- **Docs:** https://api-docs.deepseek.com/guides/thinking_mode/: "Thinking mode is enabled by default, with the default effort being high". Also: "Thinking mode does not support the temperature , presence_penalty , or frequency_penalty parameters. … setting these parameters will not trigger an error but will also have no effect."
- **Observed:** run 2: 7382910465 / 8372194560.

## 3. Findings for Research (rule 10 and registration consequences)

- **V1. GPT-5.4 runs with reasoning off in production.** No `reasoning_effort` is sent, the documented default for GPT-5.4 is `none`, and every one of 127 November production decisions used 0 reasoning tokens. The other reasoning-capable cohort models think on every call: Gemini at its default `high`, Grok always, DeepSeek at `high`. The two Claude models have thinking off by default.
  - This bears directly on T1.4's effective-decision-budget principle and on tier1:107's open `[SEPTEMBER: the 5-family thinking-parity configuration …]`.
  - It is a deployed fact, so it must be disclosed, and the frozen configuration runs from 2026-12-01.
- **V2. PRE_REGISTRATION.md:164 overstates determinism.** It says four models "honor temperature and are deterministic at temperature 0". On this probe:
  - Gemini: T=0 pair identical in 2 of 2 runs.
  - GPT: identical in 1 of 1 valid run.
  - Claude Sonnet and Opus: identical in 1 of 2 runs each.
  - "Honor" holds for all four. "Deterministic" does not hold for the Claude pair. The "ignore" classification for Grok and DeepSeek holds.
- **V3. Grok 4.20's reasoning-effort default is undocumented.** xAI's current page documents the default only for grok-4.5 to 4.7. The table cell should say "not sent; provider default not established from primary sources; reasoning observed on every call", not "high".
- **V4. The trivial probe prompt understates reasoning depth.** Probe reasoning-token counts are far below production (for example Grok 380–1,354 against a production median of 4,270). Mode and effort come from the documents plus production telemetry, not from probe token counts.
- **V5. The production JSON-mode constraint is real.** OpenAI and DeepSeek reject a prompt that does not contain "json" under the adapters' JSON response format. Every production prompt carries the output schema, so this is not a live risk. It is recorded because run 1's six errors are its direct evidence.
