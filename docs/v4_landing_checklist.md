# v4 landing checklist

**Status:** v4 is STAGED (uncommitted, held by the v4 lane). Landing is conditional: branch decision **2026-10-15**, conditional landing **2026-10-16**. If the 10-15 decision does not select v4, none of this lands and the staged set stays dormant.
**Why this is a repo file:** hub ruling 2026-10-05 — a checklist that lives only in a session's memory is one cold start from gone, and this landing has to happen on a fixed date.
**Rule:** every item marked CORRECT-AT-LANDING is fixed *before the v4 commit*, not after. The staged hunks describe a flip on 2026-09-01 that never occurred (September 2026 is a single homogeneous v3 segment, 272 records × 6 books, all `v3`/`paper` — ledger `operational_events.2026-10[september_split_conditional_did_not_fire]`).

## The staged set (what is being landed)

| path | check | value at 2026-10-05 |
|---|---|---|
| `prompts/CHANGELOG.md` | blob | `93d89633` |
| `prompts/v4.txt` | blob | `8cf46c17` |
| `src/prompt_builder.py` | blob | `638956f7` |
| `tests/test_v4_ablation.py` | blob | `b6107d6c` |
| `scripts/build_monthly_data_layer.py` | delta | +15 / −0, two hunks (index `82d031cd`) |
| `scripts/phase_a_integrity_ledger.json` | delta | +17 / −0, one contiguous insertion (index `6f531324`) |

Any correction below that edits one of the four blob-checked files **moves its blob**; re-certify against the hub afterwards. The ledger hunk is recoverable with `git diff --cached -- scripts/phase_a_integrity_ledger.json`. Landing beside other lanes follows CLAUDE.md rules 5–8 (pathspec commit; ledger via quarantine with the hub's cross-lane sign-off; delta-identity, never reserialize).

## A. Corrections the hub listed (CORRECT-AT-LANDING)

1. **`regime_boundaries.phase_b_long_only.date` is `2026-09-01`.** Re-date to the actual landing date (2026-10-16 if the conditional lands). The `note` ("v3/shorting (2026-07-01..2026-08-31) vs v4/long-only (2026-09-01+)") and the `reason` ("v4 is September's single change-slot entry; the 2026-09-30 freeze executes on v4") are wrong in the same way and move with it.
2. **`scope: "all_six_models"`.** The live cohort is five (claude, claude_opus, gpt, gemini, grok); DeepSeek is exploratory-only and paper-only (hub-ruled 2026-09-11). *Confirm the intended wording at landing:* the v4 prompt is read by all six arms, so the correction is to distinguish the five-book live cohort from the six arms, not to drop an arm.
3. **`open_shorts_at_boundary.exposure_as_of` is `2026-08-10T19:33Z`.** Stale. Re-measure open shorts at landing time. Shorting is live under v3 today (`shorting_enabled: true`), so the hazard is real: v4 removes COVER from the action enum, and a short open at the flip cannot be closed by its holder. `recommended_procedure` ("Force-cover every open short as the first step of the 2026-09-01 pre-market activation, BEFORE the prompt_version flip") is still the procedure; only its date is stale.
4. **Prompt line `EXECUTION MODE`.** `src/prompt_builder.py:685` prints `settings['mode'].upper()` into every model's prompt. Hub, 2026-10-05: Research ruled that line for deletion as v4's third verified component, because any constant lies somewhere in the window — DeepSeek reading `BROKER_PAPER` while running on the simulator (`config_loader.effective_mode`) is that falsehood arriving early: known, dated, confined to one exploratory arm, resolved at this landing.
   **Verification gap (CLAUDE.md rule 10):** no committed artifact and no session note records that Research ruling, and the *staged* `prompt_builder.py` (`638956f7`) does **not** delete the line. At landing either (a) add the deletion — which moves the `638956f7` blob and needs re-certification — or (b) cite the ruling's artifact. Do not land v4 assuming the deletion is already in the staged set.

## B. Found by the 2026-10-05 sweep — not on the hub's list (CORRECT-AT-LANDING)

All of these carry the never-occurred 2026-09-01 date or the September slot, inside the staged set:

5. `prompts/CHANGELOG.md` (staged hunk): "v4 — effective 2026-09-01"; "Staged until activation (built 2026-08-11; effective 2026-09-01, Phase B)"; "v4 occupies September's single change slot; the 2026-09-30 freeze executes on v4"; "…2026-09-01, the same discipline as the July 1 flip". Prompt freeze is now **2026-10-31**.
6. `src/prompt_builder.py` (staged comment above `_SHORTING_PROMPT_MAJORS`): "v4 (effective 2026-09-01)".
7. `tests/test_v4_ablation.py`: module docstring "effective 2026-09-01"; **`test_regime_boundary_registered` asserts `b["date"] == "2026-09-01"` and `b["scope"] == "all_six_models"`** — correcting items 1–2 without updating this test fails the suite, and updating the ledger without it is how a stale date survives; `test_config_not_flipped_yet` docstring says activation is 2026-09-01. Note the test also asserts `settings["prompt_version"] == "v3"` and will fail the moment the activation step flips it — update it in the activation step, not before.
8. `scripts/build_monthly_data_layer.py` (staged comments, two places): "v3->v4 boundary (regime_boundaries.phase_b_long_only, 2026-09-01)". Comments only — the builder derives `prompt_version` from decision-log stamps, not from this date — but they will mislead.
9. The standalone ledger entry `september_split_conditional_did_not_fire` states that the staged hunk dates v4 from 2026-09-01. It is append-only: once the hunk is corrected, add a short dated follow-up entry; do not edit it.

## C. Activation (a separate signed-off pre-market event, same morning)

10. Flip `prompt_version` v3 → v4 in `config/settings.json` after the force-cover step (item 3).
11. Settled-cash enforcement from **2026-10-16** (hub 2026-10-04): `settlement.enforce_settled_funds` false → true and `settlement.activation_date` 2026-09-16 → 2026-10-16 — one regime line, not two. The activation date is still `2026-09-16` in settings today.
12. October segmentation, per the ledger: v3 2026-10-01..10-15, v4 2026-10-16..10-31, both `insufficient_n` for regime-sensitive metrics; the v4 segment's registered function is operational verification of the ablation and of settled-funds enforcement.

## D. Do NOT touch

13. `prompts/v4.txt` line 10, "full 18-month horizon" (also v2, v3, `LLM-Trading-Lab-Decision-Prompt-v2.0.md`): model-facing input; changing it mid-experiment is a prompt-regime event (hub 2026-10-04). The "18 MO" restatement applies to human-facing surfaces only.

## E. Landing-day verification

- All four blob-checked files match their re-certified hashes; builder and ledger match by added-line identity against the pre-landing `git diff --cached`.
- `python -m pytest tests -q` is green with items 1, 2 and 7 applied together.
- `git diff --stat origin/main..HEAD` names only the v4 package's paths before any push.
