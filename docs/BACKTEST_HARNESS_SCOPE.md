# Backtest Harness — Scope & Known Limitation

**Status:** Planned (v2). Not built in Phase A.
**Owner:** Aadi Salhotra
**Target build:** late 2026 / early 2027, after the live phase stabilizes.
**Related:** [`PRE_REGISTRATION.md`](PRE_REGISTRATION.md) §1, §3.1, RQ1/RQ4/RQ5.

---

## 1. The limitation, stated plainly

The live phase (≈ Nov 2026 → Oct 2027) covers **one** market-regime path — whatever the market happens to do over those twelve months. Three of the six research questions are only fully answerable **across** regimes:

- **RQ1 (convergence)** predicts convergence is *strongest in trending regimes and weakest in vol-spikes*. You cannot test a cross-regime claim on a single regime path.
- **RQ4 (style tilts)** estimates factor loadings that are known to rotate with the regime (value vs growth leadership flips; momentum crashes in reversals). One regime gives one snapshot of loadings.
- **RQ5 (drawdown response)** literally requires drawdowns. If the live year is calm, no model crosses the 10% trigger and the question is untestable on the live tape — exactly what the pilot already shows (0 drawdown days so far).

The regime stratification in `regime_classifier.py` makes this limitation **visible** (the monthly report shows which regimes have data and which are empty), but visibility is not a fix. Stratifying a single-regime tape just produces one populated stratum and several empty ones — which is precisely what the current pilot output shows (only bull-trending and neutral strata have observations).

This is a **known Phase A limitation**, recorded here so it is not mistaken for an oversight. The fix is a separate pipeline, scoped below as v2 work.

---

## 2. Why the live pipeline can't do this itself

The live pipeline is, by design, **online**: it fetches *today's* market data and news, builds prompts, and executes. It has no mechanism to place a model "as of 2020-03-16" with only the information available on that morning. Re-pointing it at history would leak look-ahead in a dozen ways (today's news cache, today's universe membership, adjusted prices that bake in future splits/dividends). A faithful cross-regime study needs a **point-in-time** harness that is a distinct piece of software.

---

## 3. Target regimes for the backtest

Chosen to span the regime taxonomy in `PRE_REGISTRATION.md` §3.1 with maximum behavioral contrast:

| Window | Regime character | Primarily exercises |
|--------|------------------|---------------------|
| **2020 — COVID crash & recovery** (Feb–Jun 2020) | Vol-spike → V-recovery | RQ5 (drawdown response), RQ1 (does convergence break under stress?) |
| **2022 — inflation / rate-hike bear** (Jan–Oct 2022) | Sustained drawdown, value > growth | RQ4 (factor rotation), RQ5, RQ2 (disposition under losses) |
| **2024 — rate-cut cycle / soft landing** (2024) | Bull-trending, low vol | RQ1 (trending-regime convergence ceiling), RQ4 (growth/momentum tilt) |

Each window is labeled by the same `regime_classifier.py` criteria, so backtest decisions drop straight into the existing regime-stratified RQ metrics.

---

## 4. Harness design (v2)

A separate, **non-executing** pipeline that replays history through the same model brains:

1. **Point-in-time information packets.** For each historical decision timestamp, assemble the same prompt payload the live pipeline builds — universe snapshot, OHLCV/technicals, news headlines + sentiment, portfolio state — but constructed **as-of that date** with no look-ahead:
   - prices/technicals from a point-in-time source (no future-adjusted closes);
   - a frozen universe membership as of that date (no survivorship bias);
   - news restricted to items published on or before the timestamp (the hardest part — requires a dated news archive, not the live cache).
2. **Same screen→trade path.** Reuse `prompt_builder.py` and the adapters unchanged so the cognition is identical to live; only the data source differs. Decisions are **recorded, not executed** — a simulated fills engine marks them against point-in-time prices for P&L.
3. **Same logs, same metrics.** Write to the same decision/performance JSONL schema (under a `backtest/` namespace) so `research_metrics.py` runs over it with **zero changes**, producing regime-stratified RQ outputs directly comparable to live.
4. **Explicit hazards to control:** look-ahead in news and fundamentals, survivorship in the universe, adjusted-price leakage, and the fact that models may have **memorized** the historical period from pretraining (a real confound for any LLM backtest — must be acknowledged as a limitation of the backtest arm itself, and is *why* the live phase remains the confirmatory anchor).

---

## 5. What stays confirmatory

The backtest is a **robustness and generalization** arm, not a replacement for the live phase. Pretraining contamination means a model "trading" 2020 may be partly recalling 2020. So:

- **Live phase** = the confirmatory test (pre-registered decision rules resolve here).
- **Backtest harness** = cross-regime robustness for RQ1/RQ4/RQ5, reported as supporting evidence with its contamination caveat stated.

---

## 6. Build sequence

1. (v2.0) Point-in-time price + universe snapshots; simulated fills; backtest log namespace.
2. (v2.1) Dated news archive integration (or run news-free as a sensitivity arm).
3. (v2.2) Replay the three target windows for all models; regime-stratified RQ outputs.
4. (v2.3) Fold backtest strata into the monthly report's Research Questions section beside live strata.

Tracked as v2 work to start **after** the live phase is stable and the live confirmatory data is accruing — not before, so live execution remains the priority.
