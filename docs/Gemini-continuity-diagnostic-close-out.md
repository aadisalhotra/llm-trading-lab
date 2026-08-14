# Gemini Continuity Diagnostic — Close-Out

- **Window:** 2026-08-03T14:06:25.440972Z .. 2026-08-14T19:33:19.238389Z — **closed**, 10 trading days
- **Closed:** August 14, 2026
- **Status:** Closed, no divergence. No investigation opened.
- **Closes:** `phase_a_integrity_ledger.operational_events['2026-08'].gemini_sdk_migration_2026_08.continuity_diagnostic`
- **Record of authority:** `operational_events['2026-08'].gemini_continuity_diagnostic_close_out`

This document is prose over that ledger entry. **Every figure below is sourced
from it**; nothing here is computed in this file, and nothing here is carried
forward from the August 13 interim snapshot. Where the two ever disagree, the
ledger entry is correct and this document is stale.

---

## What was being watched, and what it could not decide

The continuity diagnostic opened at the `google-generativeai` → `google-genai`
cutover. Its job was early warning: catch a post-migration regression while it
was still a daily observation, rather than discovering it at gate time. Its
declared course, quoted verbatim from `continuity_diagnostic.schedule`:

> first two trading weeks post-cutover, checked daily (early-warning only, NO
> clock credit)

That parenthetical is the important half, and the close-out entry restates it
rather than letting a clean result blur into a verdict it cannot support:

> This diagnostic yields NO Phase B clock credit. […] Clock credit is decided
> only by `phase_b_clock_spec`, whose gates are evaluated per SEGMENT with
> `no_averaging=true`: the post-migration August segment (clock start ..
> 2026-08-31), September 2026, and October 2026. The 2026-08-03..08-14 window
> is a proper subset of the first of those segments and settles nothing about
> it; the August segment verdict is computed at 2026-08-31 over the full
> segment denominator, and the figures below must not be quoted as that
> verdict.

So: everything below is a clean bill of health for a two-week observation
window. It is not a passed segment. The August segment is decided on August 31,
over its own denominator.

The window closed on a precondition, not on a calendar: August 14 logged
**13 of 13** cycles for all six models, so the final cycle of the window is a
cohort-wide close rather than a partial day.

---

## Headline

| | |
|---|---|
| Decision completeness | **0.95** — 114 successes of 120 cycles |
| MAX_TOKENS failures | **0** (share **0.0**) |
| Model identity | **1** observed version across **120** paired echoes, **0** transitions |
| Residual parse mode | **0.0250**, down from **0.0769** — divergence **NOT TRIGGERED** |
| Mean visible output tokens | 852.3 → 691.6 (**−18.9%**) — attributed to trade composition |

Four of those five are unambiguous. The fifth is the reason this document
exists.

---

## Completeness

120 in-window cycles, 114 successes, **0.95**.

The denominator is logged cycles, never an assumed ticks-per-day — the rule
that keeps an infrastructure outage from reading as a model failure:

| Date | Records | Successes | Completeness |
|---|---|---|---|
| 2026-08-03 | 12 | 10 | 0.8333 |
| 2026-08-04 | 13 | 13 | 1.0 |
| 2026-08-05 | 13 | 11 | 0.8462 |
| 2026-08-06 | 5 | 5 | 1.0 |
| 2026-08-07 | 13 | 13 | 1.0 |
| 2026-08-10 | 13 | 12 | 0.9231 |
| 2026-08-11 | 12 | 12 | 1.0 |
| 2026-08-12 | 13 | 13 | 1.0 |
| 2026-08-13 | 13 | 13 | 1.0 |
| 2026-08-14 | 13 | 12 | 0.9231 |

Three days carry a denominator other than 13, each for a recorded reason.
August 6 contributes **5** — the cohort-wide chain halt logged as
`cycle_gap_2026_08_06`; all five ran and all five succeeded, so the day is 1.0
on a 5-cycle denominator and does not depress the window. August 3 contributes
**12 of its 13**, because one cycle precedes the clock. August 11 contributes
**12** because 12 ran cohort-wide.

**Excluded pre-clock: 1 record, 0 successes** —
`2026-08-03T13:41:57.902537Z`, a failure on the `extra_data` parse mode with
`finish_reason` STOP. It sits outside the window by construction: the clock
starts at the first logged post-migration *success*, so an earlier same-day
cycle cannot be inside it. **Records without a timestamp: 0** — nothing is
unplaceable, so the partition is exhaustive.

### Failures, disaggregated

Six failures. They are not six of the same thing:

| Mode | Count |
|---|---|
| `extra_data` parse | 3 |
| HTTP 503 UNAVAILABLE | 2 |
| HTTP 429 RESOURCE_EXHAUSTED | 1 |

Half are provider-transport errors with no analogue in the pre-cutover baseline
window, which showed 4 failures, all `extra_data`. They are not a model or SDK
signal and are not what the diagnostic was watching. Disaggregating them is the
difference between "the residual mode occurred six times" — false — and what
actually happened.

The `finish_reason` distribution matches: **STOP 117, null 3**. The 117 is 114
successes plus the 3 `extra_data` failures, which return a complete generation
that then fails downstream JSON parsing. The 3 nulls are the transport failures,
which never reach a `finish_reason` at all. **MAX_TOKENS is absent entirely.**

---

## Budget

**0 MAX_TOKENS across 120 cycles, share 0.0.**

The 16384 budget raise and the migration both hold. Worth stating precisely:
the ≤1% ceiling is met with the count *at zero*, not merely under threshold.
That distinction carries weight later — it makes truncation an affirmatively
excluded explanation for the token-composition finding, not just an unobserved
one.

---

## Identity

**120** in-window decision cycles, **120** paired `model_versions` echoes,
**1** distinct observed version (`gemini-3.1-pro-preview`), **0** transitions
detected. `model_id_configured` equals `model_id_returned` on **120/120**.

One methodological note the entry records, because it is a trap: the
clock-start cycle's identity echo is stamped `2026-08-03T14:06:25.436117Z`,
about **4.9ms *before*** the decision record's own `14:06:25.440972Z` stamp.
Pairing by proximity gives 120/120. A naive "echo ≥ clock start" filter drops
that echo and reads **119/120**, which looks like one unverified cycle and is
an artifact of the filter. Echo `api_success` mirrors the decision log exactly
(114 true / 6 false).

---

## The residual parse mode

The mode is the pre-existing `extra_data` trailing-brace failure:
SDK-independent, model-side, budget-independent — `finish_reason` STOP, never
MAX_TOKENS. It was declared as *expected to persist*, with the divergence
signal defined as a **rate** change rather than mere presence.

| | Count | Records | Rate |
|---|---|---|---|
| Baseline (2026-07-28..07-31, legacy SDK at 16384, live) | 4 | 52 | 0.0769 |
| Window | 3 | 120 | 0.0250 |

**Direction: decrease** — −5.19 percentage points, a 67% relative reduction.
Fisher exact two-sided **p = 0.2006** on [4 fail, 48 pass] vs [3 fail, 117
pass]: not distinguishable from sampling noise at any conventional threshold.

**Divergence trigger: NOT TRIGGERED**, on two independent grounds. The
direction is down, and the diagnostic is degradation-facing — it exists to
catch a mode getting *worse* before gate time, not to flag one getting rarer.
And at n=120 the decrease is not separable from noise, so even a
direction-blind reading has nothing to escalate.

One live consequence, recorded rather than waved past: **0.0250 now sits below
the declared ~8–13% band**. The band was cut from legacy-SDK data and is stale
as a forward expectation. It should be re-cut off the post-migration record
when the August segment closes — not left at the legacy figure, where it would
quietly stop being a threshold anything could cross. The three occurrences
carry an identical byte signature (`Extra data: line 31 column 1`), consistent
with the same model-side mode rather than a new failure class.

---

## Output tokens: the finding that reads as a regression and is not one

Mean visible output tokens per successful cycle fell from **852.3** (n=48,
baseline) to **691.6** (n=114, window) — **−160.7, or −18.9%**.

Taken alone that is the shape of a capability or truncation regression at an
SDK boundary. It is neither. It is **trade composition**, and the entry carries
the evidence rather than the assertion.

**The mechanism is a zero-decision-cycle share shift.** A zero-decision cycle
is a successful cycle whose `accepted_decisions` list is empty — a live
no-trade decision, not a failure. Its share of successes went **0.0833 →
0.2807**, up **19.74 percentage points**. Gemini simply chose to trade on far
fewer cycles, and a no-trade cycle is a short cycle.

**Per stratum**, the picture is flat, not collapsing:

| Stratum | Baseline mean (n) | Window mean (n) | Δ |
|---|---|---|---|
| Zero-decision | 308.5 (4) | 278.0 (32) | −30.5 |
| With-decision | 901.7 (44) | 853.0 (82) | −48.7 |

**The decomposition.** Hold the baseline's within-stratum means fixed and
substitute only the window's zero-decision mix: the baseline mean falls
852.3 → **735.2** with no behavioral change assumed anywhere. That accounts for
**−117.1** of the observed −160.7 — **72.9%** of the shift, from composition
alone. The remaining **−43.6** (−5.1% of baseline) is within-stratum, and the
entry declines to attribute it to the SDK: it is small, it is present in both
strata, and it runs against the direction of the discriminating statistic.

**The discriminating statistic.** Tokens per accepted decision moved the *other
way*: **312.3 → 350.4** gross (**+12.2%**), and **335.6 → 344.2** within
decision-bearing cycles alone (**+2.6%**). Per unit of actual decision output
the model is emitting *more*, not fewer, tokens. A truncation or capability
regression would push this number in the same direction as the headline mean.
It goes the opposite way.

**Corroboration.**

- **MAX_TOKENS 0/120.** Truncation is affirmatively excluded, not merely
  unobserved.
- **`thoughts_tokens` populated on 114/114** window successes, mean **4206.2**,
  against **0/48** in the baseline. This is the declared `google-genai`
  telemetry artifact, and it confirms 100% of in-window successes ran the
  migrated path. It also means visible-token comparisons are the *only*
  like-for-like comparison available across this boundary — total generated
  tokens are not comparable, and should not be compared.

**The honest weakness.** The baseline zero-decision stratum is **n=4**. Those
per-stratum baseline means are thin on that side. That is exactly why the
decomposition re-mixes the *baseline* strata rather than the window's, and why
the conclusion does not rest on it alone — tokens-per-accepted-decision and
MAX_TOKENS=0 are independent of that n.

---

## Verdict

Continuity diagnostic **closed, no divergence**. Completeness 0.95 over 120
logged cycles, MAX_TOKENS 0, identity stable at one observed version across 120
paired echoes, the residual parse mode decreased and not significantly, and the
one figure that superficially diverged — mean output tokens, −18.9% — is
attributed to trade composition with the mechanism identified and the
opposite-signed control statistic recorded.

No investigation is opened.

Phase B clock credit is untouched by any of this. The August segment verdict is
computed at 2026-08-31, over the full segment denominator, per
`phase_b_clock_spec` with `no_averaging=true`.

---

## Sources

`data/trades/gemini_2026-08.jsonl`; `data/trades/gemini_2026-07.jsonl`
(baseline 2026-07-28..07-31); `data/model_versions/gemini.jsonl`. Fields:
`output_tokens`, `accepted_decisions`, `api_success`, `thoughts_tokens`,
`api_finish_reason`, `model_id_configured`, `model_id_returned`,
`observed_version`. All figures recomputed at close-out from the closed window
and recorded in
`scripts/phase_a_integrity_ledger.json` →
`operational_events['2026-08'].gemini_continuity_diagnostic_close_out`.
