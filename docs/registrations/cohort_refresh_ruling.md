# The Confirmatory Cohort — the Registered Refresh, Executed Before Filing

- **Artifact:** `docs/registrations/cohort_refresh_ruling.md` — governing text. Routes via the hub; nothing here pushes without sign-off. Zero markers.
- **Decided:** 2026-11-21 (hub, option B), on Research's ruling of the same day (`cohort_options_ruling_2026-11-21.md` rev. 2, `396bc133…`, which stays outside the tree as the record of the three options considered). §2 to §6 are that ruling's conditions as the hub ratified them. §4 and §7 were ruled by Research on 2026-11-21 at the hub's request and need ratification.
- **Rev. 2, 2026-11-21.** Replaces `3a7317d4…` (12,397 B). The hub ratified §4 and §7 as issued and ruled that the snapshots stay on the venue path through the close of 2026-12-04, with the handover on the weekend of 2026-12-05/06 (§4, item 2). Three things are ruled here at its request and need ratification: what happens to each snapshot's book at the handover (§4, item 4); that 2026-12-07 is the first validated session and not the earliest admissible one (§3, items 2 and 8); and that the calibration uses one input set (§5).
- **Basis:** deposit texts as routed through the 2026-11-25 splice; ledger entries `cohort_as_run_2026_10_26` and `cohort_as_run_2026_10_26_provider_tests`; the NYSE calendar; provider pages read on 2026-11-21, of which Operations saves dated copies under `docs/ops/`.

## 1. The decision

The registered cohort rule (Tier 2, T2.6) is executed. Each confirmatory arm is pinned to its provider's newest generally available own-tier flagship **as of 2026-10-26**, the date the rule registered:

| Arm | Phase A snapshot (pilot cohort, registered fallback) | Confirmatory identifier |
|---|---|---|
| Claude Sonnet | `claude-sonnet-4-6` | `claude-sonnet-5-5` |
| Claude Opus | `claude-opus-4-6` | `claude-opus-5-5` |
| GPT | `gpt-5.4-2026-03-05` | `gpt-6-astra` |
| Grok | `grok-4.20-0309-reasoning` | `grok-4.7` |
| Gemini | `gemini-3.1-pro-preview` | `gemini-4-argon` |

DeepSeek v4-pro is unchanged: exploratory, paper-only.

**The resolution date is not moved.** It was fixed before the candidates were known, and the ledger resolved it from primary pages on 2026-11-10 and 2026-11-14. Models released after it are outside the rule. Resolved as of the decision date, at least the Google slot would differ.

**The Google slot.** On 2026-10-26 Argon was Google's only Gemini 4 model: announced on 2026-09-30 as "our new frontier model" and generally available from 2026-10-13. Carbon was announced on 2026-10-28 and became generally available on 2026-11-10; Hydrogen and Neon followed on 2026-11-12 and 2026-11-18. The registered test of general availability is functional, and the ledger records Argon passing it.

**The trigger.** The refresh follows lifecycle facts known since August 2026: retirement floors for the two Claude 4.6 models that fall 45 and 52 sessions into a window beginning 2026-12-01, and a Gemini preview whose successor generation is generally available. It follows no pilot result. No confirmatory data exists.

## 2. What this supersedes

1. **The pre-deposit amendment of 2026-11-08,** which pinned the confirmatory cohort to the deployed snapshots. Its reason was that a model change inside a zero-margin validation window would put integrations live with fewer than 15 validated sessions. The go-live date moves (§3), so the reason no longer holds.
2. **The Gemini exception, for this refresh.** T2.6 kept Gemini on its validated configuration, and Research ruled on 2026-11-10 and 2026-11-19 that a newer generation does not change that. Both rulings rested on there being no validation runway before go-live. Admitting Argon is an amendment and is recorded as one. `gemini_successor_disclosure_ruling.md` §3 stands for any change inside the confirmatory window and is superseded for this pre-filing refresh.
3. **The Phase B boundary.** Go-live moves from 2026-12-01 to 2027-01-04 (`docs/amendments/phase_b_boundary_amendment_2027-01-04.md`).
4. **The filing of 2026-11-23** and the freeze-day plan for 2026-11-22.

The 2026-10-26 pin was still missed. The disclosure of that process failure stands.

## 3. The validation month

1. **December 2026 is the validation month** for the refreshed cohort, under the frozen v4 prompt and the daily frame. It has 22 sessions.
2. **Fifteen sessions, mechanically.** An arm needs 15 validated sessions by the Go/No-Go. A validated session is a session on the venue path at the frozen caps and timeout. The venue path passes to the refreshed arms at the handover of 2026-12-05/06 (§4), so the first validated session is 2026-12-07, which gives 18. The hard limit is the open of 2026-12-10, which gives exactly 15.
3. **Gates, per arm, as registered:** decision completeness of at least 0.80; an uncorrupted book; stable identity; and execution integrity on the venue path.
4. **Fallback.** An arm that fails a gate, or has fewer than 15 validated sessions, enters Phase B on its Phase A snapshot. Gate outcomes decide; nobody chooses.
5. **The go-live date does not move again for one arm.**
6. **The fallback is hollow for the Claude arms.** From 2027-01-04, `claude-opus-4-6` has 23 sessions before its retirement floor and `claude-sonnet-4-6` has 30.
7. **An arm whose target and fallback both fail** is excluded. The cohort is then four, under the cohort-reduction estimator.
8. **Earlier sessions are admissible as a shakedown** (ruled 2026-11-21). A refreshed arm may decide on internal paper before the handover, from 2026-12-01 at the earliest, once its calibration is done and its caps and the timeout are set. These sessions do not count toward the 15, because they are not on the venue path. Caps and timeout may be corrected on what the shakedown shows until the open of 2026-12-07, when they freeze. The shakedown book is a fresh internal-paper book at the registered Phase B book capital; its series closes at the 2026-12-04 close and the arm starts fresh on the venue. Its records are pilot data, reported as a shakedown segment, and enter no behavioural series.

## 4. The Phase A snapshots through December (ruled 2026-11-21)

**Confirmed: all five Phase A snapshots keep running through 2026-12-31,** so that every fallback is a validated one. Conditions:

1. **Separate identity, book, state and logs for every arm.** No snapshot shares a state file, a book key or a log with its successor. The commingling of the Sonnet and Opus state files in April 2026 is the case this prevents.
2. **The venue path passes to the five refreshed arms at the handover** (hub, 2026-11-21). The snapshots stay on it through the close of Friday 2026-12-04, and the handover is the weekend of 2026-12-05/06, outside market hours. The validation has to be of the configuration that goes live: five venue books and the barrier's rotation over five. From the handover the snapshots run paper-only, as DeepSeek does, and submit no venue orders. Their live-path validation is their venue record from 2026-11-06 to 2026-12-04.
3. **Nothing is inherited.** Each refreshed arm starts a fresh book on the venue.
4. **A snapshot's venue book is closed as a series, and the snapshot starts a fresh book on internal paper** (ruled 2026-11-21). The registered procedure for an epoch boundary applies, as it did at the venue cutover (`paper_epoch_retirement.md` §1). The series terminates at its final venue session, 2026-12-04, with its terminal equity at that day's closing marks recorded in the ledger. The state file is archived read-only and pinned by commit. The snapshot then incepts fresh on internal paper at the registered Phase B book capital. No position is sold to do this: positions open at the endpoint are open at the end of a series, so nothing is censored under T2.3. If the venue's paper account can only be cleared by orders, those fills are administrative, are recorded on the snapshot's venue book before its series closes, and are censored under T2.3.
   - **Why the book is not carried.** The registered rule is that books do not carry across an epoch boundary, and a guard enforces it. Carrying would mean overriding that guard by hand on five state files, in the weekend of five new inceptions. A carry that went wrong would cost the fallback its book-integrity gate, which is the opposite of what the snapshots are kept for. The two paths also account differently (settlement, venue minimums, and dividends, which the paper simulator has never credited), so a carried book would be one book under two regimes. What a carry would buy is continuity in pilot data for models that are no longer the confirmatory cohort.
   - **What it costs.** The snapshots' December data begins with an inception, as their venue data did on 2026-11-06, and is read with that. A registered series closure is not a book-integrity event: the gate applies to each book, as it did across the venue cutover.
5. **Every arm, old and new, receives the same inputs at each tick,** under one `data_inputs_hash`.
6. **The snapshots' configuration does not change:** provider-default reasoning with no parameter sent, a 16,384-token budget, and the cohort-wide timeout of §5.
7. **The same three gates apply to a snapshot** in December: completeness, book integrity and identity.
8. **Everything recorded in December is pilot data,** for both cohorts. Figures are reported per cohort. A pair made of one arm from each cohort is exploratory.

## 5. Reasoning, budgets and the timeout

**Reasoning.** The registered rule is unchanged: every reasoning-capable model runs at its provider's standard effort, not the maximum (`docs/METHODOLOGY.md`, "Reasoning Configuration and Cross-Model Equivalence"). Reasoning cannot be disabled on any of the five confirmatory identifiers, so the rule puts all five on. The level for each is its provider's documented default.

| Arm | Level | Sent |
|---|---|---|
| `claude-sonnet-5-5` | adaptive thinking, effort `high` | explicitly |
| `claude-opus-5-5` | adaptive thinking, effort `medium` | explicitly |
| `gpt-6-astra` | provider default | not sent, unless a default is documented or reported by the API |
| `grok-4.7` | `high` | explicitly |
| `gemini-4-argon` | `MEDIUM` | explicitly |

1. **The level is never chosen.** It is not picked to match another arm or to fit a budget. The levels are not equal effort and are not described as such.
2. **It is sent explicitly where the provider documents the default and that omitting the parameter is equivalent.** That pins it against a default that moves under a fixed identifier. Operations shows the equivalence by reasoning-token counts with and without the parameter.
3. **Where no default is documented or reported, nothing is sent,** and the configuration table says so, with the observed counts.

**Budgets.** The answer budget stays 16,384 tokens for every arm (Tier 1, T1.4). Where reasoning shares the output cap, the nominal cap is 16,384 plus that arm's reasoning allowance.

1. **The allowance is measured by one rule.** Before the validation sessions begin, each arm is run off-cycle on at least 100 retained trading-call inputs covering all thirteen tick positions, and on their screening calls, at its default level, with the cap at the provider's maximum and a 600-second timeout. The allowance is twice the largest reasoning-token count observed, rounded up to the next 4,096 and bounded by the provider's maximum output. The inputs are one set, the same for every arm, drawn from the Phase A snapshots' retained inputs from 2026-11-23.
2. **The timeout is re-derived by its registered derivation** (`api_call_timeout.md` §1) from the same runs: the highest successful latency plus 40%, rounded up to 30 seconds, not below 300, cohort-wide. It has to pass the grid-occupancy check for a 30-minute cycle.
3. **Caps and timeout are frozen before the first validated session.** A later change restarts the count of 15.
4. **Effort is not lowered to make an arm fit.** An arm that cannot run at its default level inside the caps, the timeout and the grid fails validation and falls back.
5. **The calibration calls are induced tests.** They enter no behavioural series and no cost-per-decision figure.

## 6. Three further rules for the refreshed arms

1. **Server-side fallback is never requested.** A fallback is a silent model change, and the registered handling of a model change is truncation. The model named in every response must equal the configured identifier; any other value is an identity event.
2. **A refusal is its own event class.** A response whose stop reason is a refusal is recorded as `REFUSAL`, counts as a failed decision in the completeness gate, and is never retried on another model (`api_call_timeout.md` §3).
3. **Token counts are not comparable across the cohort boundary.** Operations reports up to 1.35 times the input tokens for the same text on the Claude 5.5 models. Cost per decision is reported per phase and per configuration.

## 7. Freeze and filing (ruled 2026-11-21)

**The deposit is frozen after the configuration table is verified for the five confirmatory arms, and filed before 2027-01-04.**

| Step | Date |
|---|---|
| Calibration runs complete; caps and timeout frozen | by Friday 2026-12-04 |
| Handover of the venue path | weekend of 2026-12-05/06 |
| First validated session of the refreshed arms | Monday 2026-12-07 |
| Verification record closes | close of Thursday 2026-12-17 |
| Research's last substantive change | Friday 2026-12-18, 12:00 ET |
| Freeze | Sunday 2026-12-20 |
| Filing | Monday 2026-12-21 |
| Second filing date, if the table is not verified by the cutoff | Monday 2026-12-28, on a freeze of Sunday 2026-12-27 |
| Last admissible filing | Wednesday 2026-12-30 |

**"Verified" means all six of these, for each confirmatory arm:**

1. The request fields are read from the adapter at the commit to be frozen.
2. On every production record from the arm's first validated session to the close of 2026-12-17, the model named in the response equals the configured identifier.
3. Reasoning tokens are present on those records, where the provider reports a count.
4. No response ended on its budget.
5. The provider's page for the level is saved with its date, and the equivalence of sending and omitting it is shown.
6. Temperature behaviour is established by a probe outside production: honoured, ignored or rejected.

**Why before the Go/No-Go.** The deposit registers each arm's target and its fallback, so it is true under every gate outcome. Gate outcomes land as ledger facts after it, as T2.6 already provides. A defect found between the filing and 2027-01-04 goes in the amendment log before the first confirmatory tick. That is a cheaper failure than a filing that slips past go-live.

**The final cohort** is recorded in the ledger and in the amendment log before the first confirmatory tick.

## 8. Not established on 2026-11-21

1. Lifecycle terms for `gpt-6-astra`, `grok-4.7` and `gemini-4-argon`. Argon is no longer Google's top model.
2. The default level of `gpt-6-astra`, and whether reasoning shares the cap on `grok-4.7` and `gemini-4-argon`.
3. Whether each provider documents its identifier as a fixed snapshot. All five are undated.
4. Research read Google Cloud's release notes and Google's announcements for the Google slot, not the Gemini API's own changelog, which the ledger quotes.

## Provenance

- August to September 2026: the cohort framework chose refresh over freeze, on these exposures.
- 2026-10-26: the registered pin was not executed.
- 2026-11-08: the omission was found; the cohort was pinned to the deployed snapshots by amendment.
- 2026-11-21 (rev. 2): the hub ratified §4 and §7 and fixed the handover. Research ruled the snapshot books, the shakedown sessions and the common calibration set.
- 2026-11-21: the hub reopened the decision before filing. Research ruled three options and recommended the refresh. Operations established that reasoning cannot be disabled on any target and estimated the build. The hub decided the refresh.
