# Spike Rulings — Submission-Order Parity, WASH_REJECT_POST_BARRIER, Cross-Book Submission Barrier

- **Artifact:** `docs/registrations/spike_rulings_parity_wash_barrier.md` — governing text. Committed per the standing convention; relay citations retire upon landing.
- **Ruled:** 2026-09-11 (Research), unblocking the P1 design freeze; **§1 and §3 rule 2 corrected 2026-11-04** — the registration corrected toward the implemented per-contention rotation (grounds in §1), on the hub's Class B finding. Validation-month references read **November 2026** per the Phase B boundary amendment (PRE_REGISTRATION.md amendment log, 2026-10-05 entry).

## 1. Submission-order parity (registered sequencing rule — corrected 2026-11-04)

**Who waits at the barrier (the operative parity rule):** when books contend on a symbol-side pair, priority rotates **per contention** — among the contending books, by cycle index `t mod len(contending books)` (barrier.py:70–78). This is **pairwise-exact uniformity**: any contending pair alternates 1:1, cycle by cycle. Under the registered concurrent-submission architecture (the fill-deadline feasibility registration), submission order is meaningful *only* where the barrier serializes, so this per-contention rotation is the entire cross-book parity mechanism.

**Correction, stated plainly (registration corrected toward the implementation):** the prior draft registered a fixed five-book permutation rotated cycle-level by `t mod 5`, with §3 rule 2 deferring who-waits to it. Withdrawn, on three grounds: (i) the permutation itself was never completed — it remained a `[CITE-FILL]`, so the mechanism was never fully registered; (ii) cycle-level submission ordering is vacuous under concurrent submission — uncontended orders submit concurrently and the rule had nothing to act on; (iii) on the registration's own stated goal ("never a fixed victim"), the implemented per-contention rotation is strictly stronger — exact 1:1 for every contending pair versus a (5−g):g split for a pair g apart in the permutation, uniform only in expectation. Nothing was published under either rule; the probe verifies the operative rule (`ordering_as_registered: true`, 2026-11-04).

**Within each book:** sells-first, unchanged — registered as **operational hygiene with its non-economics stated**: under T+1 settled-funds enforcement, sells-first confers no same-cycle buying-power advantage (proceeds are unsettled regardless of submission order). It narrows the same-symbol opposing-exposure window the barrier exists for; collision hygiene applied identically to all books, not a cash-management choice.

## 2. WASH_REJECT_POST_BARRIER — third Gate 4 (G-EXEC) success class

A wash-trade rejection surviving the barrier is **not** a market constraint (not halt/liquidity/buying-power) — it is an artifact of the registered account structure: two of our books colliding in one account. Classification under the categorical-vs-occasional test: the linkage worked (submitted, venue answered deterministically, terminal state, book reconciled) → **execution-successful**, the rejected intention censored per T2.3. But success must not hide it:

- `WASH_REJECT_POST_BARRIER` is a **distinct enumerated class** in G-EXEC's success taxonomy — *fills/partials; legitimate market rejections; structure-constraint rejections* — disclosed separately, never folded into market rejections. **This amends the committed Gate 4 text as an amendment, not a parallel statement** (the taxonomy stays single-sourced).
- Disclosed descriptor with **registered expectation ≈ 0** — the barrier exists to make this class unreachable, as settled-funds enforcement makes free-riding unreachable.
- **Any nonzero count in the November validation month is a barrier defect and feeds the partition branch-gate.** A nonzero Phase B rate triggers incident review under the forced-change apparatus's incident handling.
- Joins the shared-fate disclosure family: cross-book interference exists because the books share one account; correlated across the colliding pair by construction.

## 3. Cross-book submission barrier (registered execution-environment property)

The barrier changes when each book's orders reach the venue relative to its decision timestamp — an execution-environment property in the settled-funds/fill-deadline class, not an implementation detail.

**Mechanism:** opposing-side orders on the same symbol across books are serialized — the rotation-first book submits; the opposing order holds until the first reaches a terminal state or its deadline, then submits. **Scope: opposing sides only**; same-side same-symbol orders across books do not cross and are not barred. Serialization is **pairwise per contested symbol** — the held order waits on its counterpart only, while all uncontested orders proceed concurrently — so barrier engagement adds one segment to the submission chain, not a serial cascade (the composition under which the registered fill-deadline's feasibility condition and this registration are jointly satisfied).

**Three composition rules:**
1. **Deadline semantics:** a barrier-held order's fill-deadline clock (the registered fill-time deadline, execution-environment registration) starts at its *actual* submission; the serialization chain must resolve with reconciliation headroom before the next cycle's state build (worst-case chain length bounded; Operations sizes it against the 30-minute cycle; November's chain-time measurement verifies).
2. **Parity:** *who waits* is determined by the §1 per-contention rotation — any contending pair alternates exactly 1:1; barrier-induced latency is pairwise-uniform, never a fixed victim.
3. **Visibility:** barrier-held orders are tagged `BARRIER_DELAYED` in the decision log; the realized cross-book collision rate is a disclosed descriptor. The 7.7% figure registers as the pre-test exposure *bound*, superseded by the measured rate `[CITE-FILL: the RTH test measurement]` — the test refines the number, not the registration.

## 4. Landing instructions

1. Commit at `docs/registrations/spike_rulings_parity_wash_barrier.md`.
2. Amend the committed G-EXEC text to the three-class success taxonomy (§2), citing this artifact.
3. §1 and §3 land in the execution-environment registration beside settled-funds and the fill deadline.
4. Clear the two `[CITE-FILL]`s by quotation before deposit assembly.

## 5. Provenance

- 2026-09-11: ruled (Research) on the hub's spike dispatch (re-issued; the original never arrived), unblocking P1's design freeze. The fill-deadline feasibility amendment (concurrent submission as a registered architecture property) was ruled separately the same week and cites the barrier's pairwise composition; this artifact does not cite back (one-directional).
- 2026-09-1x–30: modulus corrected 6 → 5 by Operations' sweep when the live cohort resolved to five books; validation month translated October → November by the boundary amendment.
- 2026-10-04: produced as an artifact in the relay-only batch; never landed (Direction 2, 2026-11-04).
- 2026-11-04: Class B gate found the registered rotation and barrier.py:70–78 inequivalent; ruled the code governs (pairwise-exact beats in-expectation on the registration's own goal; the permutation was never completed; cycle-level order is vacuous under concurrent submission). §1 and §3 rule 2 corrected toward the implementation, stated as such. The probe's `ordering_as_registered` check verifies the operative rule.
