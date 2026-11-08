# Weekend Batch — Validator Scope, FILL_OVERRUN, Settled-Cap Clarification, Counterfactual-Record Note

- **Artifact:** `docs/registrations/weekend_batch_2026-11-07.md` — four registrations/confirmations, none holding up the Saturday push. Each item names its splice target where it amends existing text.
- **Ruled:** 2026-11-06 (Research), on the hub's weekend dispatch.

## 1. Validator-drop scope — the hub's interim ruling is CONFIRMED

**"Intended order," for G-EXEC and the no-third-state clause, means a decision that passed the validator.** Validator drops — `DAILY_TRADE_CAP`, `MAX_POSITIONS`, `OFF_UNIVERSE`, `SELL_NOT_HELD`, `PORTFOLIO_HALTED` — are enforcement of the *registered portfolio rules* against the *decision*: the decision was invalid under constraints the model was given, and the drop carries a named reason by construction. They belong to the decision record, not the execution record, and G-EXEC does not score them. This is consistent with the no-third-state clause rather than an exception to it: the clause forbids *untagged* suppression of *intended* orders; a validator drop is tagged (named reason) and precedes intention (the order never became one).

**Splice — Gate 4 cross-reference in `docs/prereg/tier2_novel_sections.md` (landed `c48d83f`), one line appended wherever the no-third-state clause is cited:**

> *For this clause, an "intended order" is a decision that passed the validator; validator drops (`DAILY_TRADE_CAP`, `MAX_POSITIONS`, `OFF_UNIVERSE`, `SELL_NOT_HELD`, `PORTFOLIO_HALTED`) are named-reason decision-record events, precede intention, and are not scored by G-EXEC.*

Two riders: (a) **validator-drop rates are disclosed descriptors per model** — a model that repeatedly issues invalid decisions is a behavioral fact (off-universe picks, selling unheld names) and belongs in the descriptive layer, not hidden in a decision-record field; (b) the validator's reason set is **closed and registered** like the skip classes — a drop without a named reason is an untagged suppression and falls under the no-third-state clause as an execution failure.

## 2. FILL_OVERRUN — registered

> **FILL_OVERRUN (registered exception to partition control 2).** BUYs are submitted as share quantities; a cash-capped order filling above its reference price leaves the book's cash a few cents negative. This is a **cents-scale exception to the per-book settled-cash cap** (partition control 2). Handling: booked truthfully (the book records the overrun; nothing is rounded away); reported `WARNING` when the fill price explains the overrun exactly, `CRITICAL` when it does not — a `CRITICAL` is a G-BOOK event, since an unexplained negative is a reconciliation divergence, not an overrun. **No buffer parameter is added**: a buffer would hide the exception inside a tolerance, and the point of the control is that the exception stays visible. **Shared-fate note:** the overrun is covered by the account's aggregate settled cash, so no venue-level violation is reachable; the exception is internal to the partition and is disclosed as such. **Why share quantities (Operations' reason; hub decision 2026-11-07):** there is no hard technical reason for share quantities. The venue accepts notional BUYs, and the barrier (keyed on ticker and side), the stops (which close held shares), and reconciliation (which compares positions and cash) do not depend on order form. The one coupling is that `requested_qty` derives from the order's quantity, so notional BUYs would require reworking `_parse_order` and the `UNFILLED_AT_DEADLINE` check. **Decision: share quantities are kept through Phase B.** That path has been run end to end by the spike, the probes, and day one; changing order form inside the zero-margin validation window is not worth a cents-scale saving. `FILL_OVERRUN` therefore stays a registered, disclosed exception, reconsiderable only at a registered boundary. The exception carries its cause, not only its handling.

## 3. Tier 2 line 350 — clarifying sentence (splice)

> Purchase orders are **capped at the book's settled balance**: an order whose target exceeds settled cash is sized down to the settled amount and submitted. "Blocked" is the case where the cap is too small to send — the capped amount falls below the venue minimum ($1) or is zero — and a blocked order is a tagged constraint event, never a silent skip.

This also corrects the drafting in the original settled-funds registration, which said "blocked" where the implemented and registered behaviour is "capped, and blocked only below the minimum." The two statements must agree (rule 10); the line-350 text governs after the splice.

## 4. Methodology note — the counterfactual-applied-to-the-record error class

> **Classification follows what happened; counterfactuals annotate.** A record is classified by the behaviour of the system that produced it, never by the behaviour of the system that *should* have produced it. The error is seductive precisely because the counterfactual is true — a correct executor *would* have capped those orders — and the truth of the counterfactual makes it feel like classification rather than speculation. Instances: the executor-floor ruling's first issue (12 suppressed orders classified as constraint events because a correct executor would have emitted them; corrected the same evening to execution failures with the counterfactual as annotation). Prior rules of the same family: the force-cover is disclosed boundary administration, never behaviour; the halt is censoring, never choice. The discipline generalizes: when a defect is corrected, the pre-fix record keeps its pre-fix classification and gains a disclosed annotation of what the corrected system would have done; nothing in the record is reclassified by the fix.

Lands in the data-integrity framework beside the undifferentiated-failure principle and the estimability taxonomy — the three together govern, respectively, how damage becomes knowable, how damaged data is classified, and how the classification is protected from the fix.

## Provenance
- 2026-11-06: hub's interim validator ruling and the three registration requests; Research confirmed/drafted same evening; one live marker (the FILL_OVERRUN cause).
- 2026-11-07 (re-issue for Sunday's registration batch): the FILL_OVERRUN cause filled from Operations' reason and the hub's decision (share quantities kept through Phase B); the intended-order definition added as an exact splice line at the Gate 4 cross-reference (`c48d83f`). **Zero remaining placeholders.**
