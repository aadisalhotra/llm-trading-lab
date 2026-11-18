# Deposit Package R-2 — Cash-Branch Sentences, Committed Measures for RQ2 and RQ3, the Collision Rate

- **Artifact:** `docs/registrations/deposit_R2_branch_and_measure_resolutions.md` — lands as **provenance**, as the Chain 3 file does: the splices into `docs/prereg/tier2_novel_sections.md` and `docs/PRE_REGISTRATION.md` are the governing act, and after them those documents govern. This file records what was spliced and why. The exact anchors travel in a companion splice file that is not committed, because anchors necessarily quote the placeholders they remove.
- **Ruled:** 2026-11-16 (Research). Census items #39, #41, #43, #45, #47, #60 and R6 (`deposit_census_chain5_2026-11-14.md`, `0a9f55a698d18914`). Every anchor in the splice file occurs exactly once in its target at origin `71d025f`; every quoted string here was checked against that tree.

## 1. The cash-branch sentences (#41, #45, #47)

**The decision fact is mechanical.** Ledger `operational_events["2026-10"]` → `branch_decision_cash_long_only_v4` (decided 2026-10-15, recorded 2026-10-23): "Phase B branch: Alpaca cash account, long-only, prompt v4." Each two-branch placeholder therefore resolves to its cash-branch reading, **Phase-A-exploratory-only**, and each resolved sentence says why Phase B cannot add to the short-segment record: the frozen v4 prompt offers no SHORT or COVER, and no short can execute in a cash account.

**One consequence stated in the RQ5 sentence, because it changes what is reported:** with no short exposure in Phase B, gross and net exposure coincide and short utilization is identically zero. The short-utilization auxiliary is a v3-regime quantity only. Reporting it for Phase B would publish a column of zeros as if it were a measurement.

## 2. The committed measures (#39, #43)

**Ruling: the prose definition governs; the code is cited as its implementation.** The same precedence the `v1.json` ruling fixed (prose over its machine-readable derivation) applies to prose over code. The governing definitions are the RQ2 and RQ3 entries of `docs/PRE_REGISTRATION.md` — the deposited, timestamped document — as modified by rulings that are themselves committed. The code is named by function, not by line: line numbers drift (`compute_rq2` is at `:623` and `compute_rq3` at `:970` today) and the deposit commit binds the version.

**"The adjudicated formulation" has no committed referent.** The phrase was Research's, written into the marker in August; nothing in the repository records an adjudication under that name. The replacement therefore names the formulation choices that *are* committed, each with its source, instead of pointing at an adjudication a reader could not find: for RQ2, the transaction-level construction in which partial trims count; direction segmentation with sign correction (registered 2026-08-17); censoring of administrative closes (T2.3); the behavioural-position floor (`ghost_position_ruling.md` §⑤).

## 3. A rule-10 conflict the census did not list — the closed-trade threshold

Naming RQ3's measure exposes it. `docs/PRE_REGISTRATION.md` registers a closed trade by a share constant in two places — the RQ3 Operationalization ("when a SELL takes residual shares below 0.01") and §3.7 ("residual shares < 0.01") — and `data/pre_registration/v1.json` repeats it at `:94` and `:231`. `ghost_position_ruling.md` §⑤, landed, replaces that constant: "a *behavioural position* is a reconstructed holding with notional ≥ **$1.00**" at the fill price of the fill being processed. The deposited document and a landed registration disagree on the closure rule of a confirmatory estimand.

**Ruled: §⑤ governs; the two `PRE_REGISTRATION.md` sites correct to it** (texts R10-new (a) and (b) in §5); `v1.json` regenerates from the corrected prose under the existing precedence ruling. The corrected Operationalization keeps one disclosure clause: Phase A pilot figures published before §⑤'s output-version recomputation used the 0.01-share constant.

**Open, for Operations:** `src/analytics/research_metrics.py` still carries `SHARES_EPSILON = 0.01` at `71d025f` (`:84`). §⑤'s implementation and its output-version recomputation are already required by the ghost ruling. Until they land, the deposit commit's code disagrees with the deposited definition. That is a pre-deposit item, or the deposit must say in plain text that the implementing change is outstanding and name its ledger entry.

## 4. The collision rate (#60, R6) — month-end ledger fact

Carried by the re-issue of `spike_rulings_parity_wash_barrier.md` (rev. 5), delivered with this file. Three things are fixed there:

1. **The marker converts to a ledger fact.** The registered quantity is November's realized rate, which cannot exist before 2026-11-30. A marker that cannot clear before the deposit either blocks it or invites a premature fill.
2. **The denominator.** Primary: contended cycles over cycles in which at least one venue book submitted an order. A cycle that sends nothing cannot collide, so counting it deflates the rate. Per-book incidence: held orders over submitted orders.
3. **Two sources with a stated boundary.** Before the tag existed, the decision-log reconstruction (0 of 50, preliminary); from `57c8fd8`, the `BARRIER_DELAYED` records.

**Flag:** the 7.7% pre-test bound has no denominator stated anywhere I can read. Rev. 5 requires it to be stated wherever the bound is cited beside the realized rate. If Operations cannot recover it, the bound is cited as a pre-test figure of unrecorded basis, not compared.

## 5. Resolved texts

The exact anchors are in the companion splice file (`deposit_R2_splice_2026-11-16.md`, not committed). Each text below replaces the placeholder or clause at the named site; where a replacement ends mid-sentence, the surrounding sentence continues unchanged.

**#39 — Tier 2 RQ2 entry, the estimand sentence:**

> per the committed measure definition — Odean's PGR − PLR, counted at the close-record level on an average-cost replay of executed trades; the transaction-level construction, in which partial trims count (`docs/PRE_REGISTRATION.md`, RQ2 entry, "Operationalization" and "Metric definition"). Formulation, as ruled and committed: direction-segmented and sign-corrected (this entry, registered 2026-08-17); administrative closes censored (T2.3); positions defined by the behavioural-position floor (`docs/registrations/ghost_position_ruling.md` §⑤). The prose definition governs; its implementation is `compute_rq2`, with `_replay_avg_cost` and `_pgr_plr`, in `src/analytics/research_metrics.py`.

**#41 — Tier 2 RQ2 entry, the closing scope sentence:**

> Long-segment confirmatory in Phase B. Short segment: Phase-A-exploratory-only. The cash/long-only branch was selected (decided 2026-10-15; `scripts/phase_a_integrity_ledger.json`, `operational_events["2026-10"]` → `branch_decision_cash_long_only_v4`): the frozen v4 prompt offers no SHORT or COVER and no short can execute in the cash account, so Phase B yields no short-segment observations; the short-segment record is the v3 regime, 2026-07-01 to the 2026-10-23 boundary. Phase A short-side results additionally carry

**#43 — Tier 2 RQ3 entry, the estimand sentence:**

> sign-corrected relative to position direction. Committed measure: on closed (full-exit) trades, the point-biserial correlation between entry confidence and the binary profitable outcome is the headline statistic, reported with the calibration curve, Brier score and ECE at p̂ = confidence / 10; entry confidence is the share-weighted average over the opens that built the position (`docs/PRE_REGISTRATION.md`, RQ3 entry, "Operationalization" and "Metric definition", and §3.7). A position is closed when the closing fill takes its residual notional below $1.00 at that fill's price (`docs/registrations/ghost_position_ruling.md` §⑤, which replaces the 0.01-share constant). The prose definition governs; its implementation is `compute_rq3`, with `_closed_trades` and `_calibration_stats`, in `src/analytics/research_metrics.py`.

**#45 — Tier 2 RQ3 entry, the closing scope sentence:**

> subject to the memory-context limitation. Long-segment confirmatory in Phase B. Short segment: Phase-A-exploratory-only, on the same branch decision as RQ2 (cash/long-only, decided 2026-10-15; ledger `branch_decision_cash_long_only_v4`); Phase B yields no short-segment calibration observations.

**#47 — Tier 2 RQ5 entry, the closing scope sentence:**

> Scope: short-response channels are v3-regime content — Phase-A-exploratory-only: the cash/long-only branch was selected (decided 2026-10-15; ledger `branch_decision_cash_long_only_v4`). Under the frozen v4 prompt Phase B carries no short exposure, so gross and net exposure coincide and short utilization is identically zero; the short-utilization auxiliary is reported for v3-regime windows only.

**R10-new (a) — `PRE_REGISTRATION.md`, RQ3 Operationalization:**

> when a SELL takes the residual position below $1.00 of notional at that fill's price (the behavioural-position floor, `docs/registrations/ghost_position_ruling.md` §⑤; Phase A pilot figures published before that ruling's output-version recomputation used a 0.01-share constant).

**R10-new (b) — `PRE_REGISTRATION.md` §3.7, the Closed trade row:**

> (residual notional < $1.00 at the closing fill's price; `docs/registrations/ghost_position_ruling.md` §⑤)

## 6. What this leaves for the marker index (R8, Tuesday)

Index rows 5, 6 and 7 (the three branch placeholders), row 8 (the RQ2 citation) and the RQ3 citation's row gain a resolved entry citing the landing commit. Amendment-table row 7 at `:439` still describes the placeholders in bracket syntax and is reworded in the refreshed index.

## Provenance

- 2026-11-14: census assigned #39, #41, #43, #45, #47 and #60 to Research for Monday.
- 2026-11-16: ruled. Branch sentences resolved from the ledger's decision entry; measures named with prose governing and code as implementation; "the adjudicated formulation" found to have no committed referent and replaced by the committed formulation choices; the closed-trade threshold conflict found and ruled to §⑤; the collision rate registered as a month-end ledger fact with its denominator fixed.
