# Account-Level Fee Allocation — Registered Rule (Phase B)

- **Artifact:** `docs/registrations/account_level_fee_allocation.md` — governing text; amends the fee-attribution rule at `docs/prereg/tier2_novel_sections.md:378` (under ①(g)). Registered before the first Phase B fee is incurred (2026-12-01).
- **Ruled:** 2026-11-07 (Research), on the hub's finding that the venue charges **$0.01 CAT + $0.01 REG per trading day, account-level, not per sell order** (~$5/year). Size is immaterial; partition control (2) makes the rule material.
- **Quoted strings verified against origin `35f4b1c` before issue** (standing rule, 2026-11-07).

## 1. Ruling: activity-independent account fees are charged to the reserve ledger

The daily CAT/REG charges are **charged to the reserve's ledger line**, not allocated across books. Rationale: cost attribution follows causation where causation exists. These fees are caused by the account's existence, not by any book's activity — they accrue on a day no book trades — so they carry zero information about any model, and allocating them would insert the only non-behavioral, non-order-anchored term into per-book net returns. The reserve's registered purpose already covers this: `config/settings.json` `_reserve_master_note` — *"It sits at master level for fees and settlement rounding."* This ruling makes the fee rule and the reserve registration say the same thing.

**Rejected:** *pro-rata by that day's sell notional* — attributes an activity-independent fee by activity, manufacturing a behavioral signal from a flat charge; rejected outright. *Pro-rata by book equity* — deterministic and pre-registered, and the rule `:378` currently applies to this case; §2 states why it is amended for this fee class and retained for the class it fits.

## 2. Why this amendment is legitimate under the no-judgment clause

`:378` reads: *"any account-level fee not attached to an order allocates pro-rata by book equity at assessment date. No allocation judgment is applied after costs are observed."* Paper-mode fees have now been observed, so the amendment must answer that clause directly rather than by relabeling the old rule as "scoped." It is legitimate on three grounds, each necessary:

1. **Timing:** it precedes the first Phase B fee. The clause protects confirmatory costs from post-hoc allocation; no confirmatory fee exists yet, and the rule is fixed before one does.
2. **Driver:** the amendment is driven by the fee's **structure** — flat, daily, independent of any book's activity — not by its effect on any book. The structure was unknown when `:378` was written (it anticipated non-order fees generically); the rule changes because the fee class turned out to be one the equity driver does not fit, not because an allocation outcome was disliked.
3. **Magnitude:** about $0.004 per book per day (~$1/book/year, ~0.05% of a $2,000 book annually) cannot change any result — no estimand, ranking, or disclosed figure moves under either allocation. There is nothing an allocation judgment could be optimizing.

An amendment that fails any one of these would be exactly what the clause forbids. This one passes all three and is recorded as an amendment, with the prior default retained for the class it fits.

## 3. The registered fee taxonomy (three classes, closed)

1. **Order-attached fees** (SEC/TAF on specific sells; any per-order venue charge): attribute via the order's book tag. *(Unchanged from `:378`.)*
2. **Activity-dependent fees not attached to an order** (any future charge proportional to account volume or positions): allocate **pro-rata by book equity at assessment date** — the pre-registered driver, applied before the fee is observed. *(`:378`'s rule, now the rule for this class.)*
3. **Activity-independent account fees** (the daily CAT/REG charges; any flat account-maintenance charge): **charged to the reserve ledger.** *(This ruling.)*

A fee that fits none of the three is escalated before it is booked; it is never allocated by judgment after observation.

## 4. Partition consequences and what Operations builds

- **The build:** the reserve line changes from a **fixed config value into a running balance** that the daily fee debits, inside the level-2 reconciliation. Live: `capital_structure.reserve_master_usd` (500) becomes the opening balance of a reserve ledger; paper: `unallocated_cash_usd` likewise (per the settings note, the live unallocated figure is *derived* from `reserve_master_usd` and the paper figure is the funded balance minus enabled books' starting capital). The fee term debits that running balance each trading day.
- **Two-level conservation:** the reserve is a ledger participant — Σ(book settled cash) + reserve running balance = broker account cash, each cycle. Books' ledgers are untouched by the fee; the aggregate stays exact.
- **Current state, stated so nothing is over-claimed:** the fee term is **not yet built**. Until it is, daily fees are absorbed inside `cash_tolerance_usd` (the settings note's explicit accommodation for fees the pipeline ledger does not model) at about −$0.08 — Monday's reconciliation does not test this ruling; it absorbs the fees in tolerance. The build lands before 2026-12-01 so that Phase B's first fee debits a running reserve balance rather than tolerance.
- **Reserve invariants preserved:** books are never replenished from the reserve; the reserve is in no return denominator (per `_reserve_master_note`); depletion (~$5/year from $500) is monitored, not material.
- **Disclosure in the cost-efficiency section:** account-level fees are reported as a **lab-level operating-cost line** alongside per-model costs — excluded from per-book net returns by this rule, with the daily amount stated so a reader can impute an even split (≈$0.004/book/day) if their question requires it. Per-book net-of-cost figures therefore contain order-attached fees and API spend only, and say so.

## Provenance
- 2026-08: account-structure registration's fee-attribution rule (`docs/prereg/tier2_novel_sections.md:378`): deterministic and order-anchored; non-order fees pro-rata by equity; no allocation judgment after observation.
- 2026-11-07: venue fee structure established (daily account-level CAT/REG); ruled same day — three-class taxonomy, flat account fees to the reserve; the no-judgment clause answered on timing, driver, and magnitude. First issue quoted a reserve-purpose string that did not exist in the repo and cited a nonexistent "T2.7"; corrected same day against origin `35f4b1c` under the new quote-verification rule. Operations builds the running reserve balance and fee term before 2026-12-01.
