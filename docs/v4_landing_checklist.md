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

| path | check | value at 2026-10-05 |
|---|---|---|
| `prompts/CHANGELOG.md` | blob | `93d89633` |
| `prompts/v4.txt` | blob | `8cf46c17` |
| `src/prompt_builder.py` | blob | `638956f7` |
| `tests/test_v4_ablation.py` | blob | `b6107d6c` |
| `scripts/build_monthly_data_layer.py` | delta | +15 / −0, two hunks (index `82d031cd`) |
| `scripts/phase_a_integrity_ledger.json` | delta | +17 / −0, one contiguous insertion (index `c38bb595`) |

Every step below that edits a blob-checked file **moves its blob**; re-certify with the hub afterwards. The ledger hunk is recoverable with `git diff --cached -- scripts/phase_a_integrity_ledger.json`. Landing beside other lanes follows CLAUDE.md rules 5–8 (pathspec commit; ledger via quarantine with the hub's cross-lane sign-off; delta-identity, never reserialize).

## Step 0 — the second v4 component is NOT BUILT (largest open item)

Committed prereg, `docs/prereg/tier2_novel_sections.md` T2.4 (amended, supersedes "literal reverse"): *v4 composition (cash branch): two verified diff components, each verified separately. (1) The shorting ablation … verified mechanically against that diff. (2) The settlement representation — a minimal reviewed addition: the state schema splits cash into settled and unsettled balances, and the prompt carries one neutral sentence stating the settled-funds purchase rule (rule statement only, no strategy guidance). No other semantic content changes; any third diff component fails verification.*

**Component 1 is in the staged set. Component 2 is not.** Checked 2026-10-05: `git diff --no-index prompts/v2.txt prompts/v4.txt` is empty (v4 is byte-identical to v2), `prompts/v4.txt` contains nothing about settled funds, and `prompt_builder.py` (HEAD and staged) renders no settled/unsettled balances. `Portfolio.snapshot()` already carries `settled_cash` (`src/portfolio/portfolio.py:201`, landed `c6d77c72`), so the data exists; the prompt-facing half does not.

To build before landing: render the settled/unsettled split in the portfolio block (`prompt_builder.py`, v4 only — see the gating note in Step 3), and add the one neutral rule-statement sentence to `prompts/v4.txt`. This **moves the `8cf46c17` and `638956f7` blobs** and invalidates the byte-identity premise (class 4).

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

## Step 2 — replace the byte-identity premise with the per-component verification (defect class 4)

The v4 lane's staged artifacts all encode "v4 is byte-identical to v2", which is false the moment Step 0 lands (and was already superseded by T2.4's two-component declaration):
- Ledger hunk `v4_equivalence`: asserts byte-identity to `prompts/v2.txt`, sha256 `963e726207feec3d07934b4df80b28a4388c8b86145226d031a03e5887534f11` on both files. Rewrite as per-component evidence (ablation verified mechanically against the committed v3 shorting diff `57cb14f4`; settlement representation as a reviewed minimal addition; environment-mode removal as the third component).
- `tests/test_v4_ablation.py`: `test_v4_is_byte_identical_to_v2` (line ~74) and `test_v4_matches_the_v2_blob_committed_at_the_v3_commit` (~86) must become component assertions; and `test_regime_boundary_registered` checks `digest in b["v4_equivalence"]` — it breaks if `v4_equivalence` is rewritten without it.
- `prompts/CHANGELOG.md`: the "Verification gate — v4 ≡ v2, byte-identical" section.
Land with Step 0 and Step 3 so the verification matches the final composition, and add the check that makes "any fourth component fails verification" true — today no test would fail on one.

## Step 3 — remove the EXECUTION MODE lines: v4's third verified diff component (RELAY-ONLY)

**Operative text (hub relay, 2026-10-05, of a Research ruling delivered inline 2026-09-11; no committed artifact):** option (i) is implemented as **removal**. The frozen prompt contains **no environment-mode metadata**. It lands as v4's **third verified diff component**: two rendered lines deleted, template variables retired. The v4 declaration amends from **two components to three** (T2.4: ablation + settlement representation + this), and **any fourth fails verification.** Rationale as relayed: any constant lies somewhere in the window — DeepSeek reading `BROKER_PAPER` while running on the simulator (`config_loader.effective_mode`) is that falsehood arriving early: known, dated, confined to one exploratory arm, resolved at this landing.

Checked against the repo (CLAUDE.md rule 10):
- **Not in the staged set.** `src/prompt_builder.py:685` still renders `EXECUTION MODE: {settings['mode'].upper()}`. Landing it **moves the `638956f7` blob**.
- **Which two lines — confirm.** By the code, the lines carrying environment metadata are `EXECUTION MODE: …` (685) and `PHASE: {settings['phase']}` (686). The relay does not name them; I infer MODE and PHASE. *The hub should confirm the pair before landing.*
- **Version-gate the removal.** `prompt_builder.py` renders these lines for every prompt version. Removing them unconditionally changes the live v3 prompt (v3 runs through 2026-10-15) and breaks v2/v3 reproducibility — itself a prompt-regime event. Apply to v4 only.
- **The amended T2.4 sentence ("two … any third fails") is committed text that this contradicts**; the tier2 amendment (addendum row 9) records the change as relay-only. A committed Research artifact should replace the relay before the OSF deposit.

## Part C — activation (a separate signed-off pre-market event, same morning)

- Flip `prompt_version` v3 → v4 in `config/settings.json` **after** the force-cover step (1a, `exposure_as_of`).
- Settled-cash enforcement from **2026-10-16** (hub 2026-10-04): `settlement.enforce_settled_funds` false → true and `settlement.activation_date` 2026-09-16 → 2026-10-16 — one regime line, not two. The date is still `2026-09-16` in settings (inert while the flag is false); its note text was corrected 2026-10-05.
- October segmentation, per the ledger: v3 2026-10-01..10-15, v4 2026-10-16..10-31, both `insufficient_n` for regime-sensitive metrics; the v4 segment's registered function is operational verification of the ablation and of settled-funds enforcement.

## Part D — do NOT touch

- `prompts/v4.txt` line 10, "full 18-month horizon" (also v2, v3, `LLM-Trading-Lab-Decision-Prompt-v2.0.md`): model-facing input; changing it mid-experiment is a prompt-regime event (hub 2026-10-04). The "18 MO" restatement applies to human-facing surfaces only. (This does not conflict with Step 3: that step removes metadata lines under a Research ruling; this line is the objective statement.)

## Part E — landing-day verification

- All four blob-checked files match their re-certified hashes; builder and ledger match by added-line identity against the pre-landing `git diff --cached`.
- `python -m pytest tests -q` is green with Steps 0–3 applied together.
- `git diff --stat origin/main..HEAD` names only the v4 package's paths before any push.
