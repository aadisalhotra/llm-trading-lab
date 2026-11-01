# v4 Composition and Verification Rule — Three Components, Fourth Fails

- **Artifact:** `docs/registrations/v4_verification_rule.md` — the single citation target for the v4 verification rule (PRE-REG Tier 2, row 9). Committed per the standing convention; the row-9 relay citation retires upon landing.
- **Status of this text:** **the first committed registration of the 2026-09-11 ruling.** The ruling's governing statement was a relay-only record until this file: the hub relay of 2026-10-05, of a Research ruling delivered inline 2026-09-11. Repo-verified 2026-10-31: `docs/amendments/prompt_mode_line_removal.md` does not exist in any commit on any branch, and `docs/v4_landing_checklist.md` lines 15 and 85 record the relay-only status in the repository's own words. This text is assembled from the conversation record and the operative text preserved at `docs/v4_landing_checklist.md` Step 3 — the citation-resolution gate working as designed. Single-sourcing: component *definitions* are governed by the committed sources cited below; this artifact governs the *composition and exclusion rule*.
- **Load-bearing uses as of this date:** the 2026-10-30 prompt freeze verified against this rule; the rendered-prompt hash check armed 2026-10-31 tests against the configuration it defines.

## 1. The rule

**v4 (the Phase B prompt, cash/long-only branch — the resolved branch; landed 2026-10-23) comprises exactly three verified diff components relative to v3, each verified separately. Any fourth diff component fails verification.**

1. **Shorting ablation** — the literal reverse of the committed v3 shorting diff; verified mechanically against that diff. *(Governing definition: `docs/prereg/tier2_novel_sections.md` T2.4, the standing prompt declaration.)*
2. **Settlement representation** *(content as amended 2026-10-26)* — the state schema splits cash into settled and unsettled balances, with settled (deployable) cash as the primary cash figure (first position, percent-of-book formatting, labeled deployable) and all decision-guidance references to cash denoting deployable cash or made distinction-neutral; one neutral sentence states the settled-funds purchase rule (rule statement only, no strategy guidance); the rendered cash block is pinned by test. *(Governing definition: `docs/amendments/v4_component2_rendering_amendment.md` §4.)*
3. **Environment-mode-line removal** — the `EXECUTION MODE: {mode}` and `PHASE: {phase}` rendered lines deleted, template variables retired; mechanical. *(Governing definition: `docs/v4_landing_checklist.md` Step 3, whose operative text states the removal, the third-component status, and the fourth-component exclusion verbatim; this artifact supersedes that entry's relay-only status.)*

**Exclusion:** verification enumerates the diff components against v3; a component outside this set — however small, however well-intentioned — fails verification and does not land. Adapter-level API formatting per model is not prompt content and is outside this rule's scope, per the standing prompt declaration (T2.4).

**Branch note:** on the migration branch (not taken; resolved at the 2026-10-15 branch decision), v4 would not have landed and component 3 alone would have landed as the prompt event on v3.

## 2. Basis of the 2026-09-11 ruling

The original v4 declaration (August 2026, T2.4) registered **two** components — the shorting ablation and the settlement representation — with the clause *"any third diff component fails verification."* The 2026-09-11 ruling (the Operations build-question dispatch, item ①: the mode/phase lines made the frozen prompt's rendered bytes vary inside the declared freeze window) added the mode-line removal as a third component **by explicit amendment of the two-component declaration rather than by quiet violation of it** — the composition moved from "two, any third fails" to "three, any fourth fails," with the change stated as a self-amendment and hub-ratified the same date.

That mechanism is the rule's point: **the exclusion clause is the enforcement; composition changes are amendments, never silent additions.** The rule has now operated through one full cycle — enumerated, amended once for composition (2026-09-11), amended once for a component's internal content without composition change (2026-10-26), and verified against at the freeze — which is the behavior row 9 registers.

## 3. Provenance

- **2026-08 (Aug 18–19):** two-component v4 declaration delivered (M1 of the retransmission package; landed in T2.4 at `docs/prereg/tier2_novel_sections.md`).
- **2026-09-11:** three-component amendment ruled (Research, inline) and hub-ratified — the mode-line removal added by explicit self-amendment; option (i)-as-removal chosen over constants, metadata-exemption, and accept-and-register.
- **2026-09-30:** landing dates translated by the Phase B boundary move — v4 landing Sept 16 → Oct 16, freeze Sept 30 → Oct 31. Recorded at `scripts/phase_a_integrity_ledger.json`, `operational_events.2026-10[phase_b_boundary_moved_to_2026_12_01]`, and in the `docs/PRE_REGISTRATION.md` amendment log, entry dated 2026-10-05.
- **2026-10-04:** Research's routing record states the 2026-09-11 ruling's artifact was produced and routed on this date at `docs/amendments/prompt_mode_line_removal.md`. **No commit exists at that path on any branch (repo-verified 2026-10-31).** Production is attested by Research's routing record only; non-landing is repo-verified. The two claims are recorded separately and deliberately: the repository can prove the second and cannot speak to the first. The operative text survived at `docs/v4_landing_checklist.md` Step 3, labelled relay-only, which is what this artifact registers.
- **2026-10-23:** v4 landed (realized date; the Oct 16 staged date slipped within the pre-freeze window), settled-funds enforcement active.
- **2026-10-26:** component 2's content amended (`docs/amendments/v4_component2_rendering_amendment.md`) — composition unchanged, three components, fourth still fails.
- **2026-10-30:** prompt freeze executed on v4 as composed here; **2026-10-31:** rendered-prompt hash check armed.
- **2026-11-01:** this artifact produced as the first committed registration of the rule, under the citation-resolution gate. Scope: it resolves row 9's citation; the completeness of the relay-only set is an open question pending the two-direction citation sweep.

## 4. Landing instructions

1. Commit at `docs/registrations/v4_verification_rule.md` (staged-and-halt through the hub). A `docs/` commit outside `prompts/` — the freeze does not bar it.
2. Amend Tier 2 addendum row 9 in `docs/prereg/tier2_novel_sections.md` (line 423) to cite this artifact in place of the relay annotation.
3. Deadline: before the first armed session, **2026-11-02 09:35 ET**.
