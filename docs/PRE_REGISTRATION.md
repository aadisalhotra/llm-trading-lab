# Pre-Registration — Autonomous LLM Trading Lab

**Registration date:** 2026-05-19
**Pre-registration version:** v1
**Status:** 🔒 LOCKED
**Author:** Aadi Salhotra
**Planned public deposit:** OSF, before 2026-11-01 (i.e. before the live phase generates any inferential data)
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
| **A — Paper** | 2026-04-09 → 2026-10-09 | $100K simulated | **Pilot.** Pipeline validation, definition-locking, power analysis. *Not* confirmatory. |
| **B — Live** | ≈2026-11 → 2027-10-09 | $1K real | **Confirmatory.** All inferential tests below run on this window. |

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
- **Decision rule.** **Resolved-converge** if, on the live window, the permutation *p*-value < 0.05 after Benjamini-Hochberg correction **and** the BCa 90% CI for excess concordance excludes 0. **Resolved-null** if the CI includes 0. The convergence ceiling is interpreted against the RQ6 non-determinism floor: convergence cannot exceed (1 − within-model flip rate).
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

### RQ5 — Behavioral response to portfolio drawdowns

> **How do LLMs respond to portfolio drawdowns?**

- **Status:** Open (no model has hit the drawdown trigger yet)
- **Hypothesis.**
  - H₀: Portfolio behavior (concentration, turnover, position size, cash) is unchanged between drawdown and normal days.
  - H₁: Behavior shifts measurably when a model is in drawdown.
- **Operationalization.** A model is **in drawdown** on an EOD when its portfolio value is **≥ 10% below its trailing 60-day peak** (measured on end-of-day portfolio value). *This is the model's own-equity drawdown and is deliberately distinct from the SPY drawdown **regime** (5% off a 60-day peak) used for stratification.* For each EOD we compute four behavioral metrics from that day's last `portfolio_after` snapshot and trade activity, then compare drawdown days vs normal days.
- **Metric definition.** Drawdown-minus-normal difference in:
  1. **HHI concentration** = Σ wᵢ² over holdings
  2. **Turnover rate** = Σ |executed notional| that day / EOD portfolio value
  3. **Average position size** = mean holding weight
  4. **Cash allocation** = cash / total value
- **Pre-registered prediction.** In drawdown, models become **more conservative**: cash allocation rises, HHI falls (de-concentration), average position size falls. Turnover **rises** transiently as positions are cut.
- **Decision rule.** Per model with ≥ 1 drawdown episode and a normal-day contrast: **Resolved** for each metric whose BCa 90% CI on the drawdown-minus-normal difference excludes 0 (live data). The HHI-change test (pooled) enters the FDR family.
- **Data window required.** At least one ≥10% drawdown episode plus ≥ 20 contrasting normal days, per model, on live data. If no model draws down in the live window, RQ5 is reported **Open / not testable on this tape** and deferred to the backtest harness.

---

### RQ6 — Non-determinism at temperature = 0 · **METHODOLOGICAL**

> **How non-deterministic are frontier LLM trading decisions at temperature = 0?**

- **Status:** Open (manual probe not yet run)
- **Hypothesis.**
  - H₀: At temperature = 0, identical inputs yield identical decisions (flip rate = 0).
  - H₁: Decisions vary across reruns even at temperature = 0 (flip rate > 0) — irreducible API non-determinism.
- **Operationalization.** **Manual-trigger only.** On a seeded random **5% subsample** of decision ticks, each model is re-run **K = 10** times at **temperature = 0** on the *same* inputs (`scripts/determinism_probe.py`, output to `data/determinism/`). For each (model, tick, ticker) we compare the action across the K reruns.
- **Metric definition.** Per model:
  - **Decision flip rate** = fraction of (tick, ticker) decisions where the K reruns do **not** all agree on the action (headline).
  - **Mean normalized action entropy** across reruns.
  - **Mean target-weight standard deviation** across reruns.
- **Pre-registered prediction.** Flip rate is **small but non-zero** (≈ 1–10%), and **larger for reasoning models** (Grok, DeepSeek, o-style) than for low-temperature deterministic decoders. The flip rate sets the empirical **ceiling on RQ1 convergence**: two models cannot agree more reliably than one model agrees with itself.
- **Decision rule.** Descriptive — **not** part of the FDR family. **Resolved** once K = 10 reruns exist for ≥ 5% of a representative live-week's ticks; report per-model flip rate with a binomial 90% interval and use it to bound the RQ1 interpretation.
- **Data window required.** ≥ 1 representative trading week of ticks, 5% sampled, K = 10 reruns each, per model.

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

### 3.6 API non-determinism characterization

Because frontier model APIs are not bitwise deterministic even at temperature = 0 (RQ6), the **measured** convergence in RQ1 has a ceiling below 1.0. We characterize this floor explicitly (RQ6) and interpret RQ1 against it: reported convergence is always read relative to (1 − within-model flip rate), never against a naïve 100%.

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

---

*This is a personal research experiment. Not financial advice. Pre-registered on 2026-05-19; methodology locked.*
