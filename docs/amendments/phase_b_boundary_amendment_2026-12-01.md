> **Landing note (hub, 2026-11-04):** §3 cites docs/amendments/prompt_mode_line_removal.md, which never landed; the rule it carried is registered at docs/registrations/v4_verification_rule.md (2026-11-01). Research's text below stays byte-intact.

---

# Phase B Boundary Amendment — Launch Moves to 2026-12-01

- **Artifact:** `docs/amendments/phase_b_boundary_amendment_2026-12-01.md` — the governing text for this amendment. Committed per the standing convention; relay citations retire upon landing.
- **Decided:** 2026-09-30 (hub invocation of S3; Research recompute and date confirmations same date), **pre-deposit** — the boundary was a draft parameter and this amendment class was still available. **Upon the OSF deposit, the boundary binds and this amendment class closes.**
- **Trigger:** the Oct 1 venue-cutover floor was not met and would not be — Level-2 reconciliation unbuilt; no mode-flip activation event; the six-book broker-paper pipeline never run end to end. Invoked under the registered fallback ranking (S2): *shorten within the validation floor, then move the boundary, never compress the build* — calendar is spent before validity; build integrity is never spent. The decision was made deliberately, three weeks before it would have been forced, while S3's answer was still alive.

## 1. The amendment

**The Phase B live boundary moves from 2026-11-01 to 2026-12-01.**

## 2. Recompute list (S3, executed)

1. **Confirmatory window:** slides intact — **2026-12-01 through 2027-12-01** (12 months).
2. **OSF deposit deadline:** becomes **before 2026-12-01**, preserving deposit-before-any-confirmatory-data (November validation is broker-paper, not confirmatory). Target: mid-November, after the pin and freeze resolve, so the deposit carries resolved values rather than placeholders.
3. **Month-18 capstone:** moves to **December 2027**.
4. **Anthropic retirement-floor cushion:** erodes by one month — under refresh, exposure at the 2027-07-24 / 2027-06-30 floors sits ~7.5 months into the window (was ~8.5). Noted, not decisive; the freeze-vs-refresh comparison is unchanged in direction (the 4.6-generation floors would sit ~2.3 months in).
5. **Gemini validated-state continuity:** bridges by **extension, not exception** — the clean-gate requirement runs continuously from the clock start (2026-08-03) **through 2026-11-30** (~3.9 months of validation, up from ~2.9; more evidence, no gap).
6. **SSRN Fall 2027 target:** stands, on ~9 months of confirmatory data at drafting time.

All registered month-anchored machinery translates by exactly one month; **no threshold, gate definition, estimand, or rule changes content.**

## 3. Consequential date translations (confirmed 2026-09-30)

| Item | Old date | New date | Notes |
|---|---|---|---|
| Prompt freeze | 2026-09-30 | **2026-10-31** | Executes on whichever prompt is then current (v4 on the long-only branch, v3 on migration); frozen through validation and the entire confirmatory window. Daily rendered-prompt hash check arms from the freeze date. |
| Cohort pin | 2026-09-26 | **2026-10-26** | Pin-day resolutions re-run against the world as of Oct 26 under the unchanged deterministic definitions, including re-verifying the Gemini no-GA-target fact. The DeepSeek revisit trigger's window extends to the new pin date. |
| Branch decision (venue/shorting) | 2026-09-15 | **2026-10-15** | Character preserved: the venue matrix is complete; resolution remains near-mechanical (Alpaca-cash, software books, long-only, v4) unless the IBKR facts change materially. Same gates, same matrix, later clock. |
| v4 conditional landing | 2026-09-16 | **2026-10-16** | Riders translate intact: settled-cash simulator enforcement from Oct 16 (seam ruling — one regime line); the boundary force-cover of open shorts as a component of the v4 boundary event (long-only branch only); three-component verification per `docs/amendments/prompt_mode_line_removal.md`. |
| Venue cutover floor | Oct 1 target | **Nov 1 target; floor binds ~Nov 9** | The S1 floor is date-relative: ≥15 trading days of live-path validation. November 2026 holds ~20 trading days (Nov 1 is a Sunday; Thanksgiving closed), so a cutover by the sixth trading day (~Nov 9; exact date per exchange calendar, Operations confirms) meets the full floor. S1's conditional branch translates verbatim: 10–14 trading days requires all named demonstrations passed + zero unresolved organic G-BOOK/G-EXEC events + explicit hub sign-off; below 10, November is not a validation month and S2 governs again. The true binding date is registered so any future slip decision is made against the real floor, not a nominal target eight days stricter than the registration requires. |

## 4. Split-month registration: September dissolves; October inherits

The registered split-September consequence was **conditional** — "on the long-only branch, v4 lands Sept 16" — and the condition never fired (no branch resolved Sept 15; v4 never landed; the Sept 10 DeepSeek event was reclassified as an announcement with no serving change). **September 2026 is a single homogeneous v3 segment for all six arms.** Ledger handling is append-only: the September conditional entry stands, with an appended resolution note that the condition did not fire and the segment is whole.

The split-month registration **re-issues for October**, same structure: on the long-only branch, October segments into v3 (Oct 1–15, ~11 trading days) and v4 (Oct 16–31, ~12 trading days), both `insufficient_n` for regime-sensitive behavioral metrics (descriptive tallies only); the v4 segment's registered function is operational verification of the ablation and the settled-funds enforcement (schema validity, decision completeness, no shorting vocabulary emitted, settled-cash capping observed to bind correctly). On the migration branch, October is a single v3 segment. The boundary date follows the evidence; the registration precedes the data.

## 5. Riders (chosen, not discovered)

- **(a) Pilot extension:** the outgoing cohort's pilot runs through **Oct 31** — one additional pilot month under the October split; November belongs to the refreshed cohort's validation. The exploratory record grows by a month at no cost.
- **(b) November's load is the design, translated:** the validation month deliberately absorbs the full Phase B configuration change at once (refreshed cohort + venue cutover + already-frozen prompt) — exactly as October was designed to; not a new violation of change discipline.
- **(c) Public framing correction:** "18 MO" is now inaccurate — April 2026 through December 2027 is ~20 months end to end. The one-pager, dashboard, and paper boilerplate restate it as phase durations (a ~7-month pilot; a 12-month live confirmatory phase). Dispatched to Reports/Design; recorded here because this amendment created the discrepancy.

## 6. Landing instructions

1. Commit this memo at `docs/amendments/phase_b_boundary_amendment_2026-12-01.md`.
2. Update the month-anchored values across the Tier 1/Tier 2 package, the capital registration (re-inception dates: validation inception moves to the cutover within November; live inception **2026-12-01**), and the execution-environment registrations, citing this memo — one sweep, this document as the source of the new values.
3. Append the September split-month resolution note and the October re-issue to the integrity ledger (append-only; the September conditional entry is not edited).
4. Retire the relay-only flag on this amendment in the v4 landing checklist.

## 7. Provenance

- 2026-08-31 (re-issued 2026-09-12): S1–S4 schedule-risk rulings established the validation floor (15 trading days), the fallback ranking (S2), the boundary's movability pre-deposit (S3), and the Rossi-vs-internal marker classification (S4).
- 2026-09-30: hub invoked S3 on the unmet cutover floor; Research executed the recompute list, confirmed five date translations, corrected the sixth (the November floor binds ~Nov 9, not Nov 1), and ruled the split-September dissolution with the October re-issue.
- 2026-10-04: produced as a committed artifact on hub request (previously relay-only; flagged in the v4 landing checklist).
