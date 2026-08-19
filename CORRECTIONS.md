# Corrections register

Published corrections to figures, rates, and derived claims. Each entry records
what was wrong, what it was corrected to, the evidence, and any residual
uncertainty that survives the correction. Corrections are appended, never
rewritten — a superseded entry gets a follow-up entry, not an edit.

The directional rule for cost and performance claims: where evidence bounds a
value rather than fixing it, choose the bound that is unfavourable to us —
overstate our own costs, understate our own returns. A correction that moves a
figure in our favour must be attested, not merely plausible.

---

## 2026-08-05 — API cost rates: four never-published rates corrected; Grok cut-date boundary set archive-conservative

**Affects:** all API-cost figures for 2026-04-08 onward — the cumulative and
per-month cost lines in daily reports, and any cost-efficiency ranking derived
from them. Does **not** affect: monthly data layers and monthly PDFs (carry no
API-cost fields — verified), the dashboard, RQ datasets, or portfolio
accounting.

### What was wrong

`src/analytics/cost_rates.py` carried a flat rate table in which four of six
active model rates had never been a published list price at any date. These
were entry errors, not stale prices:

| Model | Carried | Actual | Effect |
|---|---|---|---|
| `claude-opus-4-6` | $15.00 / $75.00 | $5.00 / $25.00 (single tier) | 3.0× overstated |
| `gpt-5.4` | $10.00 / $30.00 | $2.50 / $15.00 | ~3.1× overstated |
| `grok-4.20-0309-reasoning` | $5.00 / $25.00 | $2.00 / $6.00, then $1.25 / $2.50 | ~4–5× overstated |
| `gemini-3.1-pro-preview` | $3.50 / $14.00 (flat) | $2.00 / $12.00 (≤200K) / $4.00 / $18.00 | ~1.55× overstated |
| `deepseek-v4-pro` | $1.74 / $3.48 | $0.435 / $0.87 | 3.0–4.0× overstated |
| `claude-sonnet-4-6` | $3.00 / $15.00 | $3.00 / $15.00 | correct throughout |

The DeepSeek entry was the "post-promo standard" rate. The 75%-off launch rate
was extended and then made the permanent list price (2026-05-22); the standard
rate never applied for a single day.

A sixth error was structural rather than numeric: `claude-opus-4-6` carried a
`>200K`-prompt long-context tier at **$10.00 / $37.50**. The provider states
that 4.6-generation models bill the **full 1M-token context window at standard
pricing**, so no such tier exists. The entry has been collapsed to a single
$5/$25 tier. Dollar impact is zero — no lab call approaches 200K input tokens,
so the row never priced a record — but a rate the provider denies does not stay
in a corrected file on the strength of being inert.

### Corrected figures

Every logged call repriced from stored token counts at the rate in force on the
call's date (`scripts/cost_rates_reconciliation_2026_08.py`, read-only):

| Period | As logged | Corrected | Overstated by |
|---|---|---|---|
| 2026-04 | $120.88 | $52.93 | $67.95 (+128%) |
| 2026-05 | $186.74 | $77.10 | $109.64 (+142%) |
| 2026-06 | $244.93 | $98.92 | $146.01 (+148%) |
| 2026-07 | $268.49 | $108.36 | $160.13 (+148%) |
| **Apr–Jul cumulative** | **$821.04** | **$337.31** | **$483.73 (+144%)** |

Per-record cross-check: **zero anomalies** — the old table reproduces every
stored `cost_usd` to <5e-4, so it is a complete account of how every logged cost
arose and the delta above is the full extent of the error.

Every figure in this entry is stated over **closed months only (April–July
2026)**. August is deliberately excluded: the run is live and an August-inclusive
total changes with each intraday tick, which is not a property a correction
register should have.

The Apr–Jul cumulative is $337.315379 exactly. **$337.31** is the sum of the
per-month figures as rounded in the table above; the rounded full-precision sum
is $337.32. Both describe the same number — the half-cent is display rounding,
not a discrepancy.

Cost-efficiency rankings in published daily reports shift materially: July
correction multipliers were Grok 5.0×, DeepSeek 4.0×, GPT 3.1×, Opus 3.0×,
Gemini 1.55×, Sonnet 1.0×.

### Residual uncertainty — Grok cut-date ambiguity window (disclosed)

The date xAI cut `grok-4.20` from $2/$6 to $1.25/$2.50 is **bounded by the
archive, not attested by it**:

- Last capture showing the **old** rate: **2026-05-01 07:03 UTC**
- First capture showing the **new** rate: **2026-05-06 16:31 UTC**
- Ambiguity window: **(2026-05-01 07:03 → 2026-05-06 16:31 UTC]**

No capture exists inside that window. The effective date is therefore set to
**2026-05-07** — the old rate holds until a new rate is attested. Every call
inside the ambiguity window attributes at the higher (old) rate.

**Press reporting suggests an earlier cut that the archive does not attest.**
Contemporaneous coverage places the grok-4.3 launch wave and its pricing at
2026-05-04. We did not adopt it: press reporting is not a price-page capture,
and the archive-conservative boundary is the evidence-governed one. The cost of
this choice is quantified — a 2026-05-07 boundary overstates our own Grok cost
by **$0.49** relative to a 2026-05-04 boundary ($337.31 vs $336.82 cumulative).
That is the correct direction under the directional rule above, and the
corrected cumulative derives from our own fill records rather than from press
reporting.

An earlier boundary of 2026-05-01 was staged and rejected: the 2026-05-01
07:03 UTC capture itself shows the old rate, so 05-01 is contradicted by direct
evidence.

### Marked unverified

`grok-4.20-0309-reasoning` period 1, **>200K-prompt tier ($4/$12)**: could not
be confirmed against any archived capture of the period-1 price page. It is
flagged `UNVERIFIED` in the table's provenance note and is inert for lab traffic
(no logged call exceeds ~11K input tokens), so it has never priced a record.
Do not rely on it without re-verification.

### How the corrected figures reach reports — read-time repricing

The JSONL decision history is **immutable**: no stored `cost_usd` or
`screening_cost_usd` value is rewritten by this correction. Instead
`compute_api_cost_summary` and `compute_api_cost_summary_window` now **reprice
at read time** from each record's stored token counts and its own date against
the corrected rate table, and no longer sum the logged cost fields at all. Every
surface fed by those functions — daily reports, the dashboard, the budget
monitor, the email digest — therefore shows corrected costs from this deploy
forward, for historical records as well as new ones.

Two consequences worth stating plainly:

- **Screening calls are now included.** The previous implementation summed only
  the decision call and silently omitted the screening leg, which is a
  substantial share of spend: over the four closed months Apr–Jul, $224.85
  decision + $104.94 screening. Repriced totals are therefore *not* comparable
  to pre-correction totals by the rate multiple alone — the screening leg is
  new to the figure. The split is exposed as `decision_cost_usd` /
  `screening_cost_usd` so the change is never hidden inside an aggregate.
- **Historical screening input tokens are back-solved.** Records written before
  2026-08-05 log a screening call's output tokens and its cost but not its
  input tokens. Input is recovered by inverting the logged cost against the
  frozen pre-2026-08 rate table (`LEGACY_FLAT_TABLE_PRE_2026_08`), exact to
  ±0.2 tokens at these rates. This is documented, bounded, and applies to
  4,987 records across the Apr–Jul window. From 2026-08-05 the decision log records
  `screening_input_tokens` directly, so new records never use the back-solve
  and the legacy table can be deleted once pre-2026-08 records leave the
  analysis window.

### Not corrected here

- **Failed-but-billed calls.** `compute_api_cost_summary*` count only records
  with `api_success = true`, but a call that fails after the provider has
  metered it is still billed. Over Apr–Jul that is **206 records worth $7.53**
  of real spend that the summaries do not report. This is pre-existing
  inclusion behaviour, unchanged by this correction and left unchanged
  deliberately — altering it would also move call counts and cost-per-trade.
  The reconciliation figures above **do** include these records, which is why
  the summaries total $329.79 against the reconciliation's $337.31 for the same
  window. Flagged for a ruling.
- **Daily-report archive.** The `.md` cost lines in published daily reports
  from April onward were rendered with the old rates and are not patched. That
  surface is non-reproducible by design; this register is the disclosure
  instrument for it.

---

## 2026-08-19 — July 2026 consolidated corrections record

**Scope.** One record for every correction touching the July 2026 reporting
cycle. Two items are corrections; a third is recorded here explicitly as a
**non-correction**, so that its absence from the register is a stated finding
rather than an omission.

**A scoping note, because the dispatch that ordered this record named three July
items.** The SPY-anchor erratum is **not** one of them — it is a **May 2026**
correction, published 2026-07-03 (`4c9c5e93`), and it appears below only as the
convention July rev. 2 followed. See *Cross-references*. A separate, still-open
SPY matter (the `canonical_spy_return` retroactive drift) is likewise not a July
correction and is recorded there for the same reason.

---

### Item 1 — Notable-decisions extractor omitted short-side executions

**Published as July 2026 rev. 2**, 2026-08-13 (`ea9bf1ea`); erratum landed
2026-08-10 in `cf8c0488` as `corrections.notable_events_long_only_extractor`.

**Mechanism.** The notable-decisions extractor filtered executions on a
`BUY`/`SELL` literal. That predicate was written when the universe of executed
sides *was* BUY and SELL; when shorting activated 2026-07-01 it silently became a
long-only filter, dropping every `SHORT` and `COVER` across 74 short-side
executions from July 1 until correction. The panel header encoded the defect in
its own title — *"LARGEST LONG-LEG EXECUTIONS"* — which is why the rev. 1
disclosure read as a design statement rather than a bug.

**Corrected figures.** Two entries enter at #2, displacing two others; two demote
#2 → #3:

| Model | Entering at #2 | Displacing |
|---|---|---|
| Gemini | COVER PG **$16,505.28** (7/30) | SELL SLB $13,020.15 |
| DeepSeek | COVER QCOM **$9,412.04** (7/9) | SELL GLD $7,903.32 |

All twelve reported entries reconcile to the cent. The panel header is corrected
to "TOP TRADES BY VALUE", and the rev. 1 "long-leg only by extractor design"
disclosure is **quoted and repudiated** rather than silently dropped.

**What it did not touch.** No returns, metrics, gates, or RQ inputs. Leaderboard
unmoved (Gemini 12.62/+7.58, DeepSeek 3.57/−1.47); page count unchanged at five.
Rev. 1 is preserved on disk unchanged, not replaced.

**Defect class.** Member of the long-only BUY/SELL filter class — the same class
as Item 2, and as the 2026-07-01 daily-report render bug. The rule ratified from
this item: express tripwires in **rendered** form, and validate them against a
known-positive control.

---

### Item 2 — RQ2 July pooled disposition figure is a hybrid, not a long-segment estimate

**Not previously published as a correction.** Affected figure:
`reports/monthly/2026-07/data_layer.json` →
`methodology_data_integrity_rq.rq_update.point_estimates.RQ2.pooled_disposition_difference`.
Technical anchor: **`docs/RQ2-paper-leg-contamination.md`**, carrying the full
reproduction, the per-model decomposition, and the builder hazard.

**Mechanism.** The estimator's two legs were scoped differently. The realized leg
(numerator) counted long realizations only — a side effect of the same long-only
BUY/SELL vocabulary as Item 1. The paper leg (denominator) read every open
holding from the `portfolio_after` snapshot **regardless of direction**, so short
positions sat in the denominator of a measure whose numerator could never contain
them. Because `unrealized_pl_pct` already reports relative to direction, the
contamination was **direction-corrected and therefore sign-invisible**: nothing
looked wrong.

**Both values, published together.**

| | Value | What it is |
|---|---|---|
| **−0.1031** | `-0.10308579739847612` | **Pre-fix-code output** — a long realized leg over a direction-blind paper leg. The figure in the published July layer. |
| **−0.1073** | `-0.10730611196712891` | **Clean long-segment estimate** under the registered direction-segmented spec. Supersedes the above. |

Delta **−0.0042** (`-0.00422031456865279`), **4.09% relative**.

**Sign and conclusions are preserved, stated in the same breath as the
correction.** PGR − PLR is negative before and after — losses realized at a
higher rate than gains, the reverse of the classic disposition effect. Rank order
across the six books is unchanged. The published 90% interval is
[−0.11666, −0.08891] at p = 0.0, and the clean estimate **falls inside it**. No
inferential conclusion drawn from the July figure is disturbed.

This is a construct-validity and labelling correction, not a results correction —
which is exactly why it is registered rather than absorbed. The published number
sat inside its own interval while estimating a quantity nobody registered.

**Decomposition.** Realized legs identical (RG 313, RL 345); event count identical
(n = 547). Only the denominators move:

| Denominator | Pre-fix | Clean | Removed | Share of leg |
|---|---|---|---|---|
| Paper gains PG | 6805 | 6767 | 38 | 0.56% |
| Paper losses PL | 2001 | 1932 | 69 | **3.45%** |

The contamination is **asymmetric by roughly six to one** — July's short book sat
disproportionately in the paper-*loss* bucket, diluting PLR far more than PGR, so
the bias ran **toward zero**. The correction makes the cohort's anti-disposition
slightly stronger, not weaker.

**Four of six books are byte-identical** across the correction: claude, gpt, grok
and claude_opus ran no July shorts. The entire pooled shift is carried by Gemini
(−0.0187538) and DeepSeek (−0.0073942). The bias is therefore a function of
*which books shorted*, so the same defect would bias a different month by a
different amount, in a direction that cannot be signed in advance.

**New exploratory output, not a correction.** The July short segment — PGR 0.4444,
PLR 0.4783, difference −0.0338, RG 8, RL 22 — is a first-time measurement,
low-n, v3 regime onward.

**Fixed at source.** `6998c10d` corrected the estimator; `b6e836d5` closed the
builder call site that had silently inherited the change; `a6f75169` named the
segment at every remaining call site.

---

### Item 3 — RQ3 July calibration figure: **not a correction**

Recorded deliberately. A reader comparing RQ2 and RQ3 will ask whether the same
defect touched RQ3. It did not, and the register should answer that rather than
leave it inferable from silence.

The direction-segmented recomputation reproduces the published July value
**exactly**:

```
published   0.19357739190316292    n_closed_trades = 236
recomputed  0.19357739190316292    n_closed_trades = 236
```

Agreement to **15 significant figures**, on an identical trade count. The
registration change is a **relabeling with no numerical correction**.

**Why RQ3 was structurally immune.** Contamination requires a denominator built
from the portfolio snapshot. RQ2 has one; RQ3 has none — it is a per-closed-trade
correlation, and a closed trade is constructed entirely from one segment's own
open/close vocabulary. Pre-fix, `_closed_trades` walked BUY/SELL only, which *is*
the long segment. Shorts were therefore **absent** from RQ3, and absence from a
per-trade estimator is exclusion, not bias.

The July short segment holds **26** separately-closed trades RQ3 never counted
(correlation −0.2037 — the anti-calibration observation). They were not
contaminating anything; they were not yet in scope.

*Provenance note:* the published RQ3 pooled figure already carries
`"status": "superseded_six_model_pool"` and `"reported": false`, superseded by the
per-model GPT/Grok primary. Its exactness is a provenance fact, not a live
headline.

---

### Cross-references — two SPY items, neither a July correction

- **May 2026 SPY-anchor erratum** (`4c9c5e93`, published 2026-07-03). SPY
  since-inception return corrected **11.26% → 11.18%**, and the six derived model
  alphas re-stated; cause was an ambiguous inception anchor, now ledger-pinned at
  **680.40**. It appears here only because July rev. 2 followed its dashboard
  erratum-note convention. It is a May correction and belongs to the May cycle.
- **`canonical_spy_return` retroactive drift** — the upstream cumulative SPY
  return for a *fixed* window was revised **10.32% → 9.69%** after publication.
  This is a data-source drift, **not a correction this project issued**, and no
  July figure moves because of it: the monthly builder is insulated by a frozen
  `spy_benchmark.cumulative_return_since_inception` in `report_meta` (July:
  `0.09792769325690731`). Open for Research; recorded so that the absence of a
  July SPY correction is a finding rather than a gap.

### Where this record is cited

The Tier 2 pre-registration RQ2 entry cites this record jointly with the site-2
output-version ledger entry (marker 9); the RQ3 entry cites it as the
recomputation record (marker 14). The August methods note **cites this record and
does not restate it** — a methods note is not the disclosure instrument for a
superseded published figure.
