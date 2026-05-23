# RQ6 Rationale Correction — Empirically Verified Basis

- **Drafted:** May 22, 2026
- **Purpose:** Correct already-committed pre-registration text. The completed
  five-model API-configuration verification falsified the premise the RQ6 reframe
  was justified on. The reframe's conclusion stands — on a stronger, verified basis.
  This memo provides the corrected verbatim wording for landing.
- **Authoritative:** this memo supersedes the corresponding RQ6-rationale wording
  wherever it appears in earlier draft artifacts (`RQ5-RQ6-spec-completion.md`
  Section A; `PRE_REGISTRATION-RQ6-section.md`). Operations lands the corrected
  wording into the committed `METHODOLOGY.md` and `PRE_REGISTRATION.md`.

## What was wrong

The RQ6 reframe was justified on the premise that temperature 0 is "a no-op across
most or all of the cohort" and that "OpenAI and xAI reasoning models constrain or
ignore" temperature. Empirical test calls (~41 calls, deterministic-at-temp-0 vs.
varied-at-temp-1) falsify this: four of six models — GPT-5.4, Gemini 3.1 Pro,
Claude Sonnet 4.6, Claude Opus 4.6 — honor temperature and are deterministic at
temperature 0. Only Grok 4.20 Reasoning and DeepSeek v4-pro silently ignore it.

## Why the reframe's conclusion still holds — on a stronger basis

The verification established the correct, and stronger, basis: the deployed
trading pipeline sends no temperature parameter to any of the six models — every
model adapter omits it. Each model therefore runs at its provider-default sampling
configuration, and a provider default is not temperature 0. "Non-determinism at
temperature 0" measures an off-deployment configuration for the entire cohort —
true for all six regardless of whether a model honors temperature. This basis is
uniform, code-level, and verifiable by inspecting the adapters; it does not depend
on any claim about per-model temperature behavior.

## Correction 1 — METHODOLOGY.md, section "API Non-Determinism (RQ6) — Deployed-Configuration Basis"

Replace the section body with:

> RQ6 characterizes API non-determinism. Its original framing — repeated runs at
> temperature 0 — measures a sampling configuration the experiment never trades at.
> The deployed trading pipeline sends no temperature parameter to any of the six
> models: every model adapter omits it, so each model runs at its provider's default
> sampling configuration, and a provider default is not temperature 0.
> "Non-determinism at temperature 0" would therefore characterize an off-deployment
> configuration for the entire cohort.
>
> Per-model temperature behavior was verified empirically and is recorded as a
> disclosed fact in the Per-Model API Configuration table: four of the six models
> (GPT-5.4, Gemini 3.1 Pro, Claude Sonnet 4.6, Claude Opus 4.6) honor the temperature
> parameter and are deterministic at temperature 0 in test calls; two (Grok 4.20
> Reasoning, DeepSeek v4-pro) silently ignore it. This per-model behavior is
> disclosed for completeness; it does not bear on the reframe, because the deployed
> pipeline sets no temperature parameter for any model — temperature 0 is
> off-deployment for the whole cohort regardless of which models would honor it.
>
> RQ6 is therefore operationalized on the deployed configuration: each model's
> run-to-run non-determinism is characterized at the exact configuration it is
> traded at — provider-default sampling — recorded in the Per-Model API Configuration
> table. RQ6 is a characterization research question, not a null-hypothesis test, and
> is not a member of the Benjamini-Hochberg FDR family. Its full operationalization,
> metric, and inference are specified in the RQ6 entry of the pre-registration.

## Correction 2 — METHODOLOGY.md, section "Reasoning Configuration and Cross-Model Equivalence"

The same verification falsified a second statement in this committed section: it
currently states reasoning configuration "is set explicitly on every API call and
never left to the provider default." This is empirically false — five of six
adapters send no reasoning-effort parameter and therefore run at the provider
default. Replace the section's opening paragraph with:

> For reasoning-capable models, the reasoning/thinking configuration is part of the
> pinned, documented model identity and was verified empirically. The deployed
> configuration is the provider default for five models — GPT-5.4, Gemini 3.1 Pro,
> Claude Sonnet 4.6, Claude Opus 4.6, and Grok 4.20 Reasoning send no reasoning-effort
> parameter — and reasoning_effort=high for DeepSeek v4-pro. Per DeepSeek's
> documentation, "high" is DeepSeek v4-pro's default effort tier ("max" being an
> optional higher tier), so DeepSeek v4-pro likewise runs at its provider-default
> reasoning effort. All six models therefore run at provider-standard reasoning
> effort.

The section's equivalence-principle paragraph (no provider-independent unit of
reasoning effort; the project commits to provider-standard, not maximum; residual
cross-provider non-equivalence disclosed as a limitation) is unchanged — the
principle holds; provider-default is provider-standard.

## Correction 3 — PRE_REGISTRATION.md, RQ6 section

### 3a. Rationale paragraph

Replace the "Operationalization — Deployed Configuration" rationale with:

> The original operationalization — repeated runs at temperature 0 — is withdrawn: it
> would characterize a sampling configuration the experiment never trades at. The
> deployed trading pipeline sends no temperature parameter to any of the six models;
> every model adapter omits it, so each model runs at its provider's default sampling
> configuration, which is not temperature 0. Repeated runs at temperature 0 would
> therefore measure an off-deployment configuration for the entire cohort.
>
> Per-model temperature behavior was verified empirically and is recorded as a
> disclosed fact in the Per-Model API Configuration table: four of six models
> (GPT-5.4, Gemini 3.1 Pro, Claude Sonnet 4.6, Claude Opus 4.6) honor temperature and
> are deterministic at temperature 0; two (Grok 4.20 Reasoning, DeepSeek v4-pro)
> silently ignore it. This is disclosed for completeness and does not bear on the
> reframe: because the deployed pipeline sets no temperature for any model,
> temperature 0 is off-deployment uniformly across the cohort.

### 3b. "Why RQ6 Is Not Scoped to the Temperature-Honoring Subset" subsection

This subsection rests on the same falsified premise — it asserts the
temperature-honoring subset is "plausibly one model or zero," when it is in fact
four of six. Replace the subsection with:

> RQ6 covers all six models uniformly. RQ6 is a single uniform six-model
> characterization; it is not split by temperature-honoring behavior. The reframe's
> basis is the deployed configuration — provider-default sampling with no temperature
> parameter set — which is uniform across all six models. The empirical fact that
> four models honor temperature and two ignore it concerns a parameter the deployed
> pipeline does not use, and does not partition the RQ6 design. RQ6 therefore
> characterizes run-to-run non-determinism for all six models on the same
> deployed-configuration basis.

### 3c. Trivial follow-up

The RQ6 section's "Verification Commitment" language (temperature support to be
verified for all six models before the deposit) can now be updated to past tense —
the verification is complete and the Per-Model API Configuration table is filled.

## Per-model temperature behavior — disclosed fact (for the Per-Model API Configuration table)

Recorded as a disclosed fact, not as the reframe's justification:

- Honor temperature (deterministic at temperature 0 in test calls): GPT-5.4,
  Gemini 3.1 Pro, Claude Sonnet 4.6, Claude Opus 4.6.
- Silently ignore temperature: Grok 4.20 Reasoning, DeepSeek v4-pro.
- The deployed pipeline sends no temperature parameter to any of the six.

## Note on the correction

The original RQ6 rationale generalized from one verified case (DeepSeek ignores
temperature) to an assumed cohort-wide claim, and that assumption entered committed
text before verification. The corrected rationale is grounded in the deployed
pipeline's actual behavior — what the adapters send — which is the operative fact
and is uniformly verifiable. The reframe's direction and conclusion are unchanged;
only the stated basis is corrected, onto firmer ground.
