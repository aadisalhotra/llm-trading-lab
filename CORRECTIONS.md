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
