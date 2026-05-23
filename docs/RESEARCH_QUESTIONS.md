# Research Questions — Living Status Tracker

**Last updated:** 2026-05-19
**Current phase:** Phase A — Paper Trading (**PILOT data — not confirmatory**)
**Pre-registration:** [`PRE_REGISTRATION.md`](PRE_REGISTRATION.md) · machine-readable [`data/pre_registration/v1.json`](../data/pre_registration/v1.json)
**Regenerate:** `python -m src.analytics.research_metrics`

> ⚠️ **Everything below is pilot data from the paper-trading phase.** Per the pre-registration, confirmatory tests run on **live-phase data only** (begins ≈ Nov 2026). The numbers here validate that the pipeline measures what it should and give early power estimates — they are **not** results. Decision rules will be applied to the live window.

This file is refreshed monthly alongside the monthly report. Each RQ shows its status, the headline metric, the current pilot reading, and (where applicable) the regime breakdown and bootstrap interval.

---

## Status summary

| RQ | Question | Status | Headline metric | Pilot reading (paper) |
|----|----------|--------|-----------------|------------------------|
| **RQ1** | Convergence under identical inputs (PRIMARY) | 🟡 Testing | Action concordance vs shuffled null | **0.627 vs 0.493** (+0.134, *p*≈0.003) |
| **RQ2** | Disposition effect | 🟡 Testing | PGR − PLR | **−0.089** (reverse: cuts losers faster) |
| **RQ3** | Confidence calibration | 🟡 Testing | conf↔outcome corr / ECE | **r=+0.14 / ECE=0.38** (521 closed) |
| **RQ4** | Style-factor tilts | ⚪ Open | FF5+MOM betas | Factor data not yet overlapping |
| **RQ5** | Drawdown response | ⚪ Open | Δ(HHI, turnover, size, cash) | No ≥10% drawdown yet |
| **RQ6** | Non-determinism @ temp=0 | ⚪ Open | Decision flip rate | Manual probe not yet run |

Status legend: ⚪ **Open** (insufficient data) · 🟡 **Testing** (accumulating, no decision) · 🟢 **Resolved** (decision rule met).
Data window for the readings below: **2026-04-08 → 2026-05-13** · 6 models.

**Multiple-comparison control (BH-FDR, q=0.10):** on pilot data, RQ1, RQ2, and RQ3 survive correction (critical raw *p* ≈ 0.0025). RQ4/RQ5 not yet testable. *Pilot only — re-run on live data for the confirmatory family.*

---

## RQ1 — Decision convergence under identical information sets · PRIMARY

**Status: 🟡 Testing** — strong early signal.

The models agree on **62.7%** of buy/sell/hold calls when ruling on the same ticker under provably identical inputs, against a shuffled-permutation chance level of **49.3%** — an excess of **+13.4 points** (permutation *p* ≈ 0.003; BCa 90% CI on observed concordance [0.607, 0.647]). Target-weight vectors correlate at **r = 0.56**. So convergence shows up on both the binarized and the continuous signal.

| Regime | Pairwise obs | Action concordance |
|--------|-------------:|-------------------:|
| Bull-trending | 436 | 0.614 |
| Neutral | 414 | 0.640 |

Only bull/neutral regimes appear because the paper window's decision dates fall entirely in those SPY regimes — exactly the single-regime limitation [`BACKTEST_HARNESS_SCOPE.md`](BACKTEST_HARNESS_SCOPE.md) is built to fix. **Ceiling caveat:** measured convergence is bounded above by RQ6's within-model determinism, which has not been measured yet.

---

## RQ2 — Disposition effect

**Status: 🟡 Testing** — and the early sign is the *opposite* of the human pattern.

Pooled **PGR = 0.059, PLR = 0.148 → PGR − PLR = −0.089** (bootstrap *p* ≈ 0). Every model is negative, i.e. they realize **losses** at a higher rate than gains — disciplined loss-cutting, the reverse of the classic retail disposition effect. The effect is strongest for the Claude models:

| Model | PGR − PLR |
|-------|----------:|
| Claude Sonnet | −0.252 |
| Claude Opus | −0.203 |
| GPT-5.4 | −0.131 |
| Gemini 3.1 Pro | −0.063 |
| Grok 4.20 | −0.049 |
| DeepSeek | −0.043 |

If this holds on live data it is a genuinely interesting result: these models do not behave like loss-averse retail traders.

---

## RQ3 — Confidence calibration on closed trades

**Status: 🟡 Testing** — directionally informative, poorly calibrated in absolute terms.

Across **521 closed (full-exit) trades**, entry confidence correlates with the profitable-outcome flag at **r = +0.14** — higher-confidence trades do win slightly more often — but the **ECE is 0.38** (Brier 0.37), meaning the *level* of confidence badly overpredicts win probability. In plain terms: a "9/10" is not a 90% bet. All six models clear the pre-registered 20-closed-trade minimum (Claude 39, GPT 98, Gemini 90, Grok 119, DeepSeek 139, Opus 36).

---

## RQ4 — Systematic style-factor tilts

**Status: ⚪ Open.** The OLS-on-FF5+momentum machinery is implemented and tested, but the Ken French daily factor series does not yet overlap the 2026 portfolio dates, so there are zero aligned regression days. This resolves itself as the factor library publishes and, more importantly, once the live window provides ≥60 aligned days. Cross-regime loadings need the backtest harness.

---

## RQ5 — Behavioral response to portfolio drawdowns

**Status: ⚪ Open.** No model has been ≥10% below its trailing 60-day equity peak during the paper window — the tape has been too kind to trigger the test. The drawdown-vs-normal comparison (HHI, turnover, position size, cash) is implemented and will populate the moment a model draws down, or via the backtest harness against 2020/2022 tapes.

---

## RQ6 — API non-determinism characterization (operational reproducibility of the deployed agents)

**Status: ⚪ Open.** Frozen-context probe not yet run.

- *Question:* How run-to-run reproducible is each model's decision-making at the configuration it is actually deployed and traded at?
- *Operationalization:* Measured at the deployed configuration — provider-default sampling, no temperature parameter set — not at temperature 0. The deployed pipeline sends no temperature parameter to any model, so temperature 0 would characterize an off-deployment configuration for the entire cohort; the original temperature-0 framing is withdrawn. Per-model temperature behavior (four of six models honor temperature) is a disclosed fact in the per-model API-configuration table, not the basis for the reframe.
- *Metric:* Δ_m — per-model run-to-run decision divergence across repeated calls at the deployed configuration. Replaces the superseded flip-rate metric.
- *Type:* Characterization research question, not a null-hypothesis test.
- *Multiple-testing:* Not a member of the Benjamini-Hochberg FDR family.
- *Cross-reference:* METHODOLOGY § "API Non-Determinism (RQ6) — Deployed-Configuration Basis"; `PRE_REGISTRATION.md` RQ6 entry and §3.6.

---

*Pilot readings regenerated from the production logs by `src/analytics/research_metrics.py`. Confirmatory analysis is reserved for the live phase per the locked pre-registration.*
