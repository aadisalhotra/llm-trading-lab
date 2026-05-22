# RQ5/RQ6 Spec Completion — Closing the Task 2 Blockers

- **Drafted:** May 22, 2026
- **Purpose:** Produce the committable text and ratified decisions that Task 2's
  read-and-verify phase found missing, so the RQ5/RQ6 spec can be landed.
- **Root cause:** `RQ5-RQ6-specification.md` referenced artifacts that exist only as
  chat drafts — a numbered METHODOLOGY structure (no numbering exists), and a
  per-model API-configuration table (never produced). On landing, commit
  `RQ5-RQ6-specification.md`, this file, and the items below **as one consistent
  set**, and make `RQ5-RQ6-specification.md` self-contained — no references to
  uncommitted artifacts.

---

## A. METHODOLOGY.md — committable named sections

METHODOLOGY.md uses **named** sections, not numbered ones. Add the four sections
below. The "API Non-Determinism" section **replaces** whatever obsolete
temperature-0 / non-determinism text METHODOLOGY.md currently carries; Operations
locates and removes that text on landing.

### Section: Reasoning Configuration and Cross-Model Equivalence

For reasoning-capable models, the reasoning/thinking configuration is part of the
pinned model identity. It is set explicitly on every API call and never left to the
provider default.

There is no provider-independent unit of reasoning effort — each provider's
effort/thinking settings are calibrated to its own scale, and one provider's default
does not correspond to another's. The project therefore commits to a single explicit
equivalence rule: **all reasoning-capable models in the cohort run at
provider-standard (default) reasoning effort, not maximum.** The residual
cross-provider non-equivalence this leaves is disclosed as a limitation. Any future
move to maximum effort would be a separate, deliberate, cohort-wide change applied
consistently to all reasoning models and logged. (This equivalence principle is one
of the September methodology-lock items.)

### Section: Pinned-Snapshot Guarantee — Scope and Caveat

The pinned snapshot fixes the model's **weights**: under a pinned snapshot
identifier, the underlying parameters are not updated by the provider. For
Anthropic's Claude 4.6-generation models the dateless generation ID is itself the
canonical fixed snapshot and weights are never updated under an existing ID.

The pin does **not** guarantee that the surrounding serving infrastructure is fixed.
Anthropic documents that the request router, safety classifiers, and sampling
implementation can change under a fixed model ID and can occasionally produce minor
behavioral drift. The pinned-snapshot methodology therefore controls model-weight
identity, not the full serving stack. Residual longitudinal behavioral drift from
serving-infrastructure changes is a known limitation affecting all RQs, plausibly
present for all providers whether or not separately documented. RQ6 addresses the
distinct question of run-to-run non-determinism.

### Section: Per-Model API Configuration

The deployed configuration of each model is recorded in the table below. It is the
canonical record referenced by RQ6's operationalization and is reproduced in the
RQ6 entries of `PRE_REGISTRATION.md` and `v1.json` (pre-registration sections are
self-contained).

| Model | Provider | Pinned snapshot ID | Thinking/reasoning mode | reasoning_effort (provider-standard) | Temperature behavior | Temperature value used |
|---|---|---|---|---|---|---|
| Claude Sonnet 4.6 | Anthropic | `claude-sonnet-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Claude Opus 4.6 | Anthropic | `claude-opus-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| GPT-5.4 | OpenAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Gemini 3.1 Pro | Google | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Grok 4.20 Reasoning | xAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| DeepSeek v4-pro | DeepSeek | `[PIN]` | Enabled | `high` | Silently ignored (inoperative in thinking mode) | n/a (inoperative) |

Placeholder key:

- `[PIN]` — the dated immutable snapshot ID; a pinning task, completed before the
  OSF deposit. (Claude rows need no `[PIN]`: the dateless 4.6-generation ID is the
  canonical fixed snapshot.)
- `[VERIFY]` — established by the per-model API-configuration verification. **This
  verification is a hard pre-deposit dependency of RQ6, not a separable later task.**
  DeepSeek's row is verified; the five other models' configuration cells must be
  verified before the OSF deposit. The `reasoning_effort` cell is set to the
  provider-standard value per the equivalence principle; the specific value and
  parameter name are filled by verification. Temperature behavior is one of:
  honored / silently ignored / constrained to a range / not exposed.

### Section: API Non-Determinism (RQ6) — Deployed-Configuration Basis

RQ6 characterizes API non-determinism. Its original framing — repeated runs at
temperature 0 — assumed temperature is a usable control across the cohort. It is
not: the cohort is six reasoning models, and reasoning/thinking modes broadly do not
honor temperature (DeepSeek V4 thinking mode silently ignores it; other providers'
reasoning models commonly ignore, constrain, or do not expose it). Temperature 0 is
treated as unavailable for the cohort unless a per-model check proves otherwise.

RQ6 is therefore operationalized on the **deployed configuration** — each model's
run-to-run non-determinism is characterized at the exact configuration it is traded
at, recorded in the Per-Model API Configuration table. RQ6 is a characterization
research question, not a null-hypothesis test, and is not a member of the
Benjamini-Hochberg FDR family. Its full operationalization, metric, and inference
are specified in the RQ6 entry of the pre-registration.

---

## B. RQ5 headline-test block length — ratified

**Rule.** The moving-block bootstrap block length for the RQ5 headline test is:

```
L = round(n^(1/3))
```

where `n` is the number of decision periods in the analysis window — the uniform
Phase A pilot window for the pilot estimate, the full Phase B confirmatory window
for the confirmatory estimate. `L` is computed once `n` is fixed; the resolved
integer is logged in `v1.json` alongside the rule.

**Rationale.** One formula, one input, zero researcher discretion — the
pre-registration-clean choice. A data-driven decorrelation-based rule would adapt to
the actual serial dependence but introduces discretion (the estimator, the
proportionality constant) that a referee can question. `n^(1/3)` is the standard
default growth rate for block length.

**Sensitivity (registered).** The headline test is re-run at block lengths
`floor(L/2)` and `2L`. The headline `beta` is reported as block-length-robust if the
sign and BH-adjusted significance of `beta` are stable across `{floor(L/2), L, 2L}`.
For realistic analysis-window lengths (`n` on the order of 10^3 decision periods for
the pilot, larger for the confirmatory window), `L` falls in roughly the 11–16 range.

---

## C. Phase A shakedown-period end — designated

**Designation.** The Phase A shakedown period is designated as **April 9 – April 22,
2026 inclusive**. The uniform Phase A pilot-analysis window begins with the first
decision period of **April 23, 2026**.

**Basis (verified against the repository).** Two independent signals converge:

- *Commit history.* The last shakedown-class stabilization commit is `cacd8058`
  (April 21, 2026) — the fix for the state-file routing defect (Opus writing to
  Sonnet's state file). April 22 – May 12 contains only intraday tick commits and no
  stabilization activity; the next non-tick commits (May 13 onward) are
  post-shakedown infrastructure work, not pipeline stabilization.
- *Corruption-clearing.* The RQ5 data-availability diagnostic independently found
  the Sonnet/Opus state-file commingling corruption cleared the day after
  `cacd8058`, with both Anthropic models' data cleanly reconstructable from
  ~April 23, 2026 onward.

The pilot-window start is pinned at April 23, 2026 — the later edge of the
April 21–23 cluster the two signals bracket, and the date the data-availability
diagnostic explicitly certified — so that no possibly-tainted decision period enters
the pilot window.

**Correction note.** This supersedes the prior designation of May 1, 2026, whose
stated basis ("the April 15 – May 1 engineering stabilization sprint") does not
survive verification: the commit history shows no stabilization activity after
April 21, 2026. The May 1 designation is withdrawn.

**Why not a later date with a settling-in buffer.** A later designation would require
a buffer-length basis, and no buffer length is principled — any value (5, 10, 14
days) is a researcher degree of freedom with no stated rule. More decisively, the
commit history shows zero stabilization activity from April 22 through May 12: a
"settling-in" buffer presumes a window of instability that the repository evidence
affirmatively contradicts. The convergent, repo-checkable evidence for ~April 21–23
is the only evidence-supported basis.

**Consequence.** Per the uniform pilot-window rule (the later of the shakedown-period
end and the launch-window corruption end), both signals coincide at ~April 21–23 and
the uniform Phase A pilot-analysis window begins April 23, 2026. This applies to all
RQ Phase A pilot analyses. The entire shakedown period — not only the Sonnet/Opus
commingling window — is excluded from pilot analysis for all six models. The RQ5
block-length rule (Section B) is unchanged; only the resolved `n`, and hence the
integer `L`, shifts slightly with the corrected window and is computed once as
specified.

**Required on landing.** Write this designation, with its verified basis, into
METHODOLOGY.md and the PRE_REGISTRATION.md data-integrity section.

---

## D. Cross-reference corrections for `RQ5-RQ6-specification.md`

Replace each numbered METHODOLOGY reference with the section title:

| Current (incorrect) reference | Corrected reference |
|---|---|
| METHODOLOGY §9 | METHODOLOGY section "API Non-Determinism (RQ6) — Deployed-Configuration Basis" |
| METHODOLOGY §2.1 | METHODOLOGY section "Reasoning Configuration and Cross-Model Equivalence" |
| METHODOLOGY §2.2 | METHODOLOGY section "Pinned-Snapshot Guarantee — Scope and Caveat" |

Additionally: the RQ6 operationalization in `RQ5-RQ6-specification.md` (and the RQ6
entries of `v1.json` and `PRE_REGISTRATION.md`) should **reproduce** the Per-Model
API Configuration table inline rather than cross-reference it, so each
pre-registration section is self-contained.

---

## E. What this unblocks

- **RQ5 — fully landable now:** num_positions drop, the headline test (with
  tick-position fixed effects and the ratified `L` rule), the Gemini per-RQ
  handling, the Phase A data-integrity disclosure, and the designated shakedown date.
- **RQ6 — spec and table skeleton landable now:** the operationalization, metric,
  inference, decision rule, and the Per-Model API Configuration table structure
  (DeepSeek row complete) can be transcribed faithfully.
- **One hard dependency remains before the OSF deposit:** the five-model
  API-configuration verification (GPT-5.4, Gemini 3.1 Pro, Grok 4.20, Sonnet, Opus)
  fills the `[VERIFY]` cells, and the `[PIN]` snapshot IDs are resolved. This is an
  Operations empirical task; it gates the complete RQ6 and the deposit, not the
  internal landing.
