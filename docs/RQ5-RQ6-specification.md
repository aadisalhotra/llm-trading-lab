# LLM Trading Lab — RQ5 / RQ6 Specification for Commit

Drafted: May 22, 2026
Purpose: Resolve the two items blocking the monthly-report RQ schema and the OSF deposit. Once ratified, this is the authoritative spec for landing changes in data/pre_registration/v1.json, research_metrics.py, METHODOLOGY.md, and PRE_REGISTRATION.md (an Operations / Claude Code task).
Status: Pre-deposit. The OSF deposit has not occurred; specifying RQ6 and correcting RQ5 now is legitimate pre-registration drafting, not post-hoc change.
Discipline guard: The specifications below are principled, not fitted to Phase A pilot results. Functional forms, block length L, and the sample-size parameters K and M are set by pre-specified rule and must not be chosen to optimize a pilot p-value.
Consistency: RQ6 below consolidates and is consistent with the earlier METHODOLOGY section "API Non-Determinism (RQ6) — Deployed-Configuration Basis" deployed-configuration rewrite and the earlier RQ6 pre-registration drop-in (including its per-model API-configuration table, which remains part of RQ6's operationalization, reproduced inline below).

---

## Decision 1 — RQ6: reframe RATIFIED

Ratification. The reframe from "API non-determinism at temperature 0" to "operational reproducibility of the deployed agents at the deployed configuration" is ratified. Temperature 0 is a no-op across most or all of the cohort (DeepSeek V4 thinking mode silently ignores temperature; OpenAI and xAI reasoning models constrain or ignore it; Anthropic constrains temperature when extended thinking is enabled). "Non-determinism at temperature 0" therefore measures a configuration the experiment never trades at. The reframe measures the run-to-run reproducibility that actually propagates into RQ1–RQ5.

### RQ6 specification

Type. Characterization research question — not a null-hypothesis significance test. in_fdr_family: false. RQ6 produces no headline p-value and does not enter the Benjamini-Hochberg family. The family remains the five RQs RQ1–RQ5.

Research question. Holding model snapshot, prompt version, and input fixed, how much do a model's trading decisions vary across independent API calls at the model's deployed configuration?

Estimand. Per-model run-to-run decision divergence Delta_m at the deployed configuration.

Operationalization.

- Held-out context set. A pre-registered, regime-stratified sample of M frozen decision-period input bundles. A "decision context" is the complete frozen input for one decision period: prompt (versioned), market-state snapshot, portfolio state, recent-decision memory, macro headline, pre-market context. The sampling rule and the resulting M contexts are pinned in v1.json before the RQ6 measurement is run. Stratify across market regimes and drawdown states. The frozen-context sample is tick-position-stratified with identical tick-position composition across all six models, and the measurement batch is run off-peak (not at the 09:31 ET open) so the K repeated calls do not reproduce live open-bell congestion.
- Deployed configuration. Each model is called at the exact configuration used in live trading — pinned snapshot ID, thinking/reasoning mode and reasoning_effort per METHODOLOGY section "Reasoning Configuration and Cross-Model Equivalence", and the deployed temperature value (for most models the native/ignored value), recorded per the Per-Model API Configuration table reproduced below. Configuration is reported as a per-model variable.
- Repeated calls. For each context, issue K independent API calls in a single time-clustered batch. Time-clustering isolates run-to-run divergence from longitudinal infrastructure drift. If calls fail even off-peak, Delta_m for that context is computed on the K' <= K successful calls and K' is reported.
- Parameters. K and M are pinned in v1.json before the OSF deposit. Recommended minima: K >= 10 (>= 45 call-pairs per context), M >= 30 regime-stratified contexts. These are sample-size choices, set independently of pilot results.

Per-Model API Configuration table (reproduced inline so RQ6 is self-contained):

| Model | Provider | Pinned snapshot ID | Thinking/reasoning mode | reasoning_effort (provider-standard) | Temperature behavior | Temperature value used |
|---|---|---|---|---|---|---|
| Claude Sonnet 4.6 | Anthropic | `claude-sonnet-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Claude Opus 4.6 | Anthropic | `claude-opus-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| GPT-5.4 | OpenAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Gemini 3.1 Pro | Google | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Grok 4.20 Reasoning | xAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| DeepSeek v4-pro | DeepSeek | `[PIN]` | Enabled | `high` | Silently ignored (inoperative in thinking mode) | n/a (inoperative) |

Metrics.

- Primary — decision divergence. For context c, call i yields a decision set D[c,i] = the set of (ticker, action) pairs in that call's trades list (action in {buy, sell}; size and confidence excluded from the set). Pairwise Jaccard similarity J(A,B) = |A intersect B| / |A union B|, with J = 1 if A and B are both empty. Context divergence delta[c,m] = 1 - mean(J) over all C(K,2) call-pairs. Model divergence Delta_m = mean(delta[c,m]) over the M contexts.
- Secondary — magnitude dispersion (descriptive). Among (ticker, action) decisions present in all K calls of a context: the standard deviation of assigned confidence (1–10) and of size, aggregated per model.

Inference. Delta_m is reported per model with a BCa bootstrap confidence interval, resampling contexts, B = 10000 replicates. No hypothesis is tested.

Decision / interpretation rule. RQ6 reports Delta_m with its CI for each of the six models, alongside each model's deployed configuration. Cross-model comparison is interpreted as a comparison of deployed agents, not of intrinsic model stochasticity. Delta_m is reported alongside RQ1–RQ5 and qualifies their interpretation: a model with high Delta_m has its RQ1–RQ5 estimates flagged as carrying substantial run-to-run measurement noise.

Run-to-run vs. longitudinal. RQ6 measures run-to-run divergence (K calls clustered in time). Longitudinal behavioral drift from serving-infrastructure changes under a fixed snapshot ID is a distinct phenomenon, recorded as a general limitation, not an RQ6 measure (cross-ref METHODOLOGY section "Pinned-Snapshot Guarantee — Scope and Caveat").

Recommended v1.json RQ6 entry (reconcile field names with the existing schema)

```json
{
  "id": "RQ6",
  "title": "Operational reproducibility of deployed agents",
  "type": "characterization",
  "research_question": "Holding model snapshot, prompt version, and input fixed, how much do a model's trading decisions vary across independent API calls at the model's deployed configuration?",
  "estimand": "Per-model run-to-run decision divergence Delta_m at the deployed configuration.",
  "operationalization": "K independent API calls per held-out decision context, at each model's deployed configuration; M regime-stratified frozen contexts; K calls time-clustered per context.",
  "metric_primary": "Delta_m = mean over M contexts of (1 - mean pairwise Jaccard similarity of (ticker, action) decision sets across the K calls).",
  "metric_secondary": "Per-model SD of confidence and of size among decisions common to all K calls (descriptive).",
  "inference": "Per-model Delta_m with BCa bootstrap CI, resampling contexts, B = 10000.",
  "decision_rule": "Report Delta_m and CI per model with deployed configuration; interpret as comparison of deployed agents; use to qualify RQ1-RQ5 reproducibility. No NHST.",
  "in_fdr_family": false,
  "parameters": { "K": null, "M": null, "B": 10000 },
  "notes": "K, M pinned before OSF deposit; recommended K >= 10, M >= 30."
}
```

---

## Decision 2 — RQ5

### 2(a) num_positions — DROP from research_metrics.py

Decision. num_positions is not a pre-registered RQ5 metric. Remove it from research_metrics.py. RQ5's behavioral metric set is the four registered metrics: HHI, turnover, average position size, cash.

Rationale. (1) v1.json and PRE_REGISTRATION.md are the authoritative locked spec; code conforms to the registration. (2) num_positions is near-collinear with the registered metrics — for a given invested fraction it is close to invested_value / avg_position_size, hence approximately an algebraic function of average position size and cash. Including a near-deterministic function of other family metrics adds multiple-comparison burden for no independent construct. (3) The four registered metrics already span concentration (HHI), trading intensity (turnover), sizing (average position size), and deployment (cash). Pre-deposit, either direction is permissible; the research merits favor dropping.

### 2(b) RQ5 headline pooled test — SPECIFIED

Problem. v1.json sets RQ5 in_fdr_family: true, but compute_rq5 produces only per-model bootstrap CIs and no pooled headline p-value, so RQ5 cannot enter the Benjamini-Hochberg family — the executable family is only {RQ1–RQ4}. RQ5 must produce exactly one headline p-value.

RQ5 has two layers.

- Descriptive layer. Per-model characterization on the four metrics with BCa CIs (the existing compute_rq5 output, minus num_positions). Descriptive; does not enter the FDR family.
- Inferential layer (NEW). One headline pooled test producing the single p-value RQ5 contributes to the BH family.

Headline test — drawdown-conditioned concentration response.
Dependent variable — trade-driven change in concentration. RQ5 concerns behavior. A test on the realized HHI level regressed on drawdown would conflate behavior with mechanics: a market decline simultaneously deepens drawdown and passively reshapes portfolio weights, so a realized-HHI-on-drawdown coefficient would partly reflect an accounting identity rather than a decision. The headline dependent variable is therefore the trade-driven change in HHI.
Definitions, per model m and decision period t:

- HHI is computed on risky-position weights normalized to sum to 1 — concentration within the equity book, decoupled from the cash level (which is its own RQ5 metric). HHI(w) = sum_i w_i^2.
- w_pre[m,t]: normalized risky-position weights after mark-to-market price drift into period t, before period-t trades.
- w_post[m,t]: normalized risky-position weights after period-t trades.
- dHHI_trade[m,t] = HHI(w_post[m,t]) - HHI(w_pre[m,t]) — the concentration change the model's own trades produced.
- DD[m,t]: drawdown depth = fractional decline of model m's total portfolio value (including cash) from its running peak as of period t; DD >= 0, DD = 0 at a new peak.

Model. Pooled panel regression with model fixed effects:
dHHI_trade[m,t] = alpha_m + beta * DD[m,t] + epsilon[m,t]
alpha_m are model fixed effects, so the test is within-model — how a model's trade-driven concentration responds to its own drawdown state — pooled across the six-model cohort.

Estimand / null. beta. H0: beta = 0 (trade-driven concentration change does not respond to drawdown depth, pooled across the cohort). Two-sided. beta > 0: models actively concentrate more when deeper in drawdown; beta < 0: models actively diversify under drawdown.

Inference. Moving-block bootstrap over decision periods within model, preserving the panel and serial-correlation structure. Block length L is pinned in v1.json before the deposit by a pre-specified rule (e.g. L = round(n^(1/3)) per model, or L from the estimated decorrelation time of dHHI_trade — not chosen to optimize the pilot p-value). B = 10000 replicates. The headline RQ5 p-value is the two-sided percentile-bootstrap p-value for beta: p = 2 * min(share of beta_star <= 0, share of beta_star >= 0). A BCa CI for beta is reported alongside for effect size.

FDR. This single p-value is RQ5's entry in the Benjamini-Hochberg family. The family is genuinely {RQ1, RQ2, RQ3, RQ4, RQ5} — five p-values, one per RQ. RQ6 is excluded (characterization, no p-value). BH correction is applied across the five headline p-values at the pre-registered q-level set in v1.json; RQ5's null is rejected if its BH-adjusted p-value is below q.

Scope note. The four per-model descriptive metrics (including realized HHI level) remain descriptive RQ5 output and do not enter the FDR family. Headline tests on turnover, average position size, or cash are not registered; if wanted, that is a within-RQ5 multiple-testing question for the September professor review. The current fix registers exactly the one HHI-based headline test the existing registration implies.

Recommended v1.json RQ5 entry (reconcile field names with the existing schema)

```json
{
  "id": "RQ5",
  "title": "Path-dependent risk behavior under drawdown",
  "behavioral_metrics": ["hhi", "turnover", "avg_position_size", "cash"],
  "descriptive_layer": "Per-model characterization on the four behavioral metrics with BCa bootstrap CIs. Not in the FDR family.",
  "headline_test": {
    "name": "drawdown_conditioned_concentration_response",
    "dependent_variable": "trade_driven_delta_hhi",
    "dependent_variable_definition": "HHI(post-trade normalized risky weights) - HHI(pre-trade price-drifted normalized risky weights), per model per decision period.",
    "independent_variable": "drawdown_depth",
    "independent_variable_definition": "Fractional decline of total portfolio value from running peak; >= 0.",
    "model": "Pooled panel OLS with model fixed effects: dHHI_trade = alpha_m + beta*DD + epsilon.",
    "null_hypothesis": "beta = 0",
    "alternative": "two-sided",
    "inference": "Moving-block bootstrap within model; block length L; B = 10000 replicates.",
    "headline_p_value": "Two-sided percentile-bootstrap p-value on beta.",
    "ci": "BCa CI for beta.",
    "parameters": { "L": null, "B": 10000 }
  },
  "in_fdr_family": true,
  "notes": "Block length L pinned before OSF deposit by a pre-specified rule. The headline test supplies RQ5's single p-value to the BH family {RQ1-RQ5}."
}
```

---

## Landing checklist (Operations / Claude Code)

- v1.json — Replace the RQ6 entry with the reframed spec (in_fdr_family: false). Update the RQ5 entry: confirm the four-metric set, add the headline_test block, keep in_fdr_family: true now that RQ5 produces a p-value. Pin K, M, L, B, and the BH q-level.
- research_metrics.py — compute_rq6: implement the decision-divergence metric on frozen contexts. compute_rq5: remove num_positions; add the pooled dHHI_trade ~ DD model-fixed-effects block-bootstrap test that emits one headline p-value. Confirm the FDR driver now receives five p-values (RQ1–RQ5).
- METHODOLOGY.md — the section "API Non-Determinism (RQ6) — Deployed-Configuration Basis" is already drafted to the deployed-configuration reframe; confirm it matches this spec and land it. Add the RQ5 trade-driven dHHI definition if METHODOLOGY carries RQ-level specs.
- PRE_REGISTRATION.md — Land the RQ6 reframe; update the RQ5 section to the four metrics plus the headline pooled test.
- Verification — Re-run the read-only cross-check; all four files must agree after landing.

## September professor pre-read

- RQ6 reframe — already on the agenda (one of the four methodology-lock items).
- RQ5(b) headline pooled test — recommended addition to the pre-read. Defining a test that enters the FDR family — including the mechanical-vs-behavioral dependent variable choice — is a methodology commitment a referee will probe; it warrants ratification, not just implementation. This makes the September methodology-lock list five items.
- RQ5(a) — bookkeeping; no ratification needed.
