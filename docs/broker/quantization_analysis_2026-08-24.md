# Whole-Share Quantization Analysis — universe snapshot 2026-08-24

**Lane:** Operations — broker de-risking
**Purpose:** The last quantitative input to the 2026-09-15 venue decision. Measures
whether whole-share order sizing can express the position weights the experiment
registers, at capital levels this project would plausibly commit.
**Status:** Closed. Venue resolved to **Alpaca, cash branch**, with the registered
software-segregation structure.

This file is the citable record of the computation. It carries the price snapshot it
was computed against, both threshold definitions, the mechanical results, and the
verdict, so the figure quoted in the Sept 15 memo is reproducible from here alone.

---

## 1. Why this computation exists

IBKR's Web API supports **no fractional or notional equity orders**. That is confirmed
three times in writing: D-1 (IBKR Client Services, 2026-08-17), and D-5 and D-6 (IBKR
API Integration, 2026-08-19) — the last of which adds that IBKR is *"not aware of any
immediate plans to expand Fractional Share trading to all TWS API or Web API
clients."* Alpaca supports fractional trading default-on for cash accounts (D-4).

Under whole-share sizing the achievable position weights form a **discrete grid** whose
spacing is one share as a fraction of book equity. If that grid is coarse relative to
the weights the models register, `target_weight` stops meaning what it registers, and
the RQ1 decision unit degrades. This computation measures the grid.

## 2. Price snapshot — provenance and revision exposure

| Field | Value |
|---|---|
| Universe | `config/universe.json` v2, effective 2026-04-10 — **79 tickers** |
| Close date | **2026-08-24** — all 79 on the same session, no staleness |
| Retrieved | 2026-08-24, via yfinance (the pipeline's own market-data source) |
| Coverage | 79 / 79 priced, zero missing |
| Cross-check | ABT 116.67, LLY 1246.93, TMO 628.74 match `data/dashboard.json` `ticker_tape` exactly |

**These closes carry revision exposure.** The `canonical_spy_return` episode revised a
fixed-window SPY return retroactively (10.32% → 9.69%), and the same upstream can
revise these. This snapshot is therefore **frozen in §7 as of the retrieval date** and
must be cited with that date. Do not re-fetch to "refresh" the table — a re-fetch
produces a different computation, not a corrected one.

## 3. Threshold definitions

Both thresholds are Research's, applied here mechanically.

### T1 — holdability, zero tolerance

> A name passes T1 if **one whole share does not breach the registered 20% per-name
> position cap**. The book passes T1 only if **every** name passes.

`portfolio_rules.max_position_pct = 0.2` in `config/settings.json`. Zero tolerance is
the right posture because the failure is categorical rather than gradual: a name whose
single share exceeds the cap **cannot be held at any size**. It is not mis-weighted,
it is unavailable, and it silently leaves the investable universe.

### T2 — fidelity

> **median ≤ 1.0pp** and **p90 ≤ 2.5pp** of book equity.

Applied to the **quantization step** — one share as a percentage of book equity. The
step is the spacing of the achievable weight grid, and it is *target-independent*: it
describes the instrument, not one particular sizing choice.

**T2 is evaluated on the step, not on realized-vs-target error, and the distinction is
load-bearing** — see §5, where the two metrics return opposite verdicts at $10,000.

## 4. Mechanical results

### T1 — holdability

| Book | 20% cap | Names breaching | Verdict |
|---|---|---|---|
| $1,000 | $200 | **42 / 79** | **FAIL** |
| $4,000 | $800 | **6 / 79** — BLK, CAT, COST, EQIX, GS, LLY | **FAIL** |
| $10,000 | $2,000 | **0 / 79** | **PASS** |

$1,000 breach list: AAPL ABBV ADBE AMD AMZN APD AVGO AXP BLK BRK-B CAT COST CRM CVX DE
EQIX GE GLD GOOGL GS HD HON ISRG JNJ JPM LIN LLY LMT LOW MA MCD META MS MSFT NVDA RTX
SHW TMO TSLA UNH UNP V.

T1 first clears at a book of **$6,235** (= highest close $1,246.93 ÷ 0.20).

### T2 — quantization step (one share as pp of book)

| Book | Median | p90 | Max | Verdict (median ≤1.0, p90 ≤2.5) |
|---|---|---|---|---|
| $1,000 | 20.906 | 60.564 | 124.693 | **FAIL** |
| $4,000 | 5.226 | 15.141 | 31.173 | **FAIL** |
| $10,000 | **2.091** | **6.056** | 12.469 | **FAIL** |

At $10,000 the median step is 2.091pp — the grid cannot express a 2% target at all for
the median name, because the smallest non-zero position it admits is already larger
than the target.

### Supplementary — realized-vs-target error, |realized − target| after rounding to nearest whole share

| Book | Target | Median | p90 | Max | Rounds to 0 shares |
|---|---|---|---|---|---|
| $1,000 | 2.00% | 2.000 | 2.000 | 2.000 | 77 / 79 |
| $1,000 | 5.94% | 5.940 | 5.940 | 5.940 | 58 / 79 |
| $4,000 | 2.00% | 2.000 | 2.000 | 2.000 | 46 / 79 |
| $4,000 | 5.94% | 1.182 | 5.940 | 5.940 | 14 / 79 |
| $10,000 | 2.00% | 0.576 | 2.000 | 2.000 | 16 / 79 |
| $10,000 | 5.94% | 0.421 | 1.680 | 5.940 | 1 / 79 |

### Observed target-weight distribution — Phase A

From `accepted_decisions[].target_weight` across `data/trades/*.jsonl`, from the pinned
pilot start **2026-04-23** (`RQ5_PHASE_A_PILOT_START`, `src/analytics/research_metrics.py`),
which excludes the shakedown and state-commingling window. 6,486 records, 29,690
decisions scanned.

| Set | n | Q1 | Median | Q3 | Mean | Max | < 2% |
|---|---|---|---|---|---|---|---|
| All accepted, weight > 0 | 27,885 | 4.00% | **5.94%** | 9.80% | 6.81% | 20.00% | 3.91% |
| BUY/SHORT (position-establishing) | 4,449 | 4.00% | 5.00% | 8.00% | 6.19% | 20.00% | 2.41% |

The models target roughly **5–6%**, not 2%. A 2% baseline is about 3× tighter than
observed behaviour, so any result computed at 2% understates the operational problem.

## 5. The metric sensitivity, recorded so it is not re-derived wrongly

**At $10,000 the two candidate T2 metrics disagree**, and the venue conclusion depends
on which one is meant:

| Metric at $10,000 | Median | p90 | Verdict |
|---|---|---|---|
| Quantization **step** | 2.091 | 6.056 | **FAIL** |
| Error @ 2.00% target | 0.576 | 2.000 | PASS |
| Error @ 5.94% target | 0.421 | 1.680 | PASS |

Two findings settle it in favour of the step:

1. **T2's p90 ≤ 2.5pp threshold is vacuous against the 2% error metric.** Rounding a
   2% target to the nearest whole share can miss by at most 2.00pp — down to zero
   shares (error exactly 2.00pp) or up from ideal ≥ 0.5 shares (realized ≤ 4%, error
   ≤ 2.00pp). The metric is bounded above by 2.00pp, below the 2.5pp threshold, so the
   test **cannot fail by construction**. A threshold that cannot be violated is not a
   test, and a PASS derived from it carries no information.

2. **The step reading reproduces Research's own capital figure.** Solving the step
   metric for the book that clears T2: median ≤ 1.0pp needs ≥ $20,906 (median close
   $209.06 × 100); p90 ≤ 2.5pp needs ≥ $24,225 (p90 close $605.64 × 40). The binding
   constraint is **$24,225 per book → $145,353 across six books**, which lands inside
   Research's stated $15,000–25,000 per book and $90,000–150,000 total. The error
   readings do not reproduce those figures; the step reading does.

Cite T2 as the step metric. The realized-vs-target error table in §4 is supplementary
and must not be quoted as a T2 verdict.

## 6. Verdict

| Book | T1 | T2 | Outcome |
|---|---|---|---|
| $1,000 | FAIL (42 unavailable) | FAIL (median 20.9pp) | Not viable |
| $4,000 | FAIL (6 unavailable) | FAIL (median 5.23pp) | Not viable |
| $10,000 | PASS | FAIL (median 2.09pp, p90 6.06pp) | Not viable |
| ~$24,225 | PASS | PASS (binding: p90) | Minimum viable IBKR book |

**IBKR is not viable at any capital level this project will plausibly commit.** Making
whole-share sizing merely faithful requires roughly **$145,000** across the six books.

**Alpaca's fractional and notional sizing makes quantization error identically zero at
every book size.** At $0.01 notional granularity the grid spacing is 0.00025pp at a
$4,000 book and 0.00010pp at $10,000 — four orders of magnitude inside T2's tightest
threshold, and small enough that the constraint stops being meaningful rather than
merely being satisfied. The live weight grid therefore matches the paper-phase grid,
which is a comparability property the experiment gets for free on this venue and
cannot buy on the other.

**The 2026-09-15 decision resolves to Alpaca, cash branch.**

**The IBKR structure is preserved and not unwound** — the F-account, the linked client
account, and the activated OAuth consumer key all remain in place. It costs nothing to
hold and it is the documented fallback if anything about Alpaca changes before the
November live-phase boundary.

## 7. Frozen price snapshot — 79 closes, 2026-08-24

Sorted by close, descending. This is the exact input to §4.

| # | Ticker | Close | # | Ticker | Close |
|---|---|---|---|---|---|
| 1 | LLY | 1246.93 | 41 | NVDA | 208.48 |
| 2 | BLK | 1172.61 | 42 | CVX | 203.09 |
| 3 | EQIX | 1055.20 | 43 | PM | 191.46 |
| 4 | GS | 1036.28 | 44 | TMUS | 182.62 |
| 5 | COST | 971.40 | 45 | AMT | 178.39 |
| 6 | CAT | 811.02 | 46 | XOM | 164.05 |
| 7 | DE | 648.64 | 47 | QCOM | 158.53 |
| 8 | TMO | 628.74 | 48 | MRK | 150.66 |
| 9 | MA | 599.86 | 49 | EOG | 150.21 |
| 10 | LMT | 564.14 | 50 | PG | 146.60 |
| 11 | META | 559.02 | 51 | PEP | 144.67 |
| 12 | BRK-B | 504.32 | 52 | PLD | 143.36 |
| 13 | LIN | 490.03 | 53 | ORCL | 142.45 |
| 14 | MSFT | 487.31 | 54 | TJX | 140.71 |
| 15 | AMD | 456.74 | 55 | COP | 133.35 |
| 16 | GLD | 426.69 | 56 | USO | 132.21 |
| 17 | UNH | 398.76 | 57 | NEM | 131.84 |
| 18 | V | 382.41 | 58 | DUK | 121.97 |
| 19 | ISRG | 373.57 | 59 | ABT | 116.67 |
| 20 | AVGO | 358.76 | 60 | DIS | 110.61 |
| 21 | JPM | 356.39 | 61 | CSCO | 110.23 |
| 22 | TSLA | 348.95 | 62 | SBUX | 107.49 |
| 23 | GOOGL | 348.06 | 63 | WMT | 106.49 |
| 24 | SHW | 346.73 | 64 | UPS | 102.72 |
| 25 | GE | 341.84 | 65 | KO | 91.99 |
| 26 | HD | 337.43 | 66 | SO | 90.10 |
| 27 | AXP | 337.33 | 67 | INTC | 87.26 |
| 28 | AAPL | 310.34 | 68 | NEE | 84.10 |
| 29 | UNP | 309.86 | 69 | NFLX | 80.01 |
| 30 | APD | 306.24 | 70 | FCX | 77.80 |
| 31 | ADBE | 276.27 | 71 | CCI | 76.40 |
| 32 | JNJ | 273.04 | 72 | D | 66.61 |
| 33 | MCD | 272.54 | 73 | BAC | 62.33 |
| 34 | ABBV | 264.52 | 74 | SLV | 62.20 |
| 35 | AMZN | 262.07 | 75 | SLB | 54.00 |
| 36 | LOW | 216.98 | 76 | NKE | 40.75 |
| 37 | HON | 214.77 | 77 | CPER | 40.07 |
| 38 | MS | 214.08 | 78 | PFE | 27.97 |
| 39 | RTX | 209.22 | 79 | CMCSA | 27.02 |
| 40 | CRM | 209.06 | | | |

Summary statistics used above: median close **$209.06**, p90 close **$605.64**, max
close **$1,246.93**.

---

## Sources

- `config/universe.json` (v2, effective 2026-04-10) — the 79-name universe
- `config/settings.json` — `portfolio_rules.max_position_pct = 0.2`
- `data/trades/*.jsonl` — `accepted_decisions[].target_weight`, Phase A
- `src/analytics/research_metrics.py` — `RQ5_PHASE_A_PILOT_START = "2026-04-23"`
- `broker_confirmations_2026-08.md` — D-1, D-4, D-5, D-6
- yfinance daily closes, retrieved 2026-08-24
