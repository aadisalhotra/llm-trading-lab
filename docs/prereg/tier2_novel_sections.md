# PRE-REGISTRATION — TIER 2 (Novel Sections)

**Status:** landed verbatim as delivered by Research via the PI relay, 2026-08-13.
No text was altered in landing. Claims verifiable today were checked against
committed sources before landing; the verification record is in this header.

---

## ⚠ BLOCKING — section-number collision, hub ruling required before deposit

The payload numbers two sections **§3.9** and **§3.10**. Both numbers are already
occupied by different content in the committed pre-registration:

| Number | Committed occupant | Tier 2 payload |
|---|---|---|
| 3.9 | `docs/PRE_REGISTRATION.md:232` — *Phase A data integrity (pilot-window scope)* | *Gemini remediation: two mechanisms, one stabilization sequence* |
| 3.10 | `docs/PRE_REGISTRATION.md:256` — *Forced-Change / Deprecation Exposure — Confirmatory Model Set* | *No GA migration target* |

Tier 1 avoided this by numbering in its own namespace (`T1.1`–`T1.5`). Landing
these as `§3.9`/`§3.10` puts two different sections under each number across two
committed files, in a corpus where section numbers are load-bearing citations
and which is bound for OSF deposit.

This is **not** silently reconciled here: the text is landed exactly as
delivered, and the collision is registered as blocking. Resolution — renumber to
a `T2.x` namespace, merge into `PRE_REGISTRATION.md` as replacements, or
something else — is Research's/the hub's call, not Operations'.

---

## Marker index — decision-gated inventory

Mechanical count: **4** decision-gated markers, plus 1 landing-time instruction
resolved below.

| # | Marker | Section | Clears at |
|---|---|---|---|
| 1 | `[SEPT 15]` | Censoring principle, instance (2) | 2026-09-15 branch decision |
| 2 | `[SEPT 15 — v3 (migration branch), or v4 (long-only branch): …]` | Prompt declaration | 2026-09-15 branch decision |
| 3 | `[SEPTEMBER: freeze vs modified refresh per the framework …]` | Cohort rule | September methodology review |
| 4 | `[CITE-FILL: table]` | RQ6 | API-configuration table, post-fix state |

Resolved at landing (not outstanding): the payload's instruction
*"[The landing text quotes the ledger's stabilization-sequence entry verbatim
here.]"* — discharged in §3.9 below by quoting
`operational_events["2026-08"][0].stabilization_sequence` verbatim.

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
cited nowhere in this file — it does not exist. Citable and cited above:
`continuity_diagnostic`, `stabilization_sequence`, and the Tier 1 document.

---

# VERBATIM PAYLOAD

§3.9 — Gemini remediation: two mechanisms, one stabilization sequence. The Gemini arm's completeness failures (0.568 May / 0.785 June / 0.587 July; July segmented 0.511 pre-fix / 0.923 post-fix at the 2026-07-28 boundary) had two distinct causes remediated in one documented sequence: (1) effective-budget inequality — thinking and answer tokens share one budget on this provider; equalized to 16384 effective 2026-07-28 under the cap-equity principle, restoring conversion; (2) a deprecated integration path — google-generativeai (support ended 2025-11-30) migrated to google-genai, landed 2026-08-02T23:38Z, first post-migration success 2026-08-03T14:06:25Z, which is the validation-clock start.

> Ledger, `operational_events["2026-08"][0].stabilization_sequence`, verbatim:
>
> "2026-07-28 budget fix (16384, in-situ verified) -> 2026-08-03 original clock start (SUPERSEDED, never used) -> SDK migration boundary (cutover pre-market on sign-off; clean first tick, same discipline as the 7/28 fix)."

Gemini's Phase B entry requires the inclusion gates continuously from the clock start through 2026-10-31; failure routes to the cohort-reduction apparatus.

§3.10 — No GA migration target. As of this registration, Google exposes no generally-available pro-tier text model at gemini-3.x; the pinned preview has no compliant migration target. Consequences registered elsewhere: the cohort rule's Gemini exception, the preview-supersession exposure in the forced-change disclosure, and alias-only identity monitoring.

Censoring principle (registered; cited by the pending ledger registration). Administrative interventions censor behavioral series; they never enter them as behavior. Two instances: (1) Drawdown-halt liquidation: decision series censor at the halt; the flatten is administrative and enters no behavioral metric. (2) v3→v4 boundary force-cover (long-only branch only, [SEPT 15]): all open short positions are covered administratively immediately before v4 activation; covers are tagged boundary-administration in the fill record; their P&L remains in the book, which reflects reality; the closures are excluded from RQ2 realized-outcome classification, turnover, and reversal metrics; no pre-boundary prompt instruction to close shorts is given, as that would contaminate behavior.

Prompt declaration. The Phase B prompt freezes 2026-09-30 and is frozen for the entire confirmatory window; adapter-level API formatting per model is not prompt content. Identity: [SEPT 15 — v3 (migration branch), or v4 (long-only branch): v4 = v3 minus the shorting-enabling content, produced as the literal reverse of the committed v3 shorting diff, landing 2026-09-16].

Design refusal — calibration feedback loop (permanently excluded). Feeding models their own calibration history was considered and rejected: it makes RQ3's measured calibration endogenous to the intervention — the study would measure the feedback loop's effect, not the models' calibration. Any calibration-feedback design is a separate future experiment, not a feature of this one.

Cohort rule. [SEPTEMBER: freeze vs modified refresh per the framework — pin date, GA/flagship definitions, October validation gates, mechanical fallback, Gemini exception — ratified at the methodology review and registered here verbatim.]

RQ6 — operational reproducibility (deployed configuration). Characterization RQ, outside the BH-FDR family. Unit: the deployed configuration; every configuration is its own characterization. Metric: Δ_m, per-model run-to-run decision divergence across repeated calls at the deployed configuration. The pipeline sends no temperature parameter to any model, so temperature 0 is off-deployment for the whole cohort; per-model temperature behavior is a disclosed fact in the API-configuration table (honor / ignore / reject — the third class per the 5-family 400-error behavior), not a design basis. The configuration table registers at post-fix state [CITE-FILL: table].

Registered cost basis (failed-but-billed ruling). The registered cost basis includes failed-but-billed spend; the inclusion is registered at the deposit, not restated mid-phase. Substantive ground: excluding failure costs systematically flatters unreliable models — failed calls were real spend incurred producing the decision stream, and a cost-per-decision that drops failed spend from the numerator makes the least reliable model look cheapest per decision. Basis: total billed inference spend (successful + failed) in the numerator; completed decisions in the denominator; failed-but-billed additionally broken out as its own disclosed line. Published Apr–Jul pilot totals stand as published under the three-total labeling ($821.04 logged-stale / $337.31 reconciliation / $329.79 summaries); the $7.53 is disclosed in reporting from August onward; the inclusive basis governs the paper and Phase B from the registration.
