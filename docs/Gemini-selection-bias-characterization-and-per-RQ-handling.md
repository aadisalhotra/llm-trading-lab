# Gemini API-Failure Selection Bias — Characterization & Per-RQ Handling

- **Drafted:** May 22, 2026
- **Status:** Decided. Resolves the pre-registration items raised by the completed
  Gemini API-failure diagnostic.
- **Supersedes:** the "Gemini missing-decision rate" subsection of
  `RQ5-pilot-window-and-Phase-A-data-integrity.md` (which marked the random-vs-
  systematic question as a required diagnostic — now complete).
- **Landing:** Part 1 lands in the Phase A data-integrity section of
  `PRE_REGISTRATION.md`. Part 2 lands per-RQ in `PRE_REGISTRATION.md`; the RQ5 and
  RQ6 items also amend `RQ5-RQ6-specification.md` and `v1.json` (see "Spec
  amendments").

---

## Part 1 — Gemini availability disclosure

Gemini's Phase A API availability is disclosed as a **time series with mechanism**,
not a single percentage. A single cumulative figure (49.5%) is misleading: the
series is episodic and the current rate is low.

- **Weekly failure rate (Phase A):** ~28 / 52 / 70 / 49 / 94 / 26 / ~0%. The worst
  week was mid-May, not the April launch window.
- **Current state:** trailing five sessions ~4.6%. Gemini is currently healthy;
  there is no active Phase B threat as of this writing. The current rate is to be
  re-confirmed before the OSF deposit.
- **Failure mode:** ~73% server-side deadline/timeout, ~22% rate-limiting. The
  driver is open-bell congestion — all six models firing at 09:31 ET.
- **Tick-position gradient:** a clean monotonic gradient by within-session tick
  position — ~76% failure on the first tick of the session, declining to near-zero
  by the close.
- **Weekday skew:** a mild Monday–Wednesday-heavy skew.
- **Market-state independence:** failure is uncorrelated with market volatility
  (r = −0.12).

**Analytic characterization.** The missingness is **predominantly missing at random
conditional on within-session tick-position** (MAR | tick-position): it is driven by
open-bell server congestion, has a fully observed structure (the tick-position
gradient), and is uncorrelated with market state. It is **not** missing-not-at-random
with respect to market conditions. Because the selection structure is observed and
conditionable, conditioning on tick-position removes the dominant bias; this licenses
the per-RQ handling in Part 2.

**Residual limitation (disclosed).** The dominant deadline/timeout failure mode could
in principle correlate with the length of Gemini's would-be reasoning trace — a
longer trace is likelier to hit the server deadline — a secondary MNAR channel that
conditioning on tick-position does not remove. The volatility-independence result
does not exclude it. This residual is disclosed as a limitation. It can be bounded by
comparing the decision content of near-deadline-latency successful calls against
fast successful calls.

## Part 2 — Per-RQ handling of the tick-position selection bias

**Common mechanism.** Within-session tick-position is registered as a cohort-wide
analysis covariate / stratification dimension. Each RQ's handling below is the
appropriate form of conditioning on tick-position. The bias affects every RQ whose
quantity of interest can vary intraday; it is handled for all six, not only the
three originally flagged.

### RQ1 — cross-model herding

Tick-position is a registered stratification dimension for RQ1. Cross-model herding
is reported (i) full-panel as the headline and (ii) stratified by tick-position
bucket. Gemini-involving pair concordance is interpreted and compared **within**
tick-position strata, so the comparison against full-day pairs is like-for-like. For
the open-bell stratum, where Gemini's coverage falls below a pre-registered minimum,
Gemini-pair concordance is reported as **under-covered / not estimated** rather than
computed on a thin sample. A sensitivity analysis restricting the herding panel to
non-open ticks for all models is registered.

### RQ2 — disposition effect / RQ3 — confidence calibration

Both are exposed: disposition realization and confidence behavior plausibly vary by
time of day, so Gemini's close-skewed sample biases its RQ2 and RQ3 estimates.
Handling: tick-position is included as a covariate; Gemini's RQ2 and RQ3 estimates
are reported as conditional on tick-position; a non-open-restricted sensitivity
analysis is registered.

### RQ4 — Fama-French factor exposure

Minimally exposed: factor exposure is a slow-moving portfolio-holdings property,
near-identical at a given day's open and close. No primary tick-position covariate is
required. A coverage check is registered to confirm that Gemini's effective
factor-exposure sampling is not materially time-of-day-skewed.

### RQ5 — path-dependent risk behavior under drawdown

The headline regression gains **within-session tick-position fixed effects**:

```
dHHI_trade[m,t] = alpha_m + delta_p + beta * DD[m,t] + epsilon[m,t]
```

where `alpha_m` are model fixed effects and `delta_p` are tick-position fixed
effects. This absorbs systematic time-of-day level differences in trade-driven
concentration change and makes `beta` robust to the cohort's uneven tick-position
composition. `beta` remains a single coefficient — RQ5 still emits exactly one
headline p-value and its FDR-family membership is unaffected. Note: drawdown depth is
a slow cumulative state variable (decline from running peak), near-constant within a
session, so Gemini's close-skew does not restrict the *range* of `DD` it observes;
the handling targets the behavioral-response (`dHHI_trade`) side. A non-open-
restricted sensitivity analysis is registered.

### RQ6 — operational reproducibility

Two handling rules:

1. The RQ6 frozen-context sample is **tick-position-stratified with identical
   tick-position composition across all six models**, so the cross-model divergence
   comparison is like-for-like on the time-of-day axis.
2. The RQ6 measurement batch is **run off-peak** — not at the 09:31 ET open — so the
   `K` repeated calls per context do not reproduce the live open-bell congestion.
   RQ6 measures decision divergence (`Delta_m`) among successful calls; Gemini's
   open-bell availability is a separate quantity, disclosed in Part 1, and is **not**
   folded into `Delta_m`. If calls fail even off-peak, `Delta_m` for that context is
   computed on the `K' <= K` successful calls and `K'` is reported.

## Spec amendments

- **`RQ5-RQ6-specification.md` / `v1.json` — RQ5 `headline_test`:** add
  within-session tick-position fixed effects (`delta_p`) to the model
  (`dHHI_trade = alpha_m + delta_p + beta*DD + epsilon`). Single headline p-value on
  `beta` unchanged.
- **`RQ5-RQ6-specification.md` / `v1.json` — RQ6 operationalization:** require the
  frozen-context sample to be tick-position-stratified with identical composition
  across models, and the measurement batch to run off-peak; record `K'` per context.
- If Task 2 (landing the RQ5/RQ6 spec) has not yet completed in Operations, fold
  these amendments into it; if it has, land them as a follow-up amendment.

## September professor pre-read

This characterization is a substantive item for the September data-integrity
section. It is presented as a **characterized, mechanism-identified, conditioned-for,
and disclosed** limitation — the open-bell selection structure is exactly what a
referee probes, and a fully diagnosed selection bias with a registered handling reads
as rigor. The RQ5 tick-position-FE amendment folds into the existing RQ5(b) pre-read
item; it is not a new methodology-lock item.
