# Methodology

## Research Question
Given identical information, identical constraints, and identical execution infrastructure, which frontier LLM makes the best investment decisions — and can any of them beat a passive benchmark or a non-AI control?

## Experimental Controls

**Identical inputs.** Every model receives the same market data, the same prompt content, the same portfolio constraints, and the same per-model portfolio state. The only differences across providers are API-format adjustments (message structure, system prompt handling).

**Identical rules.** Max 50 positions, max 20% per name, no leverage, no shorting, no options. $100k paper / $1k live starting capital. 50-trade daily cap. Universe is 79 assets across 12 sectors (75 large-cap U.S. equities plus 4 commodity ETFs: GLD, SLV, USO, CPER).

**Identical execution.** All trades route through the same `executor.py`. Paper mode prices off real market quotes; live mode submits real orders to Alpaca.

## Acknowledged Variables (intentional, not bugs)

1. **Provider velocity.** If Anthropic ships an upgrade in August but OpenAI doesn't update until December, that asymmetry is a measured variable, not noise to control.
2. **Native data access.** Grok has live X/Twitter access baked into the model. This is logged and analyzed, not stripped out.
3. **Reasoning style differences.** Each model's reasoning chain is captured in full and analyzed qualitatively in monthly reports.

## Phases

| Phase | Dates | Capital | Purpose |
|---|---|---|---|
| 1: Build | Pre-launch (completed) | $0 | Pipeline + dashboard |
| 2: Test | Pre-launch (completed) | $0 | Dry runs, validate guardrails |
| A: Paper | Apr 9 – Oct 31, 2026 | $100k each | Validate viability (pilot) |
| Paper Final Report | Late Oct 2026 | — | Go/no-go for live |
| B: Live | Nov 1, 2026 – Nov 1, 2027 | $1k each | Real capital |
| Capstone | Nov 2027 | — | Final report |
| C: Scale | 2028+ | TBD | Optional |

## Model Evolution Policy

- **Core 5 are permanent.** Anthropic, OpenAI, Google, xAI, DeepSeek slots never close.
- **Models auto-upgrade monthly.** First trading day of each month, the pipeline checks each provider's flagship and transitions if a newer version exists. Logged with 30-day before/after comparison.
- **Expansion cohort, never replacement.** New providers can join with their own portfolio but never displace a core 5 model. This preserves head-to-head continuity.

## Reasoning Configuration and Cross-Model Equivalence

For reasoning-capable models, the reasoning/thinking configuration is part of the pinned model identity. It is set explicitly on every API call and never left to the provider default.

There is no provider-independent unit of reasoning effort — each provider's effort/thinking settings are calibrated to its own scale, and one provider's default does not correspond to another's. The project therefore commits to a single explicit equivalence rule: **all reasoning-capable models in the cohort run at provider-standard (default) reasoning effort, not maximum.** The residual cross-provider non-equivalence this leaves is disclosed as a limitation. Any future move to maximum effort would be a separate, deliberate, cohort-wide change applied consistently to all reasoning models and logged. (This equivalence principle is one of the September methodology-lock items.)

## Pinned-Snapshot Guarantee — Scope and Caveat

The pinned snapshot fixes the model's **weights**: under a pinned snapshot identifier, the underlying parameters are not updated by the provider. For Anthropic's Claude 4.6-generation models the dateless generation ID is itself the canonical fixed snapshot and weights are never updated under an existing ID.

The pin does **not** guarantee that the surrounding serving infrastructure is fixed. Anthropic documents that the request router, safety classifiers, and sampling implementation can change under a fixed model ID and can occasionally produce minor behavioral drift. The pinned-snapshot methodology therefore controls model-weight identity, not the full serving stack. Residual longitudinal behavioral drift from serving-infrastructure changes is a known limitation affecting all RQs, plausibly present for all providers whether or not separately documented. RQ6 addresses the distinct question of run-to-run non-determinism.

## Per-Model API Configuration

The deployed configuration of each model is recorded in the table below. It is the canonical record referenced by RQ6's operationalization and is reproduced in the RQ6 entries of `PRE_REGISTRATION.md` and `v1.json` (pre-registration sections are self-contained).

| Model | Provider | Pinned snapshot ID | Thinking/reasoning mode | reasoning_effort (provider-standard) | Temperature behavior | Temperature value used |
|---|---|---|---|---|---|---|
| Claude Sonnet 4.6 | Anthropic | `claude-sonnet-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Claude Opus 4.6 | Anthropic | `claude-opus-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| GPT-5.4 | OpenAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Gemini 3.1 Pro | Google | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Grok 4.20 Reasoning | xAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| DeepSeek v4-pro | DeepSeek | `[PIN]` | Enabled | `high` | Silently ignored (inoperative in thinking mode) | n/a (inoperative) |

Placeholder key:

- `[PIN]` — the dated immutable snapshot ID; a pinning task, completed before the OSF deposit. (Claude rows need no `[PIN]`: the dateless 4.6-generation ID is the canonical fixed snapshot.)
- `[VERIFY]` — established by the per-model API-configuration verification. **This verification is a hard pre-deposit dependency of RQ6, not a separable later task.** DeepSeek's row is verified; the five other models' configuration cells must be verified before the OSF deposit. The `reasoning_effort` cell is set to the provider-standard value per the equivalence principle; the specific value and parameter name are filled by verification. Temperature behavior is one of: honored / silently ignored / constrained to a range / not exposed.

## API Non-Determinism (RQ6) — Deployed-Configuration Basis

RQ6 characterizes API non-determinism. Its original framing — repeated runs at temperature 0 — assumed temperature is a usable control across the cohort. It is not: the cohort is six reasoning models, and reasoning/thinking modes broadly do not honor temperature (DeepSeek V4 thinking mode silently ignores it; other providers' reasoning models commonly ignore, constrain, or do not expose it). Temperature 0 is treated as unavailable for the cohort unless a per-model check proves otherwise.

RQ6 is therefore operationalized on the **deployed configuration** — each model's run-to-run non-determinism is characterized at the exact configuration it is traded at, recorded in the Per-Model API Configuration table. RQ6 is a characterization research question, not a null-hypothesis test, and is not a member of the Benjamini-Hochberg FDR family. Its full operationalization, metric, and inference are specified in the RQ6 entry of the pre-registration.

## Phase A Data Integrity

The Phase A shakedown period is designated **April 9 – April 22, 2026 inclusive**; the uniform Phase A pilot-analysis window opens with the first decision period of **April 23, 2026**. Basis, verified against the repository: the last shakedown-class stabilization commit is `cacd8058` (April 21, 2026 — the fix for the state-file routing defect that caused Claude Opus to write to Claude Sonnet's state file during ~April 9–22); April 22 – May 12 contains only intraday tick commits and no stabilization activity, and the commingling corruption clears from ~April 23 onward. All Phase A pilot analyses use this uniform post-shakedown start across all six models. The launch-window Sonnet/Opus commingling, the RQ5 reconstructed dependent variable, and the Gemini availability / per-RQ tick-position handling are detailed in `PRE_REGISTRATION.md` §3.9.

## Reporting

- **Daily**: dashboard auto-update
- **Weekly**: snapshot commit
- **Monthly**: full research report in `/reports`
- **Late Oct 2026**: Paper Trading Final Report (go/no-go for live)
- **Nov 2027**: Capstone

## Compliance
Personal investment experiment. Not financial advice. All risk is the operator's own.
