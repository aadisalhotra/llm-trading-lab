# PRE-REGISTRATION — TIER 2 (Novel Sections)

**Status:** landed verbatim as delivered by Research via the PI relay, 2026-08-13;
second landing 2026-08-14 (Gate 4 — execution integrity; October-live ruling);
third landing 2026-08-17 (T2.x renumbering; amended prompt declaration; the RQ1/RQ2/RQ3/RQ5
entries; the venue rulings). No text was altered in any landing. Claims verifiable
today were checked against committed sources before landing; the verification record
is in this header. Fourth landing **2026-08-19** (DISPATCH 2): the amended
vendor-specificity clause, ①(e), landed verbatim; Research's corrected RQ2 and
RQ3 scoping texts, B(2), landed verbatim; the Phase B account-structure
registration, ①(g), landed verbatim with its CITE-FILL discharged to D-4; HALT 1
**CLEARED**; broker citations retargeted to item identifiers. **HALT 2 CLEARED** — the
output-version ledger entries landed in the C quarantine window (`2dcc28d9`),
and markers 9 and 11 now have committed targets. See *Verification record → 2026-08-17
landing*.

---

## Section numbering — `T2.x` namespace (collision RESOLVED 2026-08-17)

The 2026-08-13 landing registered a blocking collision: the payload numbered two
sections **§3.9** and **§3.10**, both already occupied by different content in
`docs/PRE_REGISTRATION.md` (`:232` *Phase A data integrity (pilot-window scope)*
and `:256` *Forced-Change / Deprecation Exposure — Confirmatory Model Set*).

Research's ruling, relayed 2026-08-17, resolves it by renumbering into a `T2.x`
namespace — the same device Tier 1 used (`T1.1`–`T1.5`). The committed
`PRE_REGISTRATION.md` §3.9/§3.10 **keep their numbers**; nothing in that file moves.

| New | Section | Was |
|---|---|---|
| **T2.1** | Gemini remediation: two mechanisms, one stabilization sequence | §3.9 |
| **T2.2** | No GA migration target | §3.10 |
| **T2.3** | Censoring principle | *(unnumbered)* |
| **T2.4** | Prompt declaration — the amended two-component text | *(unnumbered)* |
| **T2.5** | Design refusal — calibration feedback loop | *(unnumbered)* |
| **T2.6** | Phase B cohort rule `[SEPTEMBER]` | *(unnumbered)* |

**RQ entries stay unnumbered** — RQ1, RQ2, RQ3, RQ5, RQ6 are cited by RQ label,
not by section number.

**Sections outside the mapping, recorded not invented.** The ruling enumerates
T2.1–T2.6 only. Three landed sections are named nowhere in it — *Registered cost
basis (failed-but-billed ruling)*, *Gate 4 — Execution integrity (live phase)*,
and the *October-live ruling* — plus the venue rulings landing today. They are
left unnumbered rather than assigned numbers Operations invented; if the deposit
needs them numbered, that is Research's call and a one-line follow-up.

---

## Marker index — tracked bracketed inventory

Mechanical count as of the **2026-08-19** landing: **14** bracketed markers in the
payload body — **7** decision-gated, **6** `CITE-FILL`, **1** `cross-ref`. The
count is produced by regex over the text below the `VERBATIM PAYLOAD` rule, not
by hand; references to markers inside prose notes are deliberately written
without bracket syntax so they do not inflate it.

**Two exclusions the raw regex does not make.** A bare bracket sweep below the
rule returns **16**, not 14. The two extra hits are the ledger-path fragments
`"2026-08"` and `0` from `operational_events["2026-08"][0].stabilization_sequence`
quoted in T2.1 — Python subscripts, not markers. Any future recount must subtract
them, or it will report 16 and conclude two markers were added when none were.
Recorded because the index states the count is regex-produced, and the regex as
stated over-counts.

**Corollary the 2026-08-19 landing learned the hard way.** Anything written below
the rule in bracket syntax is scored, including a *resolved* citation and a
verification tag. The account-structure landing note initially carried a bracketed
CITE-FILL reference and a bracketed PRIMARY tag and pushed the raw sweep to 19.
Two rules follow: discharge a CITE-FILL by substituting the citation **without**
brackets (the account-structure block reads `2026-08-17, D-4`, not a bracketed
D-4), and write tag names bare in prose. Bracket syntax below the rule means
"outstanding marker" and nothing else.

### Decision-gated (7) — clear at a dated decision

| # | Marker | Section | Clears at |
|---|---|---|---|
| 1 | `[SEPT 15]` | T2.3 Censoring principle, instance (2) | 2026-09-15 branch decision |
| 2 | `[SEPT 15 — v3 (migration branch) or v4 (cash/long-only branch)]` | T2.4 Prompt declaration | 2026-09-15 branch decision |
| 3 | `[SEPTEMBER: freeze vs modified refresh per the framework …]` | T2.6 Cohort rule | September methodology review |
| 4 | `[SEPTEMBER: cohort-reduction estimator …; within-vendor decomposition basis …]` | RQ1 refinements | September methodology review |
| 5 | `[SEPT 15]` | RQ2 short-segment scope | 2026-09-15 branch decision |
| 6 | `[SEPT 15]` | RQ3 scope (same branch as RQ2) | 2026-09-15 branch decision |
| 7 | `[SEPT 15: Phase-B-confirmatory on the migration branch / Phase-A-exploratory-only on the cash branch]` | RQ5 short-response channels | 2026-09-15 branch decision |

### `CITE-FILL` (6) — clear when the cited artifact exists and is committed

| # | Marker | Section | Fills from |
|---|---|---|---|
| 8 | `[CITE-FILL: committed RQ2 measure + the adjudicated formulation]` | RQ2 estimand | committed RQ2 measure definition |
| 9 | `[CITE-FILL: output-version ledger entry + correction record]` | RQ2 scope and correction | **BOTH NOW EXIST (2026-08-19)** — `operational_events["2026-08"][4]` `rq2_paper_leg_site2_output_version` in `scripts/phase_a_integrity_ledger.json`, + `CORRECTIONS.md` → *July 2026 consolidated corrections record*, Item 2. Fills at the deposit pass. |
| 10 | `[CITE-FILL: committed calibration measure]` | RQ3 estimand | committed RQ3 calibration measure |
| 11 | `[CITE-FILL: committed RQ5 spec + the site-3 fix ledger entry]` | RQ5 estimand | `docs/RQ5-RQ6-specification.md` + `operational_events["2026-08"][5]` `rq5_gross_weight_site3` — **both now committed (2026-08-19)** |
| 12 | `[CITE-FILL: table]` | RQ6 | API-configuration table, post-fix state |
| 14 | `[CITE-FILL: recomputation record]` | RQ3 scope | the direction-segmented recomputation showing 15-significant-figure agreement — `docs/RQ2-paper-leg-contamination.md` §6 and `tests/test_builder_segment_explicit.py`; the consolidated July corrections record carries it as an explicit non-correction |

### `cross-ref` (1)

| # | Marker | Section | Resolves to |
|---|---|---|---|
| 13 | `[cross-ref: §memory information-set limitation]` | RQ2 Phase A short-side | the registered memory information-set limitation section |

Resolved at landing (not outstanding): the payload's instruction
*"[The landing text quotes the ledger's stabilization-sequence entry verbatim
here.]"* — discharged in T2.1 below by quoting
`operational_events["2026-08"][0].stabilization_sequence` verbatim.

**Count history.** 2026-08-13 landing: 4. 2026-08-14 landing: 4 (neither payload
text carried a bracketed marker). 2026-08-17 landing: 13 — the nine added are
markers 4–11 and 13, all arriving with the four RQ entries and the amended T2.4;
marker 2 was **replaced in place** by the amended prompt declaration's wording
rather than added. **2026-08-19 landing: 14 — one added.** Two sub-landings that
day, counted separately:

- *①(e), the vendor-specificity clause* — **13, unchanged.** The clause carries no
  bracketed marker: its citations are resolved item identifiers (D-2, D-3)
  pointing at a committed artifact, not deferrals. The HALT 1 clearance and the
  B(1) citation retarget added and removed none.
- *①(g), the account-structure registration* — **14, unchanged.** Its one
  CITE-FILL was discharged at landing to D-4 rather than carried, so it never
  entered the inventory.
- *B(2), Research's corrected RQ2/RQ3 scoping texts* — **13 → 14.** Marker **14**
  (`[CITE-FILL: recomputation record]`) is genuinely new, arriving with RQ3's
  relabeling sentence. Two others were **replaced in place, not added**: marker 5
  shortened from the two-branch `[SEPT 15: confirmatory from launch …]` to a bare
  `[SEPT 15]`, and marker 9 widened to
  `[CITE-FILL: output-version ledger entry + correction record]`.

**Marker 9 and the ledger half of marker 11 are no longer blocked.** HALT 2
cleared 2026-08-19 when the C quarantine window landed the seven owed ledger
entries (`2dcc28d9`); Research's site-2 re-ruling arrived as the B(2) scoping
texts, and the consolidated July corrections record is published in
`CORRECTIONS.md`. Both markers now have committed targets and are fill-pending
at the deposit pass rather than blocked.

### Registered consequences (dated obligations, not decision-gated markers)

These are counted separately and deliberately: they are settled rulings carrying
dates, not open branches awaiting a decision. Nothing below clears at a marker.

| # | Consequence | Section | Date |
|---|---|---|---|
| C1 | Venue cutover must be **landed by October 1** — the integration-path rule sets the deadline; a mid-October cutover fragments the final validation segment. If it cannot land by Oct 1, Phase B launches on the venue validated through October, and no validated live path by then is an escalation about the launch itself. | October-live ruling | 2026-10-01 |
| C2 | The venue cutover **does not restart Gemini's validation clock** — the clock gates the model-call configuration (completeness, finish_reason, identity); the venue is downstream of decision generation. Execution-layer changes are gated by Gate 4's October demonstration, not by the model-call clock. | October-live ruling | standing |

C2 governs the interaction between this ruling and
`operational_events["2026-08"].gemini_sdk_migration_2026_08.phase_b_clock_spec`,
whose October segment runs to `clock_end: "2026-10-31"` — i.e. the same October
in which the cutover lands. The ruling resolves that overlap explicitly rather
than leaving it to be discovered at gate time.

**Not included and not owed in this file:** the RQ1 entry (never delivered
inline; requested from Research separately) and the RQ2/RQ3/RQ4/RQ5 entries
(blocked on rulings in flight).

---

## Verification record — claims checked against committed sources

Checked at landing. Every §3.9 figure clears.

| Claim | Committed source | Result |
|---|---|---|
| Completeness 0.568 May | `reports/monthly/2026-05/data_layer.json` → `…per_model_failure_rate.gemini` `records: 257, api_success: 146` (0.5681) | PASS |
| 0.785 June | `reports/monthly/2026-06/…` `records: 270, api_success: 212` (0.7852) | PASS |
| 0.587 July | `reports/monthly/2026-07/…` `records: 283, api_success: 166` (0.5866) | PASS |
| July segmented 0.511 / 0.923 at 2026-07-28 | `…completeness_segmentation.gemini_budget_equalization` — `pre_fix.completeness: 0.5108`, `post_fix.completeness: 0.9231`, `boundary_effective: "2026-07-28"` | PASS |
| Equalized to 16384 effective 2026-07-28 | ledger `operational_events["2026-07"].gemini_budget_equalization` — *"max_output_tokens raised 4096 -> 16384 (landed 2026-07-27, effective from the first tick of 2026-07-28)"* | PASS |
| `google-generativeai` support ended 2025-11-30 | `docs/prereg/tier1_novel_sections.md:26` — *"the deprecated `google-generativeai` SDK (support ended 2025-11-30)"* | PASS |
| Migration landed 2026-08-02T23:38Z | ledger `…cutover.landed: "2026-08-02T23:38:09Z"` | PASS |
| First post-migration success 2026-08-03T14:06:25Z | ledger `…cutover.first_logged_post_migration_success: "2026-08-03T14:06:25.440972Z"` | PASS |
| Clock start = that timestamp; gates through 2026-10-31 | ledger `phase_b_clock_spec.clock_start`, `clock_end: "2026-10-31"`, `gates_per_segment` | PASS |
| §3.10 — preview build, no GA snapshot, alias-only identity | `docs/PRE_REGISTRATION.md:266, 275, 285` | PASS |

**Citation rule observed:** the continuity-diagnostic *close-out* ledger entry is
cited nowhere in this file. At the 2026-08-13 landing it did not exist. It exists
as of 2026-08-14 —
`operational_events["2026-08"].gemini_continuity_diagnostic_close_out`, commit
`002e4988` — and the rule still holds: neither 2026-08-14 payload text cites it.
Citable and cited above: `continuity_diagnostic`, `stabilization_sequence`, and
the Tier 1 document.

### 2026-08-14 landing — cross-reference verification

The two payload texts were checked against the committed gate definitions before
landing. The committed numbering is in Tier 1 §T1.2, `tier1_novel_sections.md:56–60`,
which enumerates the three-gate framework as an ordered list sourced from
`scripts/phase_a_integrity_ledger.json` → `inclusion_gates.gates`
`["completeness", "uncorrupted_book", "model_identity_stable"]`.

| Payload cross-reference | Committed definition | Result |
|---|---|---|
| "a book-integrity event (**Gate 2**)" | §T1.2 ordered item **2** — *"**Uncorrupted book** — ledger `gate_definitions.uncorrupted_book`"* | **MATCH** |
| "Gate 4 joins **Gates 1–3** for live-phase estimability" | §T1.2 items 1–3 — completeness / uncorrupted book / stable model identity; ledger `gates` array, same order | **MATCH** |
| "Threshold: **≥0.80** per model-segment, uniform with the decision-completeness gate" | ledger `inclusion_gates.completeness_min: 0.8`; §T1.2 item 1 *"Decision completeness ≥ 0.80"* | **MATCH** |

**Numbering caveat, registered not reconciled.** The committed sources name the
gates (`completeness`, `uncorrupted_book`, `model_identity_stable`) and present
them in a numbered list; they nowhere use the literal labels "Gate 1", "Gate 2",
or "Gate 3". The payload's ordinal references resolve correctly against the
committed *order*, which is identical in the ledger array and in §T1.2, but that
order is the only thing binding them. A future reordering of either would break
the citation silently. Flagged for Research; not altered here.

**"Gate (d)" — not present.** Neither 2026-08-14 payload text contains the string
"Gate (d)" or any "(d)" reference; there was accordingly nothing to verify. The
label does occur in the committed corpus, at
`docs/broker/broker_selection_research.md` — *"per-broker implementation must be
confirmed in writing (gate d)"* and *"Covers gates (b), (c), (d)"* — but that is
the broker-lane due-diligence namespace, unrelated to the inclusion gates.
Recorded so the absence is a finding rather than an omission.

### 2026-08-17 landing — verification

| Claim landed | Source checked | Result |
|---|---|---|
| RQ1's registered decision set is {BUY, SELL, HOLD} | `docs/PRE_REGISTRATION.md:225` — *"**Decision unit** (RQ1, RQ6) \| A model's `raw_decisions` entry per (tick, ticker): {action ∈ BUY/SELL/HOLD, …}"* | **PASS** |
| RQ1 concordance is 3-way BUY/SELL/HOLD over shared tickers | `docs/PRE_REGISTRATION.md:62–64` — *"3-way BUY/SELL/HOLD **action concordance**"*, shuffled-permutation null, excess = observed − null mean | **PASS** |
| The RQ1 amendment memo exists and is committed | `docs/amendments/rq1_estimand_amendment.md` (present; committed 2026-08-05) | **PASS** |
| §3.9 / §3.10 occupants, for the renumbering rationale | `docs/PRE_REGISTRATION.md:232` *Phase A data integrity (pilot-window scope)*; `:256` *Forced-Change / Deprecation Exposure* | **PASS** |
| Rule 4210 phase-in ends October 20, 2027 | **FINRA Regulatory Notice 26-10 itself**, quoted verbatim in the landed text below. The hub's record said October 20, 2027; the notice says October 20, 2027 — **no discrepancy to flag** | **PASS** |
| Alpaca: implemented 2026-06-04; PDT designation, day-trade counting, and the $25,000 minimum removed | **D-3** — Alpaca Support (Andy), 2026-08-17 09:31 EDT, ticket 336707, at `docs/broker/broker_confirmations_2026-08.md` — *"effective **June 4, 2026**, Alpaca implemented the new intraday margin framework on that date … the pattern-day-trader designation, day-trade counting, and the $25,000 minimum-equity requirement no longer apply on our platform"* | **PASS** |
| IBKR: 2026-08-17 — account not migrated, legacy PDT applies, no migration date given | **D-2** — IBKR Client Services, 2026-08-17, at `docs/broker/broker_confirmations_2026-08.md` — *"Your account has not yet been migrated to the new Intraday Margin standards … We are unable to provide a specific date for when your individual account will be migrated… Because your account has not yet been migrated, the Pattern Day Trading (PDT) rules currently apply"* | **PASS (2026-08-19 — was FAIL; see HALT 1, CLEARED)** |
| Base "split-September registered text" to amend | **Not in the committed corpus.** See HALT 2 note in the payload | **FAIL — landed as operative statement, flagged** |

#### ✅ HALT 1 — CLEARED 2026-08-19 — the IBKR citation in the amended vendor-specificity clause

**Resolution.** The IBKR written confirmation exists. It was delivered to
Operations 2026-08-19 and is landed as **D-1** and **D-2** in
`docs/broker/broker_confirmations_2026-08.md`. The amended clause is landed in
the payload below, verbatim, under DISPATCH 2 ①(e). The verification row above
moves FAIL → PASS.

**Why the 2026-08-17 sweep found nothing, recorded so the earlier finding is not
read as an error.** The IBKR messages arrived through IBKR's **Message Center**,
behind the account holder's login — not by email. Every mail sweep therefore
terminated correctly at the 2026-08-14 18:21 UTC `apiintegration@` message about
OAuth account-type eligibility (F- and Organizational accounts eligible,
U-accounts not), which says nothing about Rule 4210. The sweep was sound; the
channel was simply not one a mailbox search can reach. Both facts stand: on
2026-08-17 the corpus did not contain the citation, and the citation did exist
in a channel not yet swept.

**What the HALT was for.** The clause asserted a dated, in-writing broker
confirmation the record did not then contain, in a corpus bound for OSF deposit
— the same defect class as the phantom 2026-08-05/2026-08-08 ruling citations
corrected in `3dbf1469`. Holding it prevented a second instance of a class this
project had just closed. The clause now cites evidence that is committed and
quotable, and it cites it **by item identifier** (D-2, D-3) rather than by date,
because two IBKR and two Alpaca messages share 2026-08-17 and a bare date is
ambiguous.

**Independent authority, unchanged.** Vendor-specificity of implementation
timing never rested on the broker letters alone: Regulatory Notice 26-10 grants
members an 18-month phase-in to October 20, 2027, verified against
finra.org 2026-08-19 (notice published 2026-04-20, effective 2026-06-04, SEC
Release No. 105226). The broker letters supply the per-venue data points; the
notice supplies the window that makes per-venue variation lawful.

#### ✅ HALT 2 — CLEARED 2026-08-19 — the output-version ledger entries (rule 7 contention)

Dispatch items ② ③ ④ each require an output-version ledger entry in
`scripts/phase_a_integrity_ledger.json`. That path is **staged by the v4 prompt
lane** (blob `d41b8be6`), which is halted awaiting its own correction list.
Project rule 7 forbids staging into a contended file: `git add` produces a
combined blob and a pathspec commit then publishes the other lane's staged
content under this lane's sign-off. Rule 7's two permitted resolutions —
serialize behind the holding lane, or quarantine its change with attribution —
both require a decision Operations cannot take unilaterally, and rule 7 requires
picking **before** editing.

The three code fixes landed without their ledger entries. Marker 9
(`[CITE-FILL: output-version ledger entry]`) and the ledger half of marker 11
stay open until this clears.

**CLEARED 2026-08-19.** Rule 7's second resolution — attributed quarantine — was
authorised by the hub, which holds cross-lane authority; serialization was not
available because the v4 prompt lane is dormant and cannot land before
2026-09-16. The window opened and closed in one commit, `2dcc28d9`, carrying
seven entries at `operational_events["2026-08"][4]`..`[10]`.

The rule-7 discipline was kept end to end: recoverability proven byte-exact
*before* the quarantine (HEAD content plus the guard patch reproduces the v4
lane's staged blob `d41b8be6`), the hunk stashed with attribution naming the v4
prompt lane as owner, the entries written as a surgical text insert (139
insertions, 0 deletions — a json round-trip would have rewritten 255 lines of
unrelated formatting and buried them), and the hunk restored and proven
byte-identical at 17 added lines. The v4 lane's staged set was never altered.

---

# VERBATIM PAYLOAD

T2.1 — Gemini remediation: two mechanisms, one stabilization sequence. The Gemini arm's completeness failures (0.568 May / 0.785 June / 0.587 July; July segmented 0.511 pre-fix / 0.923 post-fix at the 2026-07-28 boundary) had two distinct causes remediated in one documented sequence: (1) effective-budget inequality — thinking and answer tokens share one budget on this provider; equalized to 16384 effective 2026-07-28 under the cap-equity principle, restoring conversion; (2) a deprecated integration path — google-generativeai (support ended 2025-11-30) migrated to google-genai, landed 2026-08-02T23:38Z, first post-migration success 2026-08-03T14:06:25Z, which is the validation-clock start.

> Ledger, `operational_events["2026-08"][0].stabilization_sequence`, verbatim:
>
> "2026-07-28 budget fix (16384, in-situ verified) -> 2026-08-03 original clock start (SUPERSEDED, never used) -> SDK migration boundary (cutover pre-market on sign-off; clean first tick, same discipline as the 7/28 fix)."

Gemini's Phase B entry requires the inclusion gates continuously from the clock start through 2026-10-31; failure routes to the cohort-reduction apparatus.

T2.2 — No GA migration target. As of this registration, Google exposes no generally-available pro-tier text model at gemini-3.x; the pinned preview has no compliant migration target. Consequences registered elsewhere: the cohort rule's Gemini exception, the preview-supersession exposure in the forced-change disclosure, and alias-only identity monitoring.

T2.3 — Censoring principle (registered; cited by the pending ledger registration). Administrative interventions censor behavioral series; they never enter them as behavior. Two instances: (1) Drawdown-halt liquidation: decision series censor at the halt; the flatten is administrative and enters no behavioral metric. (2) v3→v4 boundary force-cover (long-only branch only, [SEPT 15]): all open short positions are covered administratively immediately before v4 activation; covers are tagged boundary-administration in the fill record; their P&L remains in the book, which reflects reality; the closures are excluded from RQ2 realized-outcome classification, turnover, and reversal metrics; no pre-boundary prompt instruction to close shorts is given, as that would contaminate behavior.

T2.4 — Prompt declaration (amended — supersedes the "literal reverse" declaration). The Phase B prompt freezes 2026-09-30 and is frozen for the entire confirmatory window; adapter-level API formatting per model is not prompt content. Identity: [SEPT 15 — v3 (migration branch) or v4 (cash/long-only branch)]. v4 composition (cash branch): two verified diff components, each verified separately. (1) The shorting ablation — produced as the literal reverse of the committed v3 shorting diff and verified mechanically against that diff. (2) The settlement representation — a minimal reviewed addition: the state schema splits cash into settled and unsettled balances, and the prompt carries one neutral sentence stating the settled-funds purchase rule (rule statement only, no strategy guidance). No other semantic content changes; any third diff component fails verification. v4 lands 2026-09-16.

T2.5 — Design refusal — calibration feedback loop (permanently excluded). Feeding models their own calibration history was considered and rejected: it makes RQ3's measured calibration endogenous to the intervention — the study would measure the feedback loop's effect, not the models' calibration. Any calibration-feedback design is a separate future experiment, not a feature of this one.

T2.6 — Cohort rule. [SEPTEMBER: freeze vs modified refresh per the framework — pin date, GA/flagship definitions, October validation gates, mechanical fallback, Gemini exception — ratified at the methodology review and registered here verbatim.]

RQ1 — Cross-model herding (primary). Estimand, null, inference, reporting rule: per the committed amendment memo, docs/amendments/rq1_estimand_amendment.md, incorporated by reference — the null-adjusted excess concordance (observed cross-model action concordance minus the within-tick per-model-shuffle permutation-null mean), permutation p, and the excess-level moving-block BCa interval; observed, null, excess, and p always reported together; raw concordance never appears alone. Scope: the registered decision set is {BUY, SELL, HOLD}; during shorting-active regimes SHORT/COVER decisions are excluded per the registered scope (PRE_REGISTRATION.md:225, :62–64) — cross-model convergence in shorting behavior is outside RQ1's estimand. Construct: convergent security selection under identical information; social imitation excluded by design (see the memo's terminology section). Family: BH-FDR member. Refinements gated: [SEPTEMBER: cohort-reduction estimator on the null-adjusted basis; within-vendor decomposition basis (raw vs null-adjusted pairwise)].

RQ2 — Disposition effect. Estimand: asymmetry in realization of gains versus losses, per the committed measure definition [CITE-FILL: committed RQ2 measure + the adjudicated formulation]. Direction segmentation (primary): long-segment and short-segment disposition estimated separately, outcomes sign-corrected relative to position direction (a short's gain is a price decline); pooled sign-corrected disposition as secondary descriptive. Censoring: administratively closed positions (drawdown-halt liquidations; the v3→v4 boundary force-cover) are excluded from realization classification — disposition measures the model's choice of when to realize; administrative closure is censoring, not choice. Scope and correction: Phase A pilot RQ2 headline figures through July 2026 were produced by pre-fix code whose realized leg counted long realizations only, but whose paper (unrealized) denominators folded in short-holding observations — direction-corrected and therefore sign-invisible. The published July pooled figure (−0.1031) is that hybrid and is superseded by the clean long-segment recomputation under the registered direction-segmented spec: −0.1073 (delta −0.0042; 4.09% relative; sign and conclusions unchanged) [CITE-FILL: output-version ledger entry + correction record]. The July short segment (PGR 0.4444, PLR 0.4783, diff −0.0338; RG 8, RL 22) is a new exploratory low-n output, not a correction. Long-segment confirmatory in Phase B; short-segment per the [SEPT 15] branch. Phase A short-side results additionally carry the memory-context limitation [cross-ref: §memory information-set limitation].

RQ3 — Confidence calibration. Estimand: the mapping from stated per-decision confidence (the v2-rebuilt 1–10 anchored scale, constant within v2+ regimes) to realized decision outcomes, sign-corrected relative to position direction [CITE-FILL: committed calibration measure]. Direction segmentation (primary): long-calibration and short-calibration estimated separately; pooled sign-corrected as secondary. Regime discipline: the confidence scale changed at v2 — no calibration series crosses the v1/v2 boundary. Design refusal cross-ref: no calibration feedback is ever supplied to the models (T2.5) — measured calibration remains exogenous. Scope: Phase A pilot RQ3 headline figures through July 2026 are long-segment estimates — the direction-segmented recomputation reproduces the published values exactly (agreement to 15 significant figures), so the registration is a relabeling with no numerical correction [CITE-FILL: recomputation record]. The July short segment (−0.2037, n = 26 — the anti-calibration observation) is a new exploratory low-n output, subject to the memory-context limitation. Long-segment confirmatory in Phase B; short-segment per the [SEPT 15] branch.

RQ5 — Path-dependent risk under drawdown. Estimand: trade-driven concentration response to drawdown — dHHI_trade (post-trade gross-weight HHI minus pre-trade price-drifted gross-weight HHI) regressed on drawdown depth; gross (absolute) weights throughout, so short exposure counts toward concentration [CITE-FILL: committed RQ5 spec + the site-3 fix ledger entry]. Design: pooled panel, model and tick-position fixed effects; tick-position as the registered cohort-wide covariate; H0: β = 0; moving-block bootstrap, L = round(n^(1/3)) with the floor(L/2)/2L sensitivity check; two-sided percentile p. Auxiliary drawdown-conditional metrics: gross exposure, net exposure, cash share, turnover, and (v3 regimes) short utilization. Censoring: the decision series censors at the 30% drawdown halt; halt semantics (stop-and-flatten, per registered live behavior) govern the book, not the behavioral estimand. Windows: homogeneous regime windows only; the confirmatory window is the frozen Phase B configuration. Scope: short-response channels are v3-regime content — [SEPT 15: Phase-B-confirmatory on the migration branch / Phase-A-exploratory-only on the cash branch].

RQ6 — operational reproducibility (deployed configuration). Characterization RQ, outside the BH-FDR family. Unit: the deployed configuration; every configuration is its own characterization. Metric: Δ_m, per-model run-to-run decision divergence across repeated calls at the deployed configuration. The pipeline sends no temperature parameter to any model, so temperature 0 is off-deployment for the whole cohort; per-model temperature behavior is a disclosed fact in the API-configuration table (honor / ignore / reject — the third class per the 5-family 400-error behavior), not a design basis. The configuration table registers at post-fix state [CITE-FILL: table]. Decision set (site-4 ruling, 2026-08-17): RQ6's decision set is the deployed configuration's full action vocabulary, per configuration — {BUY, SELL, HOLD} under v1/v2 configurations, {BUY, SELL, HOLD, SHORT, COVER} under v3. Δ_m computes over each configuration's own set; cross-configuration comparison is already prohibited, so per-configuration sets differ without inconsistency.

RQ1/RQ6 contrast (registered deliberately, one line): RQ1 excludes SHORT/COVER by registered estimand scope; RQ6 includes them by unit definition — different registered constructs, both explicit.

Registered cost basis (failed-but-billed ruling). The registered cost basis includes failed-but-billed spend; the inclusion is registered at the deposit, not restated mid-phase. Substantive ground: excluding failure costs systematically flatters unreliable models — failed calls were real spend incurred producing the decision stream, and a cost-per-decision that drops failed spend from the numerator makes the least reliable model look cheapest per decision. Basis: total billed inference spend (successful + failed) in the numerator; completed decisions in the denominator; failed-but-billed additionally broken out as its own disclosed line. Published Apr–Jul pilot totals stand as published under the three-total labeling ($821.04 logged-stale / $337.31 reconciliation / $329.79 summaries); the $7.53 is disclosed in reporting from August onward; the inclusive basis governs the paper and Phase B from the registration.

Gate 4 — Execution integrity (live phase). For live-phase segments, each model-month's execution success rate is the fraction of decision cycles whose intended orders were (i) successfully submitted to the venue and (ii) resolved to a terminal, broker-reconciled state — filled, partially filled, or rejected by a legitimate market constraint (halt, liquidity, buying-power), with the book reconciled to broker-authoritative positions and cash. A cycle fails execution when orders cannot be submitted (venue/API/auth failure), order status is unresolved, or post-cycle reconciliation diverges from broker state; a reconciliation divergence is additionally a book-integrity event (Gate 2). Legitimate market rejections are execution-successful — the linkage worked; they are logged as execution-constraint events per the registered censoring principle. Threshold: ≥0.80 per model-segment, uniform with the decision-completeness gate. Relationship to the inclusion gates: Gate 4 joins Gates 1–3 for live-phase estimability; it is inapplicable to paper-phase segments (simulated fills; trivially 1.0) and is registered as live-phase-scoped. A segment failing only Gate 4 is classified decision-estimable (execution-impaired): decision-level estimands (RQ1 concordance, RQ6) remain estimable with disclosure, while outcome-dependent estimands (RQ2/RQ3 outcome legs, RQ5's realized paths, all performance figures) are non-estimable for the segment — decisions were observed; their portfolio consequences were not. Correlation disclosure: execution failures are typically venue-wide and simultaneous across all six books (a broker outage is one event, not six); per-model rates are registered but their cross-book correlation is disclosed, and any venue-wide failure day is an incident-handling event regardless of monthly rates. October obligation: the validation month must demonstrate the execution-success measurement itself — the metric computes, the reconciliation check runs, and a deliberately induced submission failure is correctly classified (a test of the gate, not just of the venue).

October-live ruling. October validates the live configuration — specifically, the live execution path in the venue's broker-paper mode — and live capital begins November 1, never earlier. The execution path (venue, adapter, order lifecycle, reconciliation) is configuration and must be validated in October; real capital is the registered phase boundary and is not a validation variable. Broker paper mode on the actual venue exercises the true order lifecycle — submission, fills, rejections, reconciliation, the Gate 4 machinery — with zero capital at risk. The venue cutover is an integration-path change, and the integration-path rule sets its deadline: landed by October 1. A mid-October cutover would fragment the final validation segment and leave Phase B launching on a path validated for two weeks or less. So: cutover by Oct 1 → October validates refreshed cohort × frozen prompt × live venue path in paper mode → Nov 1 live capital. If the cutover cannot land by Oct 1, Phase B launches on the venue that was validated through October — and if no live path is validated by then, that is an escalation about the launch itself, not a thing October quietly absorbs. Clarification: the venue cutover does not restart Gemini's validation clock. The clock gates the model-call configuration (completeness, finish_reason, identity); the venue is downstream of decision generation. Execution-layer changes are gated by Gate 4's October demonstration, not by the model-call clock.

Settled-funds execution constraint (cash-account branch). Phase B books are cash accounts; sale proceeds settle T+1 and are not deployable until settled. Enforcement is pipeline-level: each book carries a settled-cash ledger, and purchase orders are capped at the settled balance — a purchase exceeding settled funds is blocked at submission, never sent, and logged as an execution-constraint event (G-EXEC-successful: the linkage worked; the constraint is the venue's, handled under the registered censoring principle — the blocked intention enters no behavioral outcome metric, and the book reflects reality at the next tick's state feedback). Free-riding is structurally impossible under this enforcement, which is the point: the constraint is enforced before the venue could observe a violation, so the 90-day-restriction class is unreachable. Effect on the opportunity set: a book that sells at cycle N cannot redeploy those proceeds until the next trading day — redeployment latency is a registered property of the live execution environment. Comparability caveat (registered, beyond the capital-base disclosure): paper-phase fills settle instantly and redeployment is unconstrained; live-phase turnover, cash share, reversal behavior, and RQ5 drawdown responses are shaped by T+1 redeployment latency and are not directly comparable to paper-phase values. The settlement constraint is part of the Phase B regime definition, disclosed as such.

Margin-under-legacy-PDT — disqualified as a Phase B venue shape. A day-trade budget is not registrable as a benign execution constraint in the fill-time-cap class, because it cannot coexist with unconditional stops; it is disqualifying for the venue class, not a parameter of it. The design cannot surrender the unconditional stop: the 15% position stop must be able to close any position at any time, because a tail-risk control that can be blocked is not a risk control. On a legacy-PDT margin venue that unconditionality is unachievable, and the failure is structural, not probabilistic — a stop firing on a same-day open with the 3-in-5 budget exhausted forces either blocking the stop (real capital bleeding with the safety disabled) or executing it (PDT breach → 90-day restriction → the book is dead for the confirmatory window). Exempting stops from a pipeline budget does not help; the venue counts them regardless of our labels. The only collision-free alternative — blocking same-day opens as the budget depletes — dynamically deforms each book's action space as a function of its own trade history, an effective-parity violation across books and across time. Margin-under-legacy-PDT at feasible capital is therefore ruled out. Empirical hit-rate context is wanted but cannot reverse the ruling: the argument lives in the tail, and "the risk control usually isn't blocked" is not a property a registered design can carry. The cash branch dissolves the problem — PDT is a margin-account rule, so on cash accounts with purchases made from settled funds only, a same-day stop-close of a same-day open is legal and stops stay unconditional. Recorded from the same review: there is no cross-account aggregation under legacy PDT, so the six-book independence requirement is not threatened by PDT counters on any branch; the post-migration IMD shared-fate coupling goes on the forced-change watch as a dated future regime exposure.

Forced-change contingency — third trigger type (venue-regulatory). The cash branch is primary; the cadence amendment is rejected as both worse and ineffective. The cash branch adds a disclosed execution constraint while leaving the decision-generating process untouched: 30-minute cycles, the registered action set, and the full estimand structure of RQ1/RQ5/RQ6 all survive intact, with T+1 latency entering as a registered environment property. A cadence amendment changes the DGP itself — RQ1's tick structure, RQ5's panel, RQ6's decision unit — weeks before deposit, the deepest possible class of amendment; and it does not even clear the constraint, since daily cadence on margin still permits only three same-session round trips per rolling five days, so the stop-collision survives, and only near-weekly cadence truly evades PDT, which guts the study's longitudinal resolution. This is registered under the forced-change contingency as its third trigger type — venue-regulatory — alongside provider-forced (DeepSeek) and quality-driven (Gemini): the trigger documented with dated, in-writing broker confirmations; the considered-and-rejected alternatives (margin-with-budget; cadence amendment); and the minimal chosen response (execution constraint, design intact). The contingency's trigger-agnosticism now spans three independent trigger classes with the same apparatus, and goes into Tier 1's T1.1 as the third worked example when this branch resolves.

> **Regulatory authority, quoted from the notice itself.** FINRA *Regulatory Notice 26-10*, "FINRA Adopts New Intraday Margin Standards to Replace the Day Trading Margin Requirements", published April 20, 2026:
>
> "The effective date of the amendments is June 4, 2026, 45 days from publication of this Notice. Members that need more time to implement the rule change will be permitted to phase in their implementation over a period of 18 months, until October 20, 2027."

Rule 4210's amendments bind all US broker-dealers, but implementation timing is vendor-specific within the phase-in window (Notice 26-10, published 2026-04-20, effective 2026-06-04, 18-month phase-in to 2027-10-20); documented in writing on the same day from both candidate venues: IBKR (2026-08-17, D-2 — account not migrated, legacy PDT applies, no migration date provided) and Alpaca (2026-08-17, D-3 — the venue's written representation that it implemented on 2026-06-04; PDT designation, day-trade counting, and the $25,000 minimum removed). One effective date plus an 18-month phase-in makes an early implementer and a still-migrating member both compliant; the postures are not inconsistent. The constraint is therefore assessed per-venue, per-date.

**Landing note on the vendor-specificity clause.** The paragraph above is the amended clause, landed verbatim 2026-08-19 under DISPATCH 2 ①(e). It replaces both the payload's original *"industry-wide during the phase-in, not vendor-specific"* wording (falsified) and the 2026-08-17 HALT note that stood here in its place. Its `D-` citations resolve to `docs/broker/broker_confirmations_2026-08.md`, which is the item-identified record of all four 2026-08-17 broker messages; per that dispatch's ruling ①, broker correspondence is cited by item identifier and never by date alone, because two IBKR messages and two Alpaca messages share 2026-08-17. The IBKR citation that HALT 1 could not verify is D-1/D-2 in that file — see *Verification record → 2026-08-17 landing → HALT 1 (CLEARED)*.

Split-September amendment — the v4 segment's registered function (seam ruling, 2026-08-17). The simulator enforces settled-cash from September 16, aligning the settlement boundary with the prompt boundary. Three grounds: prompt-state-behavior coherence from v4's first tick (deferring enforcement would have the prompt state a constraint that never binds for ten days — truthful but vacuous, and the smoke segment would verify a v4 that behaves differently from the v4 Phase B runs); seam count (deferral creates a third micro-regime, v4-unbound Sept 16–30 vs v4-bound Oct 1+, with different effective opportunity sets, stacked onto an already-split September — enforcing at the boundary gives one regime line, not two); and validation load (the settled-cash ledger must be built for the live pipeline regardless, so enforcing it in paper mode from Sept 16 surfaces its bugs in the smoke segment rather than during October's heaviest-ever validation month — October validates, it does not debut). Accordingly the v4 segment's registered function now reads: **"operational verification of the ablation and the settled-funds enforcement (schema validity, decision completeness, no shorting vocabulary emitted, settled-cash capping observed to bind correctly)."**

**⚠ Landing note on the split-September amendment.** The base "split-September registered text" that this sentence amends is **not present in the committed corpus** — no `docs/` file registers a September split (verified 2026-08-17 by exhaustive grep; the only September-segment content committed is the SEPT-15 marker inside T2.4 and the SEPTEMBER markers in T2.6 and Tier 1 — named here without bracket syntax so the marker recount does not score this note as marker instances). The amended function statement is therefore landed here as the **operative statement**, not as an edit to committed text. If the base registration exists upstream in the Research/hub corpus, it must be landed for this amendment to have the antecedent it names. Recorded rather than reconciled.

Phase B account structure — cash branch (registered). Phase B operates six software-segregated books on a single Alpaca cash account — the venue's recommended architecture for this pattern (written confirmation, 2026-08-17, D-4; fractionals confirmed default-on for cash accounts, same message). Broker-level multi-book structures were confirmed closed on this venue, all paths, in writing (same date). The partition is pipeline-enforced and registered as follows.

Partition controls. (1) Order-level book attribution: every order carries its book identity (client_order_id); no untagged order is submitted. (2) Per-book settled-cash ledgers: each book's purchases are capped at its own settled balance pre-submission; a book cannot deploy another book's cash even though the venue would permit it. (3) Two-level conservation audit: the conservation replay runs per book against the pipeline ledger, and the sum of book positions and cash reconciles to broker-authoritative account state each cycle; divergence at either level is a G-BOOK event. (4) Corporate-action allocation: account-level cash credits (e.g., dividends) allocate pro-rata by record-date book holdings; per-book research P&L is pipeline-ledger-based — broker tax-lot accounting is account-level, belongs to the account owner, and is never a research input. (5) Shared-capacity disclosure: the account's API rate limit spans all six books' order flow — symmetric across books, sized by Operations, exercised at full six-book load in October.

Shared-restriction-fate disclosure. Restriction status is account-level: a venue-imposed restriction would bind all six books simultaneously — the one channel software cannot partition. Reachability: the primary trigger class (free-riding) is structurally unreachable under the pre-submission settled-funds enforcement, which blocks a violating order before the venue could observe it; residual reachability is contingent on a pipeline defect in that enforcement. Any restriction event is a cohort-wide incident handled under the forced-change apparatus, disclosed as correlated across books by construction.

Fee-attribution rule (registered before first incurrence). Per-book costs are deterministic and order-anchored: order-attached fees (SEC, TAF) attribute via the order's book tag; commissions are zero on this venue; any account-level fee not attached to an order allocates pro-rata by book equity at assessment date. No allocation judgment is applied after costs are observed.

G-EXEC and reconciliation semantics (single-account form). G-EXEC evaluates per book over the book's tagged orders: submission success, terminal broker-reconciled state, and the book's ledger reconciling within the two-level audit. A venue-wide failure (account-level outage, auth failure) is one event correlated across all six books and is disclosed as such; per-book G-EXEC rates are registered with that correlation stated.

Validation gate. The October validation month must adversarially demonstrate the partition: a test order for one book exceeding that book's settled cash but within account-level cash must block pre-submission; the per-book and aggregate reconciliations must run clean at full six-book load for the month. Partition validation is a branch-gate for Phase B launch on this structure, at equal standing with the G-EXEC induced-failure demonstration.

R3 amendment (documented as an amendment, with its rationale). Per-book cost attribution must be deterministic and order-anchored — computable mechanically from tagged orders and registered rules, with no judgment applied after costs are observed. This preserves the requirement's purpose (per-model cost figures never depending on a discretionary allocation model) while generalizing its means from "flowing natively."

**Landing note on the account-structure registration (①(g), hub DISPATCH 2, 2026-08-19).** Landed verbatim as delivered in Payload 3; Payload 3's cover-reasoning paragraphs were Research's and did not land. Two things are recorded here rather than altered in the text above.

**1. The CITE-FILL marker is discharged, not removed.** It resolved to **D-4** in `docs/broker/broker_confirmations_2026-08.md`, quote-verified before filling: the recommended architecture is *"The practical path for your architecture: run a single live account and handle the six-book segregation in your own application layer — per-book cash/position attribution and order tagging client-side, under one set of API keys"*, and the fractionals clause is *"Fractional trading is supported for US-listed equities and ETFs on cash accounts (on by default — it's not margin-only)"* — the same message, as the registration states. That artifact lands in the same push package as this landing; until that package is pushed the citation is committed-pending, not committed.

**2. "All paths" spans three grounds, and the correspondence supplies only two.** The registration states broker-level multi-book structures were *"confirmed closed on this venue, all paths, in writing (same date)."* The claim is correct, but its support is not wholly in the 2026-08-17 correspondence:

| Path | Closed by | Source |
|---|---|---|
| Standard Trading API | one live account per retail individual; the partial multi-account rollout reaches only combinations like individual + IRA, *"not the six-book structure you described"* | **D-3** (2026-08-17) |
| Broker API | B2B product; *"not scoped for a single operator running six of their own research books"*, and *"wouldn't clear onboarding without external customers and a compliance program"* | **D-4** (2026-08-17) |
| Separate entity accounts | **capital-infeasible**, not refused: business trading accounts are invite-only beta at a **$30,000 minimum** per entity — roughly a $180,000 floor for six | **`broker_selection_research.md` §3**, tagged PRIMARY there, *not* the 2026-08-17 correspondence |

The entity path is therefore closed on a **separate committed source and on a different kind of ground** — capital, not venue refusal. A reader who takes "in writing (same date)" to cover all three would look for an entity-path refusal in D-3/D-4 and not find one. The registration text stands unaltered; this note records where its third ground actually lives.
