# R-4 Notes — Gate A Staging Rulings and the Repair Registration

- **Artifact:** `docs/registrations/r4_notes_gate_a_and_repair.md` — governing text for the items below, until the next revision of `tick_admission_guards_ruling.md` absorbs §1. Zero markers.
- **Recorded:** 2026-11-17 (Research). §1 records rulings the hub made on Operations' Gate A staging; §2 registers the hub's time-to-repair ruling of 2026-11-16 with the repair constraints Research set. Quotes checked at origin `75d643a`.

## 1. Gate A staging — hub rulings, recorded

These refine `tick_admission_guards_ruling.md` rev. 4 §3 ("Precise blocking conditions" and the fail-stop). None changes its class ruling or its four conditions.

1. **A non-UTF-8 row blocks.** The spacing guard blocks on a row that is not valid UTF-8. The decision logs are ASCII-only (`json.dumps` default), so such a byte can only be corruption; it is not a torn write, and rev. 4's allowance for one torn final row with other books readable does not cover it.
2. **All present logs empty blocks.** The files are append-only, so an empty file is damage. A missing file still proceeds — absent remains normal, as demonstration (v) requires.
3. **The fail-stop reads GitHub's run record first.** It answers "clear" only when the "Run pipeline" step provably never started; anything else falls through to the venue. This prevents false disables when GitHub cancels a queued run (16 since 2026-11-02). It adds a second source and never overrides a venue answer of "orders found". Rev. 4 required the determination to come from the venue and to disable when it cannot be made; that stands, with one case carved out — a run whose order-submitting step never began cannot have submitted an order.
4. **One record per withheld tick.** A withheld tick is recorded once, under its `TICK_WITHHELD_*` cause, never also as a generic missed cycle. G-COMPLETE counts it once.
5. **Replay (condition 2), as reported by Operations:** 63 ticks since 2026-11-11 and 178 since 2026-10-29, zero differences.

**Research note on ruling 3.** The carve-out is sound only in one direction, and the ruling keeps it that way: the run record can clear, never convict, and an unrecognised or missing step record falls through to the venue. If the step is ever renamed in the workflow, the check degrades to the venue path, not to "clear".

## 2. Repair — registered constraints

A withhold under `tick_admission_guards_ruling.md` clears only when a person repairs the book. The repair is an administrative act on the record, so its limits are registered.

1. **By order id, never by balance.** The repair appends the specific venue executions to the book, attributed by order id. It never sets a book's cash or holdings to the broker's balances. The reconciliation handler already carries the rule ("Never adjusts a book's own cash or holdings to match the broker.", `src/execution/reconciliation.py:367`), and in a shared account matching balances misattributes across books.
2. **No decision row is written or reconstructed.** The book-tick stays `DECISION_RECORD_LOST`. Classification follows what happened.
3. **The repair is ledgered, and the ledgered record is what accounts for the `cycle_id`** under P1's primary test. Nothing else readmits the book.
4. **Target (hub ruling, 2026-11-16): repaired before the next session's open.**
5. **If the target is exceeded: no manual trades.** A discretionary trade breaks the zero-intervention design and, in a shared account, cannot be attributed to one book. The book stays withheld.
6. **Unprotected time is a disclosed exposure** — on the Go/No-Go sheet and in the Phase B disclosures.

**Two additions by Research, proposed 2026-11-16 and not objected to; the hub ratifies or strikes them at landing.**

- **The estimator for unprotected time.** Per book and per withhold: regular-session hours during which the book's positions went without stop evaluation, in excess of the normal 30-minute cadence. Reported with it: the number of positions whose price crossed their stop level during the interval, and for each, the loss beyond the stop level at the first evaluation after readmission. That last figure is an annotation — what a protected book would have realised — and reclassifies nothing.
- **A prolonged withhold needs no new rule.** Withheld ticks are G-COMPLETE misses; a withhold that outlasts the target flows into the existing completeness gate and the estimability taxonomy.

## Provenance

- 2026-11-16: hub ruled the time-to-repair target, the no-manual-trades rule and the disclosure; Research set the by-order-id and no-reconstruction constraints and proposed the estimator.
- 2026-11-17: hub ruled the five Gate A staging items and asked for them to be recorded; recorded here with the repair registration.
