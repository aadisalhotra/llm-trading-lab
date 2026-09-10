# Adapter field-retention audit — cohort-wide, 2026-09-09

**Trigger.** The DeepSeek V4.1-Flash forced substitution
(`docs/boundaries/deepseek_v41_substitution_2026-09-10.md`) exposed that
`deepseek_adapter` read four things off each response — the model-id echo, two
token counters, the cost — and discarded everything else, including a build
fingerprint that had been on the wire since the cell went live. The 2026-07-27
V4-GA record had concluded such a swap was "structurally invisible to lab
telemetry"; it was not, we were just not looking.

**Question asked of every adapter:** what does this provider return that
identifies the model or explains the call, and what were we throwing away?

**Sources.** Authoritative for four of five — the installed SDKs' own response
types (`anthropic` 0.92.0, `openai` 2.31.0, `google-genai` 2.16.0) and, for
DeepSeek, a live pre-boundary probe against `deepseek-v4-pro`. xAI posts raw
REST with no local type information; its OpenAI-compatible response shape is
taken from published API references, and the reads are defensive, so a field
that turns out not to exist yields `None` rather than an error. **The first
production Grok call after deployment confirms or refutes it at no cost** —
check `api_system_fingerprint` on the next `grok` decision record.

---

## What each provider exposes

Legend: **✓ kept** = was already captured · **+ added** = this audit ·
**— n/a** = the provider does not expose it.

| | build fingerprint | finish reason | reasoning tokens | cache split | service tier |
|---|---|---|---|---|---|
| **DeepSeek** | **+ `system_fingerprint`** | + `finish_reason` | + `completion_tokens_details.reasoning_tokens` | + `prompt_cache_hit/miss_tokens` | — |
| **OpenAI** | **+ `system_fingerprint`** | + `finish_reason` | + `completion_tokens_details.reasoning_tokens` | + `prompt_tokens_details.cached_tokens` | + `service_tier` |
| **xAI** | **+ `system_fingerprint`** | + `finish_reason` | + `completion_tokens_details.reasoning_tokens` | + `prompt_tokens_details.cached_tokens` | — |
| **Gemini** | — none exposed | ✓ kept `finish_reason` | ✓ kept `thoughts_token_count` | + `cached_content_token_count` | — |
| **Anthropic** | **— none exposed** | + `stop_reason` | **— none exposed** | + `cache_read` / `cache_creation` | + `service_tier` |

Also added: Gemini `finish_message` and prompt-level `block_reason`; Anthropic
`stop_sequence`.

### The asymmetry that matters

**Anthropic exposes no build fingerprint and no reasoning-token counter.**
`Message` carries `container, content, id, model, role, stop_details,
stop_reason, stop_sequence, type, usage` — nothing that identifies the build
behind the model string. So for the two Claude cells, `model` remains the only
identity signal, and a string-stable substitution by Anthropic would be
undetectable in exactly the way DeepSeek's nearly was.

That is a disclosable limitation of the experiment, not a gap this audit can
close. Gemini is a partial case: no fingerprint either, but `model_version`
returns a *build-level* id (`gemini-3.1-pro-002`) rather than the alias we
request, so it already carries more identity than a bare echo.

Ranked by how well a string-stable substitution could be detected:
**DeepSeek / OpenAI / xAI** (fingerprint) → **Gemini** (build-level version
string) → **Anthropic** (alias only).

### Cache accounting differs, and mixing the conventions mis-states cost

- **OpenAI, xAI, Gemini** report an input count **inclusive** of cached tokens
  → `miss = input − hit`.
- **Anthropic** reports `input_tokens` **exclusive** of cache reads
  → `miss = input`, and cache *writes* are a third counter billed at a premium.

Both conventions are pinned by test. Applying the OpenAI subtraction to an
Anthropic response would under-report input tokens by the entire cache-read
count.

---

## Why these fields and not others

Captured because each has a stated use:

- **fingerprint** — the only identity signal that moves independently of the
  model string, and unlike cost it does not depend on our own rate table.
- **finish reason** — separates a truncated decision from a complete one. Live
  risk, not theoretical: Anthropic runs under a 4096-token cap, and the July
  Gemini completeness investigation stalled precisely for want of this field.
- **reasoning tokens** — bill inside the output count and against the cap, so a
  cap-stop with few visible tokens is thinking overrun, not a long answer. Also
  the sharpest single discriminator of a reasoning-tier change.
- **cache split** — `cost_rates.py` prices every call at the full (cache-miss)
  rate as a deliberate upper bound. This makes that conservatism measurable
  instead of assumed.
- **service tier** — Priority/Flex bill differently and are not modelled, so
  this is what makes the standard-tier assumption behind every cost figure
  checkable.
- **Gemini `block_reason`** — prompt-level, and the sharpest of the Gemini
  gaps: a blocked prompt returns **no candidates at all**, so the finish-reason
  path never fires and the run currently looks like an unexplained empty
  response. Deliberately kept out of `api_finish_reason`: the Phase B
  MAX_TOKENS gate reads that field, and synthesising new values into a series a
  live gate consumes would change what it counts.

Considered and declined: per-response `id` (no analytical use; failures already
persist `raw_response_on_failure`), `logprobs`, `safety_ratings`,
`grounding_metadata`, `total_token_count` (derivable). Each is one `.get()`
away if a use appears.

---

## Changes

| Path | Change |
|---|---|
| `src/adapters/openai_adapter.py` | + fingerprint, finish_reason, reasoning→`thoughts_tokens`, cache split, service_tier |
| `src/adapters/xai_adapter.py` | + same five, OpenAI-compatible shape |
| `src/adapters/anthropic_adapter.py` | + `stop_reason`→finish_reason, `stop_sequence`→finish_detail, cache read/creation, service_tier |
| `src/adapters/gemini_adapter.py` | + `finish_message`, prompt-level `block_reason`, cache split |
| `src/logging/decision_log.py` | + `cache_creation_tokens`, `api_finish_detail`, `api_block_reason`, `api_service_tier` |
| `tests/test_adapter_field_retention.py` | new, 12 tests |

`src/adapters/deepseek_adapter.py` was closed separately in `1a5fee77`.

All reads are defensive: a provider that stops returning a field yields `None`
and never costs a trading decision. Every content extraction that feeds
`BaseAdapter`'s parse-level retry keeps its original indexed form, so a
malformed response still raises rather than being swallowed into a successful
empty decision. New log fields are additive and `.get()`-read, the same shape
as the 2026-08-05 `screening_input_tokens` addition.

Suite **422 → 434** (+12). Nothing pre-existing changed status.

---

## Consequence for the boundary record

These fields have **no pre-boundary baseline for any provider except DeepSeek**,
where a probe caught them hours before the substitution. For the other four,
absolute post-deployment values are still interpretable — a truncation rate, a
cache-hit share, a fingerprint that then holds steady — but no before/after
comparison across an earlier boundary is possible. That is the cost of the gap
having existed, and it is not recoverable.

Which is the argument for landing this tonight rather than at the next
convenient moment: the fields cost nothing to keep and everything to have
missed.
