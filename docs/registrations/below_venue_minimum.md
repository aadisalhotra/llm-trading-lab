# BELOW_VENUE_MINIMUM — Venue Minimum-Order Constraint

- **Artifact:** `docs/registrations/below_venue_minimum.md` — governing text. Committed per the standing convention; relay citations retire upon landing.
- **Ruled:** 2026-09-11 (Research), on the P0 finding that Alpaca rejects sub-$1 notional orders ("cost basis must be >= minimal amount of order 1"), reachable under fractional sizing in a way whole-share sizing never was. **Figures updated** to the confirmed capital parameter (`BOOK_CAPITAL_PHASE_B = $2,000`, 2026-09-2x).

## 1. Registration

> **Venue minimum-order constraint (registered).** The venue rejects orders under $1 notional. Orders computing below the minimum are blocked **pre-submission**, classified `BELOW_VENUE_MINIMUM`, and treated as execution-constraint events under the registered censoring principle (T2.3), by direct analogy with the settled-funds block: the constraint is the venue's, the linkage worked, the order was never sent, the blocked residual enters no behavioral metric, and the book reflects reality at next-tick state feedback (**G-EXEC-successful**).
>
> **Fidelity bound (why this is disclosure, not deformation):** the unexpressed residual is < $1 per order — ≤ 0.05% of NAV at `BOOK_CAPITAL_PHASE_B = $2,000` — more than an order of magnitude below the 1.0pp fidelity criterion that disqualified whole-share sizing. The venue minimum functions as a $1 sizing-granularity floor, registered as such.
>
> **Descriptor:** the per-model `BELOW_VENUE_MINIMUM` rate is a disclosed descriptor (the unfilled-at-deadline pattern: not a gate, but published, so chronic occurrence is visible rather than silently absorbed). The November validation month establishes the baseline rate at the registered capital.
>
> **Parameter coupling:** the reachable rate scales inversely with `BOOK_CAPITAL_PHASE_B` (at the observed ~5.94% median position target, ~$119 intended positions make sub-$1 residuals correspondingly more frequent than at larger books). This constraint is on the capital parameter's floor-check list: below ~$1,500/book, the residual rate re-examines alongside the other small-stakes disclosures.

## 2. Landing instructions

1. Commit at `docs/registrations/below_venue_minimum.md`.
2. The registration lands in the execution-environment section beside the settled-funds constraint; the descriptor joins the disclosed-descriptor set (unfilled-at-deadline; barrier collision rate; wash-reject count).

## 3. Provenance

- 2026-09-11: Operations' P0 classification (pre-submission block, execution-constraint event) accepted by the hub pending Research registration; registered same-day with the fidelity bound and descriptor rule added.
- 2026-09-2x: fidelity bound recomputed at the confirmed `BOOK_CAPITAL_PHASE_B = $2,000` (was ≤ 0.025% of NAV at the superseded $4,000 assumption); the coupling clause absorbed the change with no structural amendment, as designed.
- 2026-10-04: produced as a committed artifact in the relay-only batch (citation-resolution gate).
