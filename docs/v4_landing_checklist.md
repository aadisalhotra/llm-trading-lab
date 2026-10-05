# v4 landing checklist

**Status:** v4 is STAGED (uncommitted, held by the v4 lane). Landing is conditional: branch decision **2026-10-15**, conditional landing **2026-10-16**. If the 10-15 decision does not select v4, none of this lands and the staged set stays dormant.
**Why this is a repo file:** hub ruling 2026-10-05 — a checklist that lives only in a session's memory is one cold start from gone, and this landing has to happen on a fixed date. This file is the authoritative list; the ledger entry `v4_staged_hunk_followup_2026_09_01_never_occurred` points here and does not restate it.
**Provenance of the list:** the hub-ratified correction list (audited 2026-08-14, extended 2026-08-17: five defect classes across four staged artifacts), the 2026-10-04/05 dispatches, and a 2026-10-05 re-check of every item against the staged files and the committed prereg. Where an item rests on something that could not be verified, it says so.

## Relay-only items (no committed artifact exists)

Two things this checklist depends on rest on a **hub relay and nothing else**. The hub is commissioning committed artifacts for both; until they exist, cite each as a relay, with its date, never as if it were committed.

| item | what rests on the relay | where it is used |
|---|---|---|
| Phase B boundary amendment | Research's text was delivered governing-inline; the hub relay is the faithful transcription (hub, 2026-10-05). Recorded in ledger `operational_events.2026-10[phase_b_boundary_moved_to_2026_12_01]` as a relay. | the new dates throughout |
| EXECUTION MODE removal | A Research ruling delivered inline **2026-09-11**; the hub relay is the only record (hub, 2026-10-05). | Step 3 |

This repo has already had phantom ruling citations (class 1 below). A ruling with no artifact is the shape one takes.

## The staged set (what is being landed)

| path | check | value (NEW BASELINE recorded 2026-10-05 after the v4 build; pending hub re-certification) |
|---|---|---|
| `prompts/CHANGELOG.md` | blob | `f322d216` (was `93d89633`) |
| `prompts/v4.txt` | blob | `d8e38661` (was `8cf46c17`) |
| `src/prompt_builder.py` | blob | `c1ed4823` (was `638956f7`) |
| `tests/test_v4_ablation.py` | blob | `13c40070` (was `b6107d6c`) |
| `scripts/build_monthly_data_layer.py` | delta | +15 / −0, two hunks (index `82d031cd`, unchanged) |
| `scripts/phase_a_integrity_ledger.json` | delta | +17 / −0, one contiguous insertion (index `9fb45160`; one line, `v4_equivalence`, rewritten) |

The four blob moves are the hub-ordered build (dispatch 2026-10-05, items ①②); the hub is asked to re-certify them. v4.txt sha256 (LF) is now `5a275e2b0eb306cccb48c136f39784817de9f727e32b3154673403643b4c6d07`; v2 reference `963e7262…`.

Every step below that edits a blob-checked file **moves its blob**; re-certify with the hub afterwards. The ledger hunk is recoverable with `git diff --cached -- scripts/phase_a_integrity_ledger.json`. Landing beside other lanes follows CLAUDE.md rules 5–8 (pathspec commit; ledger via quarantine with the hub's cross-lane sign-off; delta-identity, never reserialize).

## Step 0 — the settlement representation: BUILT 2026-10-05 (hub ①)

T2.4 component 2, to the registered spec and nothing beyond: the state schema splits cash into settled and unsettled (`prompt_builder._format_portfolio_block(..., settlement_split=True)`, keys from `Portfolio.snapshot()`), and `prompts/v4.txt` carries **one** neutral sentence as the last hard limit: "Purchases may use only your settled cash; the proceeds of a sale become settled cash one trading day after the sale." ("trading day" is accurate: settlement uses the NYSE calendar.) A second sentence is a fourth component and fails verification — pinned by test.

Not in the spec, added as a safety check (veto-able): a v4 prompt **refuses to build** (`SettlementPromptMismatchError`) while settled-funds enforcement is off, so the prompt can never state a rule the simulator is not enforcing. It makes v4's activation and the settlement flag one event — see Part C.

## Step 1 — correct the never-occurred 2026-09-01 flip: ONE ATOMIC STEP (hub, 2026-10-05)

**Do not split this.** The ledger fields and the tests that pin them move together, or the suite fails. Every sub-item is part of the same commit.

September 2026 was a single homogeneous v3 segment (272 records × 6 books, all `v3`/`paper`; ledger `operational_events.2026-10[september_split_conditional_did_not_fire]`). The staged files describe a flip on 2026-09-01 that never occurred. The 2026-09-01 date was already overruled once (committed prereg: decision `[SEPT 15]`, landing 2026-09-16); it is now 2026-10-16. Re-date to the **actual landing date**.

**1a. Ledger hunk** `regime_boundaries.phase_b_long_only`:
- `date`: `2026-09-01` → landing date.
- `scope`: `all_six_models` → distinguish the five-book live cohort (claude, claude_opus, gpt, gemini, grok) from the six arms. *Confirm the wording at landing:* the v4 prompt is read by all six arms (DeepSeek runs paper-only), so the correction is not to drop an arm.
- `reason`: "v4 is September's single change-slot entry; the 2026-09-30 freeze executes on v4" — prompt freeze is now **2026-10-31**. (Also carries a phantom citation: see 1g.)
- `note`: "v3/shorting (2026-07-01..2026-08-31) vs v4/long-only (2026-09-01+)" — the v3 segment now runs to the day before landing.
- `open_shorts_at_boundary.exposure_as_of` = `2026-08-10T19:33Z` — stale; **re-measure open shorts at landing time.** Shorting is live under v3 (`shorting_enabled: true`); v4 removes COVER from the action enum, so a short open at the flip cannot be closed by its holder.
- `open_shorts_at_boundary.recommended_procedure`: "Force-cover every open short as the first step of the 2026-09-01 pre-market activation, BEFORE the prompt_version flip" — the procedure stands, the date does not.

**1b. `prompts/CHANGELOG.md`** (four places): "v4 — effective 2026-09-01"; "Staged until activation (built 2026-08-11; effective 2026-09-01, Phase B)"; "v4 occupies September's single change slot; the 2026-09-30 freeze executes on v4"; "…2026-09-01, the same discipline as the July 1 flip".

**1c. `src/prompt_builder.py`**: the comment above `_SHORTING_PROMPT_MAJORS`, "v4 (effective 2026-09-01)".

**1d. `tests/test_v4_ablation.py`**: the module docstring ("effective 2026-09-01, Phase B long-only"); **`test_regime_boundary_registered` asserts `b["date"] == "2026-09-01"` and `b["scope"] == "all_six_models"` by name** — fix 1a without 1d and the suite fails; fix 1d without 1a and the ledger and its test disagree. Also the `test_config_not_flipped_yet` docstring. Leave its `prompt_version == "v3"` assertion until the activation step flips the config (Part C), then update it there.

**1e. `scripts/build_monthly_data_layer.py`** (staged comments, two places): "v3->v4 boundary (regime_boundaries.phase_b_long_only, 2026-09-01)". Comments only — the builder derives `prompt_version` from decision-log stamps, not from this date — but they mislead.

**1f. Ledger follow-up:** `september_split_conditional_did_not_fire` states that the staged hunk dates v4 from 2026-09-01. It is append-only: once the hunk is corrected, add a short dated follow-up entry; do not edit it.

**1g. Phantom citations (defect class 1, hub-ratified 2026-08-14).** The 2026-08-08 execution-architecture ruling **never happened** (the PI was travelling with no computer access); the 2026-08-05 session was real but covered cost accounting, Tier 1 corrections and routing, **not** long-only content. Do not purge 2026-08-05 elsewhere (`src/analytics/cost_rates.py` cites it correctly three times). In the staged set:
- `prompts/CHANGELOG.md` lines ~10–11: "Phase B is long-only per the **2026-08-08 execution-architecture ruling** (reinstating the 2026-08-05 ruling)".
- Ledger hunk `reason`: the same phrase.
- `prompts/CHANGELOG.md` line ~77: "Shorting design ratified by Research + PI" — undated, no backing artifact.
- `scripts/build_monthly_data_layer.py:216` "(Research ruling)" — undated; this is in committed HEAD, not the staged hunk, and joined the list because that file is contended (hub chose serialization). Separately, line ~2202 cites "the Research ruling of 2026-09-05": dated, but I did not verify a backing artifact — check it.
Replace each with the **backing artifact** (CLAUDE.md, CORRECTIONS.md, the ledger, a docs section) or an explicit *relay-only* flag. Do not invent an authority.

**Done when:** `git diff --cached` for the staged files contains no `2026-09-01` that is not quoting history, `all_six_models` is not the scope of this boundary, no phantom ruling is cited as authority, and `python -m pytest tests -q` is green — all in one commit.

## Step 2 — the byte-identity premise replaced by per-component verification: DONE 2026-10-05 (defect class 4)

Done in the staged files: `tests/test_v4_ablation.py` (`test_v4_is_byte_identical_to_v2` and `test_v4_matches_the_v2_blob…` replaced by component 1/2/3 tests, plus a render-level test that fails on **any** difference beyond the three components, and a negative test that injects a fourth and requires it to fail); the ledger hunk's `v4_equivalence` (per-component claim with both digests; `test_regime_boundary_registered` now checks the v4.txt digest **and** the v2 reference digest); the CHANGELOG "Verification gate" section. **Remaining at landing:** nothing in this step beyond re-certifying the blobs above.

## Step 3 — remove the EXECUTION MODE / PHASE lines: v4's third verified diff component — IMPLEMENTED, GATED (RELAY-ONLY)

**Operative text (hub relay, 2026-10-05, of a Research ruling delivered inline 2026-09-11; no committed artifact):** the frozen prompt contains **no environment-mode metadata**; the two rendered lines are deleted and their template variables retired; it is v4's **third** verified diff component and **any fourth fails verification.** Hub 2026-10-05 ②: the pair is `EXECUTION MODE:` and `PHASE:` (`prompt_builder.py:685–686`), removal gated to the **Oct 16 boundary and never before** — unconditional removal would be an unregistered mid-regime prompt event on live v3.

Implemented (`prompt_builder.py`): `_environment_lines(version, settings)` omits both lines when `environment_metadata_removed` — i.e. for prompt major 4 (long-only branch: v4 *is* the Oct 16 event), **or** when `settings["prompt_environment_metadata"] == "removed"` (migration branch: the removal lands alone on v3 at the Oct 16 activation). The setting is **absent today**, so v1–v3 render the lines byte-for-byte as before (pinned by `test_v3_render_is_unchanged_by_the_v4_machinery`).

**At landing:** on the long-only branch nothing extra — the `prompt_version` flip (Part C) is the removal. On the migration branch, the signed-off activation adds `"prompt_environment_metadata": "removed"` to `config/settings.json`; that is the Oct 16 event. Cite the ruling as a relay until a committed artifact exists.

## Part C — activation (a separate signed-off pre-market event, same morning)

- Flip `prompt_version` v3 → v4 in `config/settings.json` **after** the force-cover step (1a, `exposure_as_of`).
- **Flip the settlement flag in the SAME event as the version flip.** A v4 prompt states the settled-funds rule and will refuse to build (`SettlementPromptMismatchError`) while `settlement.enforce_settled_funds` is false or the run date precedes `settlement.activation_date`; setting both together (below) is what makes the sentence true.
- Settled-cash enforcement from **2026-10-16** (hub 2026-10-04): `settlement.enforce_settled_funds` false → true and `settlement.activation_date` 2026-09-16 → 2026-10-16 — one regime line, not two. The date is still `2026-09-16` in settings (inert while the flag is false); its note text was corrected 2026-10-05.
- October segmentation, per the ledger: v3 2026-10-01..10-15, v4 2026-10-16..10-31, both `insufficient_n` for regime-sensitive metrics; the v4 segment's registered function is operational verification of the ablation and of settled-funds enforcement.

## Part D — do NOT touch

- `prompts/v4.txt` line 10, "full 18-month horizon" (also v2, v3, `LLM-Trading-Lab-Decision-Prompt-v2.0.md`): model-facing input; changing it mid-experiment is a prompt-regime event (hub 2026-10-04). The "18 MO" restatement applies to human-facing surfaces only. (This does not conflict with Step 3: that step removes metadata lines under a Research ruling; this line is the objective statement.)

## Part E — landing-day verification

- All four blob-checked files match their re-certified hashes; builder and ledger match by added-line identity against the pre-landing `git diff --cached`.
- `python -m pytest tests -q` is green with Steps 0–3 applied together.
- `git diff --stat origin/main..HEAD` names only the v4 package's paths before any push.
