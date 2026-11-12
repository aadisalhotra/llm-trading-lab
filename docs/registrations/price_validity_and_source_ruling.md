# Price Validity Guard and Closing-Valuation Source — Rulings (2026-11-11)

- **Artifact:** `docs/registrations/price_validity_and_source_ruling.md` — governing text. Lands with the NaN guard and the data-validity correction of the 2026-11-11 rows.
- **Trigger:** the price vendor returned NaN closes for 2026-11-11; the end-of-day run wrote NaN valuation rows (being replaced); seven false alerts fired.

## 1. Class ruling — data-validity, not executor-class (hub interim ruling confirmed)

Under the registered class boundary (`ghost_position_ruling.md` §①: executor-class = any change that alters the venue order a given decision-and-state would produce), a guard treating NaN or non-finite vendor prices as missing alters no order for finite inputs and refuses to manufacture one from invalid inputs, where no meaningful order ever existed. **Data-validity class; the corrected-executor count is unaffected.** Two scoping conditions, registered:

(a) **The guard sits at ingestion, before the prompt.** A ticker without a finite price in a cycle is not presented as tradable; a decision on it is a validator drop under a new named reason, **`PRICE_UNAVAILABLE`**, added to the closed validator reason set. A NaN that reaches model-facing state is an information-set defect irrespective of execution.

(b) **No third state.** A rejected input emits a classified event, **`MISSING_PRICE`** (per ticker per cycle), counted as a data-availability descriptor — never a silent skip. The alerting path applies the same finiteness check before any threshold test; the seven 2026-11-11 alerts are logged as instrument artifacts (an instrument reading NaN as signal), not as events.

**Correction of the written rows:** the NaN rows are invalid writes replaced by valid rows from the §2 hierarchy — a data-validity correction, ledgered with the replacement source named; not a restatement of a valid figure and never a silent overwrite.

## 2. Closing-valuation source hierarchy (registered)

**Deadline, tied to the row's first consumer:** if the intraday cycle reads row D (for state, sizing, or drawdown-halt evaluation), the row is finalized before D+1 09:35 ET; if its only consumers are end-of-day (the return series, RQ5's drawdown path, the EOD valuation of D+1), it is finalized before the D+1 end-of-day row is written. Operations' dry run of 2026-11-11 establishes which applies and the ledger records it; the stricter bound is not imposed where the reason does not reach.

1. **Primary vendor bar**, retried at the D+1 pre-market build; used if finite.
2. **Venue consolidated (SIP) daily bar** from the venue's market-data API — the official close, not a single-exchange (IEX) last print — for every name in the row and the benchmark. The venue is already the registered authority for positions and cash in reconciliation; its SIP mark is the principled fallback and covers the full universe, not only held positions. Access to SIP history under the account is confirmed by Operations before this branch is relied upon; if SIP history is unavailable, the fallback is escalated before use rather than degraded to IEX.
3. **A stale prior close is prohibited** for any valuation row: it fabricates a flat day in the return series and a false point on the drawdown path.

**Rules that travel with the hierarchy:** *one date, one source* — vendor and venue marks are never mixed within a row; every row carries a **`price_source`** tag (`vendor` | `venue`) and fallback days are a disclosed descriptor; **persisted-canonical holds** — if the vendor later backfills a finite bar, the published row keeps the source it used, and the vendor value is recorded alongside as a vendor–venue divergence descriptor, never swapped in (the SPY determinism defect in another form).

**For 2026-11-11:** retry the vendor at the 2026-11-12 pre-market build; if still NaN, the venue SIP close populates the row, tagged `venue`, with the ledger entry recording the fallback and the seven alert artifacts.

## Provenance
- 2026-11-11 (evening): vendor NaN closes; NaN rows written; seven false alerts; hub interim class ruling.
- 2026-11-11 (re-issue, same evening): deadline tied to the row's first consumer (two branches; dry run decides); venue fallback named as the SIP consolidated daily bar, IEX excluded, SIP access confirmed before reliance. Class-boundary quote verified at origin `50b551e` (`ghost_position_ruling.md:11`).
- 2026-11-11: Research confirmed data-validity class with the ingestion-point and no-third-state conditions (new validator reason `PRICE_UNAVAILABLE`; event class `MISSING_PRICE`); registered the source hierarchy with venue-close fallback, one-date-one-source, the `price_source` tag, and the no-swap rule.
