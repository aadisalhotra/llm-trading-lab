# Phase B Boundary Amendment — Launch Moves to 2027-01-04

- **Artifact:** `docs/amendments/phase_b_boundary_amendment_2027-01-04.md` — the governing text for this amendment. Routes via the hub; nothing here pushes without sign-off. Zero markers.
- **Decided:** 2026-11-21 (hub), **pre-deposit**. The boundary was still a draft parameter. Upon the OSF deposit the boundary binds and this amendment class closes.
- **Trigger:** the decision to execute the registered cohort refresh before filing (`docs/registrations/cohort_refresh_ruling.md`). A refreshed arm needs 15 validated sessions before live capital, and none was available before 2026-12-01. The registered ranking applies: move the boundary, never compress the validation.
- **This is the second move.** The first, on 2026-09-30, took the boundary from 2026-11-01 to 2026-12-01 (`phase_b_boundary_amendment_2026-12-01.md`). Both precede the deposit and any confirmatory data.

## 1. The amendment

**The Phase B live boundary moves from 2026-12-01 to 2027-01-04.** 2027-01-04 is the first NYSE session of 2027.

## 2. Recompute list

| Item | Was | Now |
|---|---|---|
| Confirmatory window | 2026-12-01 to 2027-12-01 | **2027-01-04 to 2028-01-03**, 252 sessions |
| Phase A (pilot) | 2026-04-09 to 2026-11-30 | 2026-04-09 to **2026-12-31** |
| Validation month | November 2026 | November 2026 for the venue path; **December 2026** for the refreshed cohort |
| Validation floor | 15 trading days of live-path validation | unchanged in content: 15 validated sessions per refreshed arm. December has 22. Arms decide from 2026-12-07 (18 sessions); the hard limit is the open of 2026-12-10 (15) |
| Go/No-Go | 2026-11-30 | between the close of 2026-12-31 and 2027-01-02 |
| OSF deposit | before 2026-12-01 | **before 2027-01-04**; filing 2026-12-21, second date 2026-12-28, last admissible 2026-12-30 |
| Gemini preview's validation clock | through 2026-11-30 | through **2026-12-31**, as the Google arm's registered fallback |
| Anthropic retirement floors | 4.6 pair: 45 and 52 sessions into the window | 5.5 pair: 180 sessions (Opus, floor 2027-09-22) and 184 (Sonnet, floor 2027-09-28) into the window |
| RQ6 runs | shakeout before the first confirmatory-window run; runs at roughly months 3 and 9 | unchanged in content; months 3 and 9 are about April and October 2027 |
| Capstone report | December 2027 | after the window closes, **early 2028** |
| Paper | SSRN, Fall 2027 | after the confirmatory window closes |

All registered month-anchored machinery translates by the same interval. No threshold, gate definition, estimand or rule changes content.

## 3. What does not move

1. **The prompt freeze.** v4 was frozen on 2026-10-30 and governs validation and the whole confirmatory window.
2. **The cohort's resolution date,** 2026-10-26.
3. **The venue cutover** of 2026-11-06 and its November record. The venue path is not re-validated; the refreshed arms are validated on it.
4. **The daily-history, output-budget and call-input-retention boundaries** of November 2026.

## 4. Riders

1. **December carries two cohorts.** The refreshed arms run on the venue's paper path. The Phase A snapshots run paper-only beside them as registered fallbacks. Everything recorded in December is pilot data.
2. **December's load is the design.** The validation month absorbs the refreshed cohort, its reasoning configuration and its budgets at once, as November was designed to absorb the venue cutover. It is one change, made at a phase boundary.
3. **The pilot is about nine months,** not seven, and the project runs about 21 months from April 2026 to January 2028, with the capstone after that. Public descriptions state phase durations. Dispatched to Reports and Design by the hub.

## 5. Landing instructions

1. Commit at `docs/amendments/phase_b_boundary_amendment_2027-01-04.md`.
2. Apply the date translations of §2 through the deposit documents by the companion splice, with this document as the source of the new values.
3. One ledger entry records the move, citing this document's hash.

## 6. Provenance

- 2026-08-31: the schedule-risk rulings established the 15-session validation floor, the ranking that moves the boundary before compressing validation, and the boundary's movability before the deposit.
- 2026-09-30: first move, to 2026-12-01.
- 2026-11-21: the hub decided the cohort refresh and this move. Research computed the session counts from the NYSE calendar.
