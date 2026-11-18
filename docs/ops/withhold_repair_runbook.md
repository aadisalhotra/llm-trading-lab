# Withhold Repair Runbook

**Governs:** repairing a book withheld, or found unaccounted, under `docs/registrations/tick_admission_guards_ruling.md` (P1, §2 and §4). Constraints are registered in `docs/registrations/r4_notes_gate_a_and_repair.md` §2. This page applies them and adds nothing to them.

**Target: repaired before the next session's open** (hub ruling 2026-11-16). While a book is withheld it evaluates no stops, so time-to-repair is a risk parameter, not a convenience.

## 0. Hard rules (r4_notes §2)

1. **By order id, never by balance.** Append the specific venue executions to the book, attributed by `client_order_id`. Never set a book's cash or holdings to the broker's balances. In a shared account, matching balances misattributes across books (`reconciliation.py`: "Never adjusts a book's own cash or holdings to match the broker.").
2. **No decision row is written or reconstructed.** The book-tick stays `DECISION_RECORD_LOST`. The fills are real and stay in the book. Classification follows what happened.
3. **The ledgered repair record is the only thing that readmits the book.** It is what accounts for the `cycle_id` under P1's id test.
4. **No manual trades of any kind while withheld.** That includes closing or hedging an open position whose stop is suspended. A discretionary trade breaks zero intervention and cannot be attributed to one book.
5. **If the target is missed, the book stays withheld.** Unprotected time is recorded, not acted on.

## 1. Identify, by order id

- Read the page (CRITICAL) and the verdict row: `data/admission/venue_check/{YYYY-MM}.jsonl` for (V), with `would_withhold_class`, `unaccounted[]` and `books_to_repair`; or `data/admission/{YYYY-MM}.jsonl` for a Gate A withhold.
- Run the nightly instrument (`scripts/order_id_set_difference.py`, read-only). It lists every venue order whose book segment has no `cycle_id` in that book's own log.
- For each unaccounted order, record its `client_order_id`, `cycle_id`, side, filled qty, fill price, status and `submitted_at`, from the venue's order record. Do not use positions.

## 2. Repair the book

- For each **filled** order, apply exactly its fill to that book's state file (`data/state/{book}.json`): shares and average cost as the executor would have applied them, cash by the fill notional, proceeds booked as unsettled where settlement is enforced.
- An order that never filled (cancelled, rejected, expired) moves nothing. It is still listed in the repair record.
- An order still **open** at the venue: **proposed, not yet ruled.** Operations' proposal is to cancel it under its own ledger entry, then handle it as above. A cancel submits no order, but it is a venue action, and the registered constraints do not name it. Until the hub rules, page the hub and do nothing at the venue.
- Never touch another book's file, even when the symbol is shared.

## 3. Ledger the repair (what accounts for the `cycle_id`)

1. Add the ledger entry to `scripts/phase_a_integrity_ledger.json`: surgical text insertion with the three proofs (CLAUDE.md rule 8). It names the book, every `cycle_id` and `client_order_id`, the fills applied, and the class (`DECISION_RECORD_LOST`).
2. Append one row per repaired `cycle_id` to `data/admission/repairs.jsonl`:
   `{"book", "cycle_id", "order_ids": [...], "ledger_entry": "<ledger key>", "repaired_utc"}`.
   A row without `ledger_entry` accounts for nothing (`src/logging/venue_check.read_repairs`).
3. Commit the state file, the ledger and `repairs.jsonl` together, on their own sign-off.

## 4. Record unprotected time (the r4_notes estimator)

This applies per book and per withhold:
- regular-session hours during which the book's positions went without stop evaluation, beyond the normal 30-minute cadence;
- the number of positions whose price crossed their stop level during that interval;
- for each of those positions, the loss beyond the stop level at the first evaluation after readmission.

The last figure is an annotation of what a protected book would have realised. It reclassifies nothing. All three go on the Go/No-Go sheet and in the Phase B disclosures.

## 5. Verify on the next tick

- The next tick's verdict row for the book reads `proceed`, with `unaccounted: []`. Its `orders_examined` count must include the repaired orders. This holds because the window does not advance past an order until a repair accounts for it.
- Re-run the set difference: 0 unaccounted.
- If the verdict still withholds, stop. Do not trade around it. Return to §1.

## Gate A (a guard file unreadable)

`TICK_WITHHELD_GUARD_UNREADABLE` withholds every book until the file parses again.

1. Restore the file from its last committed good version, by the file's own git history. Do not hand-edit it.
2. Ledger the restoration.
3. Confirm the next tick proceeds.

At EOD the same withhold suspends only the stop sweep (`scope: "EOD"`). That counts toward §4's unprotected time.
