# RQ5 Pilot Window & Phase A Data Integrity — Addendum

- **Drafted:** May 22, 2026
- **Status:** Decided. Resolves the pre-registration items raised by the Task 1
  RQ5 data-availability diagnostic.
- **Landing:** Committed by Task 2 alongside `RQ5-RQ6-specification.md`. Part A
  lands in the RQ5 entry of `RQ5-RQ6-specification.md` and `v1.json`; Part B lands
  as a data-integrity section in `PRE_REGISTRATION.md`.

---

## Part A — RQ5 Phase A pilot-window rule

**Rule.** The RQ5 headline test's Phase A pilot estimation uses a **uniform series
start across all six models**: the start date is the **later of** (a) the end of
the designated Phase A shakedown period and (b) the end of the launch-window
state-file commingling corruption (~April 23, 2026).

- The exact date is resolved from the project's shakedown-period documentation. It
  is `>= April 23, 2026`. If no shakedown-period end date is documented, one is
  designated for the record; it must be `>= April 23, 2026`.
- Pin the **rule** in `v1.json` (RQ5 entry) and `RQ5-RQ6-specification.md`; the
  resolved date is recorded separately, consistent with the project's
  pin-the-rule-not-the-value discipline for `K`, `M`, `L`.

**Rationale.** RQ5's headline statistic `beta` is a cross-model pooled estimate
(pooled panel regression with model fixed effects). A per-model ragged start —
April 9 for GPT/Gemini/Grok/DeepSeek, ~April 23 for Sonnet/Opus — is econometrically
executable but injects a confound: the four models would contribute shakedown-window
behavior to `beta` while the two Anthropic models would not. Shakedown-period
behavior is unlikely to match stable-operation behavior (the pipeline was being
actively debugged in that window), so an uneven distribution of shakedown
contamination across the panel biases a cross-model estimate. A uniform start
removes the asymmetry. The cost is low: the window is pilot/exploratory data, ~8% of
a six-month pilot, and is shakedown-quality data for all six models regardless of
the commingling defect.

**Scope.** This rule is Phase A pilot/exploratory only. It does **not** affect the
Phase B confirmatory RQ5 test, which runs the full confirmatory window
(November 1, 2026 onward) with all six models present and balanced.

**Generalization.** The same uniform post-shakedown start is applied to **all RQ
Phase A pilot analyses**, not RQ5 alone — the commingling defect corrupts every
per-model behavioral metric for the two Anthropic models in the affected window
(see Part B). For RQ6, whose frozen-context sampling is not a time series, the
corrupted/shakedown window is excluded from the eligible context pool. A single
global rule handles the corruption once, with no per-RQ special-casing.

---

## Part B — Phase A Data Integrity (for `PRE_REGISTRATION.md`)

### Launch-window state-file commingling (Claude Sonnet / Claude Opus)

During the launch window (~April 9–22, 2026), a state-file commingling defect caused
Claude Opus to write to Claude Sonnet's state file. As a result, the recorded
holdings, cash, and total portfolio value for **both** Anthropic models in that
window do not reflect each model's own independent trading. The defect was resolved;
both models reconstruct cleanly from ~April 23, 2026 onward. The corruption is
**forward-unfixable** — no logging change recovers the affected window.

- **Scope.** The corruption affects all per-model behavioral data for Claude Sonnet
  and Claude Opus in the affected window — not RQ5 alone. It falls entirely within
  the pilot/exploratory phase and overlaps the designated Phase A shakedown period.
  It does **not** touch Phase B confirmatory data.
- **Handling.** All Phase A pilot analyses use a uniform series start across the six
  models, beginning at the later of the designated shakedown-period end and the
  corruption-end date (~April 23, 2026) — see Part A. The corrupted window is
  thereby excluded for all models, and no per-RQ special-casing of the Anthropic
  corruption is required.

### RQ5 dependent variable — derived quantity

The RQ5 headline test's dependent variable, trade-driven `dHHI`, requires pre-trade
portfolio weights. **Pre-trade weights are not directly logged for any model.** They
are reconstructed from the prior decision period's post-trade holdings re-priced at
the current period's prices, normalized to risky-position weights. The reconstruction
has been validated as robust. The pre-registration records that RQ5's dependent
variable is a **derived/reconstructed quantity**.

- **Phase B integrity upgrade (recommended).** A forward-only logging change to
  persist a direct pre-trade portfolio snapshot at each decision period would make
  the Phase B confirmatory RQ5 dependent variable a **logged** rather than a
  reconstructed quantity. This removes reliance on the reconstruction's assumptions —
  notably the assumption that holdings change between decision periods only through
  price drift, which a between-period 15% stop-loss trigger would violate. The change
  is additive, low-risk, and does not alter trading behavior. Recommended to be
  implemented before Phase B begins (November 1, 2026) and logged as a data-integrity
  infrastructure change. It is forward-only and does not affect Phase A pilot data.

### Gemini missing-decision rate

Approximately 49% of Gemini's Phase A decision periods are no-decision ticks
resulting from failed API calls. The remaining periods reconstruct correctly. This
is disclosed as a data-quality issue affecting **all** of Gemini's RQ estimates, not
RQ5 alone:

- It reduces statistical power for every Gemini per-model estimate.
- **Random vs. systematic — required pre-deposit diagnostic.** If the failures are
  random, the effect is power loss only. If they are correlated with market
  conditions, API load, prompt size, or any other factor, Gemini's observed periods
  are a **biased sample** of its decision opportunities — a selection-bias threat to
  validity, not only a power loss. Whether the failures are random or systematic
  must be characterized before the OSF deposit.
- **Ongoing vs. historical.** If the ~49% rate reflects an ongoing condition rather
  than a resolved early-Phase-A issue, it endangers Gemini's Phase B confirmatory
  data and must be addressed operationally before November 1, 2026. The current
  failure rate is to be confirmed.

---

## Task 2 instruction

Task 2 commits `RQ5-RQ6-specification.md` and this addendum to the repo, lands Part A
into the RQ5 entry of `v1.json` and `RQ5-RQ6-specification.md`, and lands Part B as a
Phase A data-integrity section of `PRE_REGISTRATION.md`. The RQ5 pilot-window rule
(Part A) is incorporated into `compute_rq5`'s Phase A pilot estimation.
