# RQ2 — paper-leg direction contamination in the published July 2026 figure

Operations finding, stated in full for the record · 2026-08-19 · **open for Research**
· attached to `6998c10d` (*analytics: direction-segmented RQ2/RQ3, gross-weight RQ5,
full-vocabulary RQ6*)

**Status.** This is a characterization of a published Phase A pilot figure, not a
proposed amendment. The correction it describes is already in the code as of
`6998c10d`; what is *not* settled is what happens to the published number, which is
Research's call. Item F of DISPATCH 2.

---

## The finding in one paragraph

The July 2026 pooled RQ2 disposition figure published as **−0.1031** is not an
estimate of the long segment. Its realized leg (numerator) counted long closes only,
while its paper leg (denominator) counted **every open holding regardless of
direction** — so short positions sat in the denominator of a long-only measure. The
clean long-segment estimate over the identical window and identical close-records is
**−0.1073**. The published figure is biased **toward zero** by **0.0042**. Sign,
direction, and significance are unchanged, and the clean estimate falls inside the
published 90% interval; what changes is that the number now measures what its label
says it measures.

---

## 1. The defect

`_replay_avg_cost` yields, per decision record containing at least one
realization-classifiable close, four counts: realized gains (`rg`), realized losses
(`rl`), paper gains (`pg`), paper losses (`pl`). PGR and PLR are then

```
PGR = RG / (RG + PG)        PLR = RL / (RL + PL)        disposition = PGR − PLR
```

The realized counts come from replaying average cost over the segment's own trade
vocabulary. The paper counts come from that record's `portfolio_after` snapshot —
every position still open and not closed in this record.

Before `6998c10d`, the two legs were scoped differently:

| Leg | Source | Pre-fix scope |
|---|---|---|
| Realized (`rg`/`rl`) | executed trades | **BUY/SELL only** — long by construction |
| Paper (`pg`/`pl`) | `portfolio_after.holdings` | **every holding** — long *and* short |

The realized leg was long-only not by design but as a side effect of the long-only
BUY/SELL vocabulary that predates shorting. When shorting went live 2026-07-01, short
positions began appearing in `portfolio_after.holdings` and were counted into `pg`/`pl`
— entering the denominator of a measure whose numerator could never contain them.

`6998c10d` closes it with the segment filter now at `research_metrics.py:535`:

```python
if _holding_segment(h) != segment:
    continue
```

This is a member of the long-only BUY/SELL filter defect class, and it is the mirror
image of the usual member: the ordinary case is a reader that *drops* shorts and
under-reports; here a denominator *keeps* them and over-counts.

Note the paper side needs segmenting but no sign correction —
`Holding.unrealized_pl_pct` already reports relative to direction, so a winning short
reads positive. The defect is membership, not sign.

## 2. Exact reproduction

The pre-fix estimator was re-implemented against the committed decision records and
reproduces the published value **to full float precision**, which is what licenses
every comparison below.

Window 2026-07-01 → 2026-07-31, attribution by sale-date, all six books.
Published layer: `reports/monthly/2026-07/data_layer.json`,
`report_meta.source_commit` = `cf8c0488` (2026-08-10 — pre-`6998c10d`, as expected).

| | n | RG | RL | PG | PL | PGR | PLR | PGR − PLR |
|---|---|---|---|---|---|---|---|---|
| **Published / pre-fix** | 547 | 313 | 345 | 6805 | 2001 | 0.04397 | 0.14706 | **−0.1030858** |
| **Clean long segment** | 547 | 313 | 345 | 6767 | 1932 | 0.04421 | 0.15152 | **−0.1073061** |
| *Short segment (exploratory)* | 29 | 8 | 22 | 10 | 24 | 0.44444 | 0.47826 | *−0.0338164* |

```
published pooled_disposition_difference : -0.10308579739847612
pre-fix re-implementation               : -0.10308579739847612   <- exact match
clean long segment                      : -0.10730611196712891
delta (clean − published)               : -0.00422031456865279
```

**The realized leg is untouched** — RG 313 and RL 345 are identical in both. The event
count is identical at 547, because the same close-records are classified either way.
Only the denominators move.

## 3. Where the contamination sat

| Denominator | Pre-fix | Clean | Removed | Share of leg |
|---|---|---|---|---|
| Paper gains `PG` | 6805 | 6767 | 38 | 0.56% |
| Paper losses `PL` | 2001 | 1932 | 69 | **3.45%** |

The contamination is **asymmetric by a factor of about six**. July's short book sat
disproportionately in the paper-*loss* bucket, so the pre-fix PLR was diluted far more
than the pre-fix PGR. Since disposition is PGR − PLR, understating PLR pulls the
difference upward — toward zero.

Per model, only the two books that actually held shorts in July move:

| Model | Published | Clean | Δ | PG | PL |
|---|---|---|---|---|---|
| claude | −0.0859062 | −0.0859062 | — | 745 → 745 | 179 → 179 |
| gpt | −0.0985649 | −0.0985649 | — | 1635 → 1635 | 395 → 395 |
| **gemini** | −0.0534601 | **−0.0722138** | **−0.0187538** | 653 → 618 | 295 → 244 |
| grok | −0.1285735 | −0.1285735 | — | 2859 → 2859 | 771 → 771 |
| **deepseek** | −0.0719633 | **−0.0793575** | **−0.0073942** | 550 → 547 | 302 → 284 |
| claude_opus | −0.1406872 | −0.1406872 | — | 363 → 363 | 59 → 59 |

Four books are byte-identical because they ran no shorts in July. The entire pooled
shift is carried by Gemini and DeepSeek, and Gemini carries most of it. This is
diagnostic, not incidental: the pooled figure's bias is a function of *which books
shorted*, so the same defect would bias a different month by a different amount and in
a direction that cannot be signed in advance.

## 4. Direction of the effect, stated plainly

PGR − PLR is negative across the whole cohort, both before and after. Negative means
losses are realized at a higher rate than gains — **the reverse of the classic
disposition effect**. The correction makes the cohort's anti-disposition slightly
*stronger*, not weaker.

## 5. What does not change

- **Sign** — negative before and after, every model.
- **Rank order** — unchanged across all six books.
- **Inference** — the published 90% interval is [−0.11666, −0.08891] with p = 0.0. The
  clean point estimate −0.10731 sits comfortably inside it. No inferential conclusion
  drawn from the July figure is disturbed.

The correction is a **labeling and construct-validity** matter, not a results
correction. That is precisely why it should not be quietly absorbed: the published
number is inside its own interval but is an estimate of a quantity nobody registered —
a long numerator over a mixed denominator.

## 6. Why RQ3's July figure is exact, and RQ2's is not

RQ3's published July pooled confidence-outcome correlation is
**0.19357739190316292** on **n = 236** closed trades. Recomputing the long segment
returns **236** closed trades — identical. The figure is exact.

The asymmetry is structural, and worth registering because it predicts which future
estimators are exposed:

> **Contamination requires a denominator built from the portfolio snapshot.** RQ2 has
> one — the paper leg reads `portfolio_after.holdings`, which is direction-mixed.
> RQ3 has none: it is a per-closed-trade correlation, and a closed trade is
> constructed entirely from one segment's own open/close vocabulary. Pre-fix,
> `_closed_trades` walked BUY/SELL only, which *is* the long segment. Shorts were
> therefore **absent** from RQ3, and absence from a per-trade estimator is exclusion,
> not bias.

The July short segment holds **26** separately-closed trades that RQ3 never counted.
They were not contaminating anything; they were simply not yet in scope.

*Aside, recorded for completeness:* the published RQ3 pooled figure already carries
`"status": "superseded_six_model_pool"` and `"reported": false` — superseded by the
per-model GPT/Grok primary because the six-model pool mixes non-estimable and
exploratory books. Its exactness is therefore a provenance fact, not a live headline.

## 7. Live reproducibility hazard — the builder's call site

`scripts/build_monthly_data_layer.py:378` computes the monthly RQ2 block by calling
the helper directly:

```python
ev = [e for e in _replay_avg_cost(full_records.get(key, [])) if _inwin(e["date"])]
```

No segment argument. `_replay_avg_cost` now signs as
`(records, segment: str = "long")`, so this call **silently became the long-segment
estimator** when `6998c10d` landed — without one character changing in the builder.

Two consequences, both live:

1. **Rebuilding July from the current tree yields −0.1073, not the published
   −0.1031**, from the same builder and the same trade logs. Any reproduce-July check
   that assumes byte-stability across this boundary will red, and the cause will not
   be visible in the builder's own diff.
2. **The monthly layer has no direction label.** `_rq2_month` emits `pooled` and
   `per_model` with a `windowing_note` that describes calendar attribution and says
   nothing about direction. Post-fix it publishes long-segment estimates under
   unlabelled keys, and it computes no short segment at all — so the registered
   direction segmentation is not represented in the monthly artifact even though the
   estimator now implements it.

This is the concrete form of the monthly builder's direct-helper-import coupling: the
builder consumes private helpers whose semantics changed underneath it.

## 8. Open for Research

Operations states the finding and stops here. The decisions are Research's:

- Whether the published July −0.1031 is corrected, superseded with a pointer, or left
  standing with a disclosure — noting the registered RQ2 text already declares Phase A
  headline figures to be long-segment estimates, which the published number is not.
- Whether the monthly builder should call the segmented entry point and publish both
  segments with explicit direction labels, rather than an unlabelled default.
- Whether June and earlier months need the same treatment. They do not: shorting went
  live 2026-07-01, so no pre-July month has a short position to contaminate the paper
  leg. July is the first exposed month, and August onward are exposed until the
  builder call site is addressed.
