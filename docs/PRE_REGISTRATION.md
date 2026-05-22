# Pre-Registration — Autonomous LLM Trading Lab

**Registration date:** 2026-05-19
**Pre-registration version:** v1
**Status:** 🔒 LOCKED
**Author:** Aadi Salhotra
**Planned public deposit:** OSF, before the live phase begins on **2026-11-01** (so the deposit predates any inferential data)
**Machine-readable copy:** [`data/pre_registration/v1.json`](../data/pre_registration/v1.json)

---

## 0. Why this document exists

This file fixes the research design **before** the inferential data exists. A pre-registration written after seeing results is worthless — it lets you fit the hypothesis to the noise. So everything that could be tuned to manufacture a finding is locked here, with a datestamp and a git history: the six research questions, their hypotheses, the exact metric each is operationalized as, the numeric prediction, the decision rule that will resolve it, the data window required, and the statistical corrections applied to the whole family.

Three commitments make this binding:

1. **This document is append-only after the deposit date.** Any change after the OSF deposit is recorded in the Amendment Log (§5) with a date and rationale — never a silent edit.
2. **The code is subordinate to this document.** `src/analytics/research_metrics.py` is the executable form of the definitions below. If the two ever disagree, this document is correct and the code is the bug.
3. **Inference uses live-phase data only.** The paper-trading phase is a *pilot*: it validates the pipeline, fixes the definitions, and gives power estimates. No paper-phase number is reported as a confirmatory result.

---

## 1. Experiment overview

Six frontier LLMs each manage an independent portfolio under identical conditions — same 79-asset universe, same market data, same news, same risk rules, same execution venue, randomized ticker order shared across all models within a tick. Decisions are made every 30 minutes during NYSE hours via a two-step screen→trade pipeline. No human intervenes.

| | |
|---|---|
| **Models** | Claude Sonnet 4.6, GPT-5.4, Gemini 3.1 Pro, Grok 4.20 (Reasoning), DeepSeek Reasoner, Claude Opus 4.6 |
| **Benchmark** | SPY buy-and-hold |
| **Universe** | 79 assets (75 US large/mega-cap equities, full GICS coverage, + 4 commodity ETFs) |
| **Decision cadence** | Every 30 minutes, 09:30–16:00 ET |
| **Identical-input guarantee** | All models share one `data_inputs_hash` per tick (verified) |

### Phases

| Phase | Window | Capital | Role in this study |
|-------|--------|---------|--------------------|
| **A — Paper** | 2026-04-09 → 2026-10-31 | $100K simulated | **Pilot.** Pipeline validation, definition-locking, power analysis. *Not* confirmatory. |
| **B — Live** | 2026-11-01 → 2027-11-01 | $1K real | **Confirmatory.** All inferential tests below run on this window. |

The single biggest threat to this study is that the live window covers only **one** market regime. That limitation, and the planned backtest harness that addresses it for RQ1/RQ4/RQ5, is documented in [`BACKTEST_HARNESS_SCOPE.md`](BACKTEST_HARNESS_SCOPE.md).

---

## 2. Research questions

Each RQ is stated with a null/alternative hypothesis, the operational definition, the precise metric, a numeric pre-registered prediction, the decision rule that resolves it, the data window required, and a status flag (**Open** = not enough data yet · **Testing** = accumulating data, no decision · **Resolved** = decision rule met).

---

### RQ1 — Decision convergence under identical information sets · **PRIMARY**

> **Do frontier LLMs converge on identical trading decisions under identical information sets?**

- **Status:** Testing (pilot data accumulating)
- **Hypothesis.**
  - H₀: Given identical inputs, pairwise decision agreement across models equals the chance level implied by each model's own action base rates.
  - H₁: Pairwise decision agreement exceeds chance — models converge.
- **Operationalization.** Records are joined on `data_inputs_hash` (identical across all models within a tick, verified). For each model we read `raw_decisions` (its own pre-risk-filter output, so we measure model cognition, not the executor). For every model pair on every tick, over the tickers **both** ruled on (≥ 3 shared):
  - *Binarized signal:* 3-way BUY/SELL/HOLD **action concordance** = fraction of shared tickers with identical action.
  - *Continuous signal:* **weight correlation** = Pearson r of the two models' `target_weight` vectors over the shared tickers.
- **Metric definition.** Headline metric = mean pairwise action concordance across all (pair, tick) observations. Chance level = the **shuffled-permutation null**: within each tick, each model's action vector is independently permuted across its tickers (preserving that model's BUY/SELL/HOLD marginal rates, destroying cross-model alignment); the grand-mean concordance is recomputed; repeated to build the null distribution. Excess = observed − null mean.
- **Pre-registered prediction.** Observed action concordance exceeds the shuffled-null mean by **≥ 0.10** (e.g. ~0.60 observed vs ~0.50 chance), with a one-sided permutation *p* < 0.05 surviving FDR control. Convergence is **strongest in trending regimes** and weakest in vol-spike regimes.
- **Decision rule.** **Resolved-converge** if, on the live window, the permutation *p*-value < 0.05 after Benjamini-Hochberg correction **and** the BCa 90% CI for excess concordance excludes 0. **Resolved-null** if the CI includes 0. The convergence ceiling is interpreted against the RQ6 run-to-run reproducibility floor: convergence cannot exceed (1 − within-model run-to-run divergence).
- **Data window required.** ≥ 200 pairwise (pair, tick) observations with ≥ 3 shared tickers, on live data. Stratified estimates require ≥ 50 observations per regime.

---

### RQ2 — Disposition effect in sequential trading

> **Do frontier LLMs exhibit a disposition effect — realizing gains faster than losses?**

- **Status:** Testing
- **Hypothesis.**
  - H₀: The proportion of gains realized equals the proportion of losses realized (PGR − PLR = 0).
  - H₁: PGR − PLR ≠ 0 (disposition effect if > 0; reverse / loss-cutting if < 0).
- **Operationalization.** Odean (1998). Replaying each model's executed trades with average-cost accounting, on every decision record containing ≥ 1 executed SELL:
  - Each sell is a **realized gain** (fill > avg cost) or **realized loss** (fill < avg cost).
  - **Paper gains/losses** are the still-held positions (not sold in that record), classified by their `unrealized_pl_pct` from that record's `portfolio_after` snapshot.
- **Metric definition.**
  - PGR = realized gains / (realized gains + paper gains)
  - PLR = realized losses / (realized losses + paper losses)
  - **Disposition difference** = PGR − PLR (headline) · **Disposition ratio** = PGR / PLR.
  - This is the *transaction-level* construction; partial trims count. (Contrast RQ3, which requires full exit.)
- **Pre-registered prediction.** Models exhibit a **weaker** disposition effect than retail humans (whose PGR − PLR ≈ +0.05 to +0.10 in Odean). Direction is genuinely uncertain; the pilot suggests these models may show a **reverse** disposition (cutting losers faster), so the test is two-sided.
- **Decision rule.** Per model and pooled: **Resolved** when the BCa 90% CI for PGR − PLR excludes 0 (live data); sign of the point estimate gives the direction. Pooled test enters the FDR family.
- **Data window required.** ≥ 30 sale records per model (≥ 100 pooled) on live data.

---

### RQ3 — Confidence calibration on closed trades

> **Are LLMs' self-reported confidence scores calibrated to trade outcomes?**

- **Status:** Testing
- **Hypothesis.**
  - H₀: P(profitable | confidence = k) is flat in k (confidence carries no information about outcomes); ECE is no better than a non-informative baseline.
  - H₁: P(profitable | confidence = k) increases in k (confidence is informative).
- **Operationalization.** Computed on **closed trades only**. *A position counts as closed only on full exit* — when a SELL takes residual shares below 0.01. **Partial trims do not close a position.** A position re-opened after a full exit is an independent closed trade. Outcome = realized P&L > 0 over the full life of the position. Entry confidence = **share-weighted average** of the confidence scores on the buys that built the position, rounded to the nearest integer 1–10.
- **Metric definition.**
  - **Calibration curve:** P(profitable | confidence = k) for k = 1…10.
  - **Brier score:** mean((p̂ − outcome)²) where the predicted probability p̂ = confidence / 10.
  - **Expected Calibration Error (ECE):** Σₖ (nₖ/N) · |accₖ − k/10|, bins = integer confidence levels.
  - Headline test statistic: point-biserial correlation between entry confidence and the binary profitable outcome.
- **Pre-registered prediction.** Confidence is **directionally informative but overconfident**: confidence/outcome correlation > 0 but small (≈ +0.05 to +0.20), and ECE materially above 0 (poor absolute calibration — high stated confidence overpredicts win probability).
- **Decision rule.** Per model with **≥ 20 closed trades** (pre-registered minimum): **Resolved-calibrated** if the BCa 90% CI for the confidence/outcome correlation excludes 0 *and* is positive (live data); report ECE and Brier alongside. Models below 20 closed trades stay **Open**. Pooled correlation enters the FDR family.
- **Data window required.** ≥ 20 closed (full-exit) trades per model on live data.

---

### RQ4 — Systematic style-factor tilts

> **Do LLM portfolios exhibit systematic style-factor tilts?**

- **Status:** Open (factor data does not yet overlap the live window)
- **Hypothesis.**
  - H₀: A model's daily excess returns load zero on each style factor (it is a pure-alpha / factor-neutral allocator).
  - H₁: One or more factor loadings are non-zero — the model has a persistent size / value / profitability / investment / momentum tilt.
- **Operationalization.** OLS of each model's daily **excess** return (portfolio return − RF) on the **Fama-French 5 factors (Mkt-RF, SMB, HML, RMW, CMA) plus Momentum (MOM)**, with an intercept (alpha). Factors from the Ken French Data Library (daily). EOD portfolio values deduplicated to one per trading day.
- **Metric definition.** Per model: annualized alpha and its *t*-stat; the six factor betas with *t*-stats; R². (*p*-values use a normal approximation — no scipy in the stack — and are flagged approximate at small *n*.)
- **Pre-registered prediction.** Models tilt toward **large-cap growth/quality** (negative SMB, negative HML, positive RMW) and carry a **positive momentum** loading, reflecting their training-data priors about "good companies." Alphas are statistically indistinguishable from zero net of factors.
- **Decision rule.** Per factor per model: **Resolved-tilt** if the BCa 90% CI for the beta excludes 0 over the live window. Alpha ≠ 0 (smallest-*p* across models) enters the FDR family. Loadings are reported per regime where data allow.
- **Data window required.** ≥ 60 aligned trading days per model on live data; cross-regime estimates require the backtest harness (see §3, [`BACKTEST_HARNESS_SCOPE.md`](BACKTEST_HARNESS_SCOPE.md)).

---

### RQ5 — Path-dependent risk behavior under drawdown

> **How do LLMs' trading decisions respond to their own portfolio drawdowns?**

- **Status:** Testing (pilot data accumulating; confirmatory on the live window)
- **Two layers.** A **descriptive** layer (four behavioral metrics, *not* in the FDR family) and one **inferential headline test** (the single RQ5 p-value in the FDR family).
- **Hypothesis (headline).**
  - H₀: β = 0 — trade-driven change in within-equity concentration does not respond to drawdown depth, pooled across the cohort.
  - H₁: β ≠ 0 (two-sided) — models actively concentrate (β > 0) or diversify (β < 0) more when deeper in drawdown.
- **Descriptive layer (not in the FDR family).** Per model, the four registered behavioral metrics — (1) **HHI concentration** = Σ wᵢ²; (2) **turnover** = Σ |executed notional| / EOD value; (3) **average position size** = mean holding weight; (4) **cash allocation** = cash / total value — compared drawdown-vs-normal day, with BCa 90% CIs. `num_positions` is **not** a registered RQ5 metric.
- **Headline test — drawdown-conditioned concentration response.**
  - *Dependent variable — trade-driven change in concentration.* A test on the realized HHI *level* would conflate behavior with mechanics (a market decline simultaneously deepens drawdown and passively reshapes weights). The dependent variable is the **trade-driven** change: dHHI_trade[m,t] = HHI(w_post) − HHI(w_pre), where HHI(w) = Σ wᵢ² on **risky-position weights normalized to sum to 1** (cash excluded); w_post = post-trade weights; w_pre = the prior period's post-trade holdings re-priced at period-t prices (mark-to-market drift, before period-t trades). **Pre-trade weights are not directly logged; they are reconstructed** — RQ5's dependent variable is a derived quantity (see §3.9).
  - *Independent variable.* DD[m,t] = drawdown depth = fractional decline of total portfolio value (incl. cash) from its running peak; DD ≥ 0, 0 at a new peak.
  - *Model.* Pooled panel OLS with **model fixed effects and within-session tick-position fixed effects**: dHHI_trade = αₘ + δₚ + β·DD + ε. (The δₚ tick-position fixed effects absorb the open-bell time-of-day structure — see §3.9 Gemini handling.)
  - *Inference.* Moving-block bootstrap over decision periods within model, **block length L = round(n^(1/3))** (n = decision periods in the analysis window; resolved integer logged in `v1.json`), B = 10,000. Headline p = the two-sided percentile-bootstrap p on β: 2·min(share β\* ≤ 0, share β\* ≥ 0). A BCa CI for β is reported alongside.
  - *Sensitivity (registered, not in the FDR family).* Re-run at block lengths **floor(L/2)** and **2L**; β is block-length-robust if its sign and BH-adjusted significance are stable across {floor(L/2), L, 2L}.
- **Pre-registered prediction.** Descriptive: in drawdown, models become more conservative (cash up, HHI down, average position size down; turnover rises as positions are cut). Headline: two-sided on β.
- **Decision rule.** The single headline p-value enters the BH family; RQ5's null is rejected if its BH-adjusted p < q (live data). The four descriptive metrics are reported per model with BCa 90% CIs and do not enter the family.
- **Phase A pilot window.** All RQ5 Phase A pilot analyses use a **uniform series start across all six models** = the later of the designated shakedown-period end and the launch-window commingling corruption-end, resolved to **2026-04-23** (see §3.9). Phase A is exploratory.
- **Data window required.** Confirmatory: the full Phase B live window.

---

### RQ6 — Operational reproducibility of deployed agents · **CHARACTERIZATION**

> **Holding model snapshot, prompt version, and input fixed, how much do a model's trading decisions vary across independent API calls at the model's deployed configuration?**

- **Status:** Open (frozen-context probe not yet run)
- **Type.** Characterization research question — **not** a null-hypothesis test, and **not** a member of the BH FDR family; it produces no headline p-value. (This reframe replaces the original "non-determinism at temperature 0": temperature 0 is a no-op across the reasoning cohort — see METHODOLOGY § "API Non-Determinism (RQ6) — Deployed-Configuration Basis".)
- **Estimand.** Per-model run-to-run decision divergence Delta_m at the deployed configuration.
- **Operationalization.** A pre-registered, regime-stratified sample of **M** frozen decision-period input bundles. For each context, **K** independent API calls in one **time-clustered batch** at the model's deployed configuration (Per-Model API Configuration table below). The frozen-context sample is **tick-position-stratified with identical composition across all six models**, and the measurement batch is **run off-peak** (not the 09:31 ET open). If calls fail off-peak, Delta_m for that context uses the **K′ ≤ K** successful calls and K′ is reported.
- **Metric.** Per context c: decision set D = {(ticker, action)} (action ∈ {BUY, SELL}). delta_c = 1 − mean pairwise Jaccard over the C(K′,2) call-pairs (J = 1 if both sets empty). **Delta_m = mean(delta_c)** over the M contexts. Secondary (descriptive): SD of confidence and of size among decisions common to all K calls.
- **Inference.** Per-model Delta_m with a **BCa bootstrap CI resampling contexts, B = 10,000**. No hypothesis is tested.
- **Parameters.** K and M pinned in `v1.json` before the OSF deposit; recommended K ≥ 10, M ≥ 30.
- **Decision / interpretation rule.** Report Delta_m and its CI per model alongside the model's deployed configuration; interpret as a comparison of **deployed agents**, not intrinsic model stochasticity; use Delta_m to qualify the RQ1–RQ5 interpretation (a high-Delta_m model carries substantial run-to-run measurement noise).

**Per-Model API Configuration** (reproduced inline so this entry is self-contained):

| Model | Provider | Pinned snapshot ID | Thinking/reasoning mode | reasoning_effort (provider-standard) | Temperature behavior | Temperature value used |
|---|---|---|---|---|---|---|
| Claude Sonnet 4.6 | Anthropic | `claude-sonnet-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Claude Opus 4.6 | Anthropic | `claude-opus-4-6` (dateless canonical snapshot) | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| GPT-5.4 | OpenAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Gemini 3.1 Pro | Google | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| Grok 4.20 Reasoning | xAI | `[PIN]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` | `[VERIFY]` |
| DeepSeek v4-pro | DeepSeek | `[PIN]` | Enabled | `high` | Silently ignored (inoperative in thinking mode) | n/a (inoperative) |

`[PIN]` = dated immutable snapshot ID resolved before the deposit (Claude rows use the dateless 4.6-generation canonical snapshot, no `[PIN]` needed). `[VERIFY]` = filled by the per-model API-configuration verification (a hard pre-deposit dependency of RQ6); DeepSeek's row is verified.

---

## 3. Cross-cutting methodological commitments

These apply to the whole RQ family and are locked.

### 3.1 Regime stratification

Every inferential estimate is reported **overall and stratified by market regime**, so a finding reads as conditional on market state rather than averaged over a single tape. Regimes are assigned **post hoc** from SPY daily prices by these quantitative criteria (`src/analytics/regime_classifier.py`):

| Regime | Criterion |
|--------|-----------|
| **Bull-trending** | SPY 20-day return > **+3%** AND 20-day annualized volatility < **15%** |
| **Range-bound** | SPY 20-day return in **[−2%, +2%]** AND 20-day annualized vol in **[10%, 20%]** |
| **Vol-spike** | SPY 5-day realized annualized volatility > **25%** |
| **Drawdown** | SPY > **5%** below its trailing **60-day** peak (EOD close) |

A day can satisfy more than one rule. To produce mutually-exclusive strata we apply a fixed **precedence: vol-spike > drawdown > bull-trending > range-bound > neutral.** The acute states dominate because they are what the robustness checks care about. The raw per-rule booleans are also retained so anyone can re-stratify under a different precedence without re-running. Volatility is annualized as daily-std × √252.

### 3.2 Multiple-comparison control — Benjamini-Hochberg FDR at q = 0.10

The study runs one headline frequentist test per research question (RQ1, RQ2, RQ3, RQ4, RQ5; RQ6 is descriptive). These five form a **family** controlled by the **Benjamini-Hochberg** procedure at **false-discovery rate q = 0.10**. A result is only called a "finding" if it survives BH correction across the family. Per-model sub-tests within an RQ are **exploratory** and reported with raw + BCa intervals, not folded into the primary family (to avoid diluting it).

### 3.3 Deflated Sharpe alongside raw Sharpe

Any Sharpe ratio reported for a model is accompanied by its **Deflated Sharpe Ratio** (Bailey & López de Prado, 2014), which discounts the observed Sharpe for (a) non-normal returns (skew, kurtosis), (b) sample length, and (c) the number of trials — here, the **6 models + SPY = 7** strategies selected from. A model "beats SPY on a risk-adjusted basis" only if its DSR is high, not merely its raw Sharpe. Probabilistic Sharpe is also available. (`src/analytics/statistical_corrections.py`.)

### 3.4 Block bootstrap — length 5, 10,000 resamples, BCa

All confidence intervals use the **circular moving-block bootstrap** with **block length 5** and **10,000 resamples**, and the **BCa** (bias-corrected and accelerated) interval. Blocks preserve the short-horizon autocorrelation in daily returns and decision series that an IID bootstrap would destroy; BCa corrects for the skew and median-bias that a naïve percentile interval ignores. Acceleration is estimated by a delete-one-block jackknife. Default interval is **90% (α = 0.10)**.

### 3.5 Live-phase data only for inferential analysis

The paper-trading phase (Apr–Oct 2026) is **pilot data**: it exists to validate the pipeline, lock these definitions, and estimate statistical power. **No paper-phase result is reported as confirmatory.** All decision rules above resolve on **live-phase** data only.

### 3.6 API non-determinism characterization (RQ6, deployed configuration)

Frontier model APIs are not run-to-run deterministic at the **deployed configuration**; RQ6 measures this as per-model decision divergence Delta_m. (Temperature 0 is a no-op across the reasoning cohort, so the original temperature-0 framing is withdrawn — see METHODOLOGY § "API Non-Determinism (RQ6) — Deployed-Configuration Basis".) The **measured** convergence in RQ1 therefore has a ceiling below 1.0; RQ1 is read relative to (1 − within-model run-to-run divergence), never against a naïve 100%.

### 3.7 Locked operational definitions

| Term | Locked definition |
|------|-------------------|
| **Closed trade** (RQ3) | A position is closed **only on full exit** (residual shares < 0.01). Partial trims never close a position. Re-entry after exit is an independent trade. |
| **Portfolio drawdown** (RQ5) | EOD portfolio value ≥ 10% below the trailing 60-day peak. Distinct from the SPY drawdown *regime* (5% off a 60-day peak). |
| **Confidence → probability** (RQ3) | Predicted win probability p̂ = stated confidence / 10. |
| **Decision unit** (RQ1, RQ6) | A model's `raw_decisions` entry per (tick, ticker): {action ∈ BUY/SELL/HOLD, target_weight ∈ [0,1], confidence ∈ 1–10}. |
| **Identical information set** (RQ1) | Decision records sharing one `data_inputs_hash` (verified identical across all models within a tick). |

### 3.8 Data provenance & immutability

Decisions, executions, and EOD snapshots are written to append-only JSONL logs (`data/trades/`, `data/performance/`) and committed to git on every tick, timestamping the full record and the inputs hash. The analysis reads only these logs. Raw model responses are retained. This pre-registration and the dataset are version-controlled; the OSF deposit freezes the v1 design hash.

### 3.9 Phase A data integrity (pilot-window scope)

Discloses the Phase A (pilot) data-quality issues and the registered handling. Pilot/exploratory scope only; does not touch Phase B confirmatory data.

**Phase A shakedown period — designated.** The Phase A shakedown period is **April 9 – April 22, 2026 inclusive**; the uniform Phase A pilot-analysis window opens with the first decision period of **April 23, 2026**. Basis, verified against the repository: the last shakedown-class stabilization commit is `cacd8058` (April 21, 2026 — the state-file routing fix below); April 22 – May 12 contains only intraday tick commits and no stabilization activity. Independently, the RQ5 data-availability diagnostic found the state-file commingling corruption cleared the day after `cacd8058`, with both Anthropic models' data cleanly reconstructable from ~April 23, 2026. (This supersedes a prior May 1 designation whose "engineering sprint" basis did not survive commit-history verification.)

**Launch-window state-file commingling (Claude Sonnet / Claude Opus).** During ~April 9–22, 2026 a state-file commingling defect caused Claude Opus to write to Claude Sonnet's state file, so the recorded holdings, cash, and total portfolio value for **both** Anthropic models in that window do not reflect each model's own independent trading. The defect was resolved (`cacd8058`); both reconstruct cleanly from ~April 23 onward. The corruption is **forward-unfixable** — no logging change recovers the affected window. It affects all per-model behavioral data for the two Anthropic models in the window (not RQ5 alone), falls entirely within the pilot phase, and does not touch Phase B.

**Uniform pilot-window rule.** All RQ Phase A pilot analyses use a **uniform series start across all six models** = the later of the designated shakedown-period end and the launch-window corruption-end (~April 23, 2026). A per-model ragged start would make a cross-model pooled estimate (e.g. RQ5's β) confound shakedown-window behavior unevenly across the panel; a uniform start removes the asymmetry and excludes the corrupted window for all models with no per-RQ special-casing. For RQ6 (frozen-context sampling, not a time series) the shakedown/corruption window is excluded from the eligible context pool.

**RQ5 dependent variable — derived quantity.** RQ5's headline dependent variable, trade-driven dHHI, requires pre-trade portfolio weights, which are **not directly logged for any model**. They are reconstructed from the prior decision period's post-trade holdings re-priced at the current period's prices, normalized to risky-position weights; the reconstruction has been validated as robust. A forward-only Phase B logging change to persist a direct pre-trade snapshot is recommended (it would make the Phase B dependent variable logged rather than reconstructed, removing the assumption that between-period holdings change only via price drift — which a 15% stop-loss trigger would violate); it is additive and does not affect Phase A pilot data.

**Gemini availability (Phase A).** Disclosed as a **time series with mechanism**, not a single percentage (a cumulative 49.5% is misleading — the series is episodic and the current rate is low). Weekly failure rate: ~28 / 52 / 70 / 49 / 94 / 26 / ~0% (worst week mid-May, not the April launch); trailing five sessions ~4.6%. Failure mode: ~73% server-side deadline/timeout, ~22% rate-limiting, driven by **open-bell congestion** (all six models firing at 09:31 ET); a clean monotonic **tick-position gradient** (~76% failure on the first tick of the session, declining to near-zero by the close); a mild Monday–Wednesday skew; **uncorrelated with market volatility** (r = −0.12). The missingness is **missing-at-random conditional on within-session tick-position** (MAR | tick-position); it is not MNAR with respect to market conditions. A residual MNAR channel — failures could correlate with would-be reasoning-trace length — is disclosed as a limitation, boundable by comparing near-deadline-latency vs fast successful calls. The current rate is to be re-confirmed before the OSF deposit.

**Per-RQ handling of the tick-position selection bias.** Within-session tick-position is a registered cohort-wide analysis covariate / stratification dimension; each RQ conditions on it appropriately:

- **RQ1.** Registered stratification dimension; cross-model herding reported full-panel (headline) and stratified by tick-position bucket. Gemini-pair concordance compared **within** strata; in the open-bell stratum, where Gemini coverage falls below a pre-registered minimum, it is reported **under-covered / not estimated**. A non-open-tick sensitivity analysis is registered.
- **RQ2 / RQ3.** Both exposed (disposition and confidence behavior vary by time of day). Tick-position included as a covariate; Gemini's estimates reported conditional on tick-position; a non-open-restricted sensitivity analysis is registered.
- **RQ4.** Minimally exposed (factor exposure is a slow-moving holdings property). No primary tick-position covariate; a coverage check confirms Gemini's factor-exposure sampling is not materially time-of-day-skewed.
- **RQ5.** The headline regression carries within-session tick-position fixed effects (δₚ), absorbing time-of-day level differences in trade-driven concentration change and making β robust to the cohort's uneven tick-position composition. β remains one coefficient (one headline p-value; FDR membership unaffected). Drawdown depth is near-constant within a session, so Gemini's close-skew does not restrict the range of DD observed; the handling targets the dHHI_trade side. A non-open-restricted sensitivity analysis is registered.
- **RQ6.** The frozen-context sample is tick-position-stratified with identical composition across models, and the measurement batch is run off-peak, so the cross-model divergence comparison is like-for-like and does not reproduce live open-bell congestion. Gemini's open-bell availability is a separate quantity (above) and is not folded into Delta_m.

---

## 4. Analysis software

The definitions above are implemented in:

- [`src/analytics/research_metrics.py`](../src/analytics/research_metrics.py) — the six RQ metrics + the FDR family driver.
- [`src/analytics/regime_classifier.py`](../src/analytics/regime_classifier.py) — §3.1 regime labeling.
- [`src/analytics/statistical_corrections.py`](../src/analytics/statistical_corrections.py) — §3.2 BH-FDR, §3.3 deflated Sharpe, §3.4 block bootstrap BCa.

Run `python -m src.analytics.research_metrics` to regenerate every metric in this document from the current logs. The living status of each RQ is tracked in [`RESEARCH_QUESTIONS.md`](RESEARCH_QUESTIONS.md).

---

## 5. Amendment log

Append-only. Each entry: date · what changed · why. No edits above this line after the OSF deposit date.

| Date | Change | Rationale |
|------|--------|-----------|
| 2026-05-19 | Initial registration (v1). | Locks design before the live (confirmatory) phase. |
| 2026-05-20 | Reconciled phase dates to one canonical timeline: Phase A (paper) 2026-04-09 → 2026-10-31; Phase B (live) 2026-11-01 → 2027-11-01 (12-month live window). | Aligned `settings.json`, `README.md`, and `v1.json`; the prior draft ended paper on 2026-10-09 with an approximate live window. No change to research questions, metrics, or decision rules. |
| 2026-05-22 | Landed the ratified RQ5/RQ6 specification. **RQ5** → four registered metrics (dropped `num_positions`) plus the pooled drawdown-conditioned concentration-response **headline test** (dHHI_trade ~ DD with model + tick-position fixed effects; L = round(n^(1/3)); one BH-family p-value). **RQ6** → reframed from "non-determinism at temperature 0" to "operational reproducibility of deployed agents" (characterization, not in the FDR family) with the Per-Model API Configuration table inline. Added **§3.9 Phase A data integrity** (shakedown April 9–22 / pilot window opens April 23; Sonnet/Opus state-file commingling; RQ5 derived dependent variable; Gemini availability + per-RQ tick-position handling). | Pre-deposit drafting (the OSF deposit has not occurred). Resolves the items raised by the Task 1 RQ5/Gemini diagnostics. Sources: `docs/RQ5-RQ6-specification.md`, `RQ5-pilot-window-and-Phase-A-data-integrity.md`, `Gemini-selection-bias-characterization-and-per-RQ-handling.md`, `RQ5-RQ6-spec-completion.md`. |

---

*This is a personal research experiment. Not financial advice. Pre-registered on 2026-05-19; methodology locked.*
