# RQ6 / Forced-Change Reconciliation Package

- **Drafted:** May 22, 2026
- **Why this exists:** The RQ6 correction memos and the Gemini Flag 2 package were
  grounded in chat-drafted artifacts (`PRE_REGISTRATION-RQ6-section.md`, the
  deprecation-exposure draft) that were never committed. Claude Code verified the
  committed `PRE_REGISTRATION.md` / `METHODOLOGY.md` and found those targets do not
  exist. This package re-grounds the corrections against the committed structure and
  authors the missing infrastructure.
- **Atomic landing:** the landable package is Correction 1 + Correction 2 (from
  `RQ6-rationale-correction.md`, unchanged, targets confirmed) + Part 1 + Part 1B +
  Part 1C + Part 1D + Part 1E + Part 2 + Part 3 below. It lands as a single commit.
  Nothing lands piecemeal — landing a subset would leave the false temperature
  premise live in some committed location and produce the cross-file contradiction
  this package exists to eliminate. The pre-landing grep identified seven
  occurrences of the false premise across the repo; all seven have ratified handling
  here (Corrections 1–2, Parts 1, 1B, 1C, 1D, 1E).
- **Audit status:** the reconciliation audit is complete. It found three
  uncommitted items — the RQ1 cohort-reduction estimator, the METHODOLOGY
  resolved-identity check, and this Forced-Change section. Items 2 and 3 are
  authored or absorbed by this package; the RQ1 estimator is registered as an open
  September methodology-review item (Part 2, Part 4). No audit gate remains; the
  package is clear to land atomically.

---

## Part 1 — Correction 3, re-grounded to the committed bulleted RQ6 entry

The committed `PRE_REGISTRATION.md` RQ6 entry is a bulleted entry. There is no
"Operationalization — Deployed Configuration" paragraph, no "Why RQ6 Is Not Scoped"
subsection, and no "Verification Commitment" subsection. Correction 3a/3b/3c, which
targeted those, are withdrawn and replaced by the following, mapped to the bulleted
structure.

**Structural decision:** the "Why Not Scoped" content lands as a **new bullet, not a
subsection** — a subsection is structurally foreign to a bulleted entry.

**Edit 1 — the "Type." bullet.** The committed "Type." bullet contains the
false-premise clause "temperature 0 is a no-op across the reasoning cohort." Replace
that clause (and any rationale clause depending on it) with:

> the deployed pipeline sends no temperature parameter to any of the six models, so
> every model runs at its provider-default sampling configuration; temperature 0 is
> an off-deployment configuration for the entire cohort, which is why RQ6 is
> operationalized at the deployed configuration rather than at temperature 0

**Edit 2 — add one new bullet** to the RQ6 entry, in the entry's existing bullet
style:

> **Scope:** RQ6 covers all six models uniformly on the deployed-configuration
> basis; it is not partitioned by temperature-honoring behavior. The verified
> per-model temperature behavior — four models (GPT-5.4, Gemini 3.1 Pro, Claude
> Sonnet 4.6, Claude Opus 4.6) honor temperature and are deterministic at
> temperature 0; two (Grok 4.20 Reasoning, DeepSeek v4-pro) silently ignore it — is
> recorded as a disclosed fact in the per-model API-configuration record, not as a
> basis for the RQ6 design.

**Integration:** Claude Code, which has the committed RQ6 entry visible, applies
Edit 1 as a clause-level replacement within the "Type." bullet (preserving the
bullet's other, correct content) and appends Edit 2 as a new bullet. This memo
supplies the corrected content; the surgical placement is Claude Code's, since the
surrounding bullets are not visible from this chat.

---

## Part 1B — PRE_REGISTRATION.md §3.6 parenthetical correction

`PRE_REGISTRATION.md` §3.6 ("API non-determinism characterization (RQ6, deployed
configuration)") is a committed section whose primary subject is RQ1's convergence
ceiling. It is already reframed to deployed-configuration / Δ_m language and is
correct except for one parenthetical clause that states the falsified premise as the
basis for withdrawing the temperature-0 framing. Only that parenthetical is
corrected; the rest of §3.6 — the Δ_m framing and the RQ1 convergence-ceiling
commitment — is correct and is not touched.

**Replace this parenthetical, verbatim:**

> (Temperature 0 is a no-op across the reasoning cohort, so the original
> temperature-0 framing is withdrawn — see METHODOLOGY § 'API Non-Determinism
> (RQ6) — Deployed-Configuration Basis'.)

**with:**

> (The deployed pipeline sends no temperature parameter to any model, so temperature
> 0 is an off-deployment configuration for the entire cohort; the original
> temperature-0 framing is therefore withdrawn — see METHODOLOGY § 'API
> Non-Determinism (RQ6) — Deployed-Configuration Basis'.)

This is a clause-level find-and-replace. The corrected basis is consistent with
Correction 1: the reframe rests on the deployed pipeline setting no temperature
parameter, not on any claim about per-model temperature honoring. Per-model
temperature behavior (four of six models honor temperature) remains a disclosed fact
in the METHODOLOGY section and the per-model API-configuration table; it is not cited
as a basis here.

---

## Part 1C — research_metrics.py compute_rq6 docstring correction

`research_metrics.py` (the `compute_rq6` docstring, ~line 1329) is one of the four
files the consistency cross-check runs across, and it states the false premise as
the reason for the reframe. **This must be in the atomic commit:** if the package
lands without it, the corrected METHODOLOGY/PRE_REGISTRATION text contradicts the
docstring and may trip the cross-check. Only the parenthetical reason is corrected.

**Replace this docstring text, verbatim:**

> Reframed from 'non-determinism at temperature 0' (temperature is a no-op across the
> reasoning cohort) to run-to-run divergence at each model's DEPLOYED configuration.

**with:**

> Reframed from 'non-determinism at temperature 0' (the deployed pipeline sets no
> temperature parameter for any model, so temperature 0 is an off-deployment
> configuration for the entire cohort) to run-to-run divergence at each model's
> DEPLOYED configuration.

Clause-level find-and-replace; the rest of the `compute_rq6` docstring is not
touched.

---

## Part 1D — docs/RQ5-RQ6-specification.md superseded-by pointer

`docs/RQ5-RQ6-specification.md` (Decision 1, ~line 13) is a committed source
document that carries the false premise in its original strongest form ("Temperature
0 is a no-op across most or all of the cohort..."). It is handled by a superseded-by
pointer, not in-place correction — consistent with the package's Task 7 treatment of
the sibling document, and the correct treatment for a source document recording a
past decision: in-place rewriting falsifies the historical record, whereas a pointer
preserves the decision as made and directs the reader to the correction.

**Add to Decision 1 of `docs/RQ5-RQ6-specification.md`:**

> **Superseded-by note.** The temperature rationale in this Decision — that
> temperature 0 is a no-op across most or all of the cohort — is superseded. The RQ6
> deployed-configuration reframe rests on the deployed pipeline sending no
> temperature parameter to any model (so temperature 0 is an off-deployment
> configuration for the whole cohort), not on per-model temperature honoring; the
> verification found four of six models honor temperature. See the RQ6 Rationale
> Correction (`RQ6-rationale-correction.md`) and the corrected METHODOLOGY § 'API
> Non-Determinism (RQ6)'. The original temperature-premise wording below is retained
> as a historical record of the decision as first made.

Claude Code formats this pointer parallel to the Task 7 superseded-by pointer on
`RQ5-RQ6-spec-completion.md` Section A, so the two sibling documents are handled
consistently.

---

## Part 1E — docs/RESEARCH_QUESTIONS.md RQ6 entry (scope decision: IN this commit)

**Scope decision.** `docs/RESEARCH_QUESTIONS.md` (RQ6 entry, lines ~86, 88) carries
the entire superseded temp-0 RQ6 framing, including the superseded flip-rate metric.
`PRE_REGISTRATION.md` §4 cites `RESEARCH_QUESTIONS.md` as the living RQ-status
tracker. The corrected RQ6 entry therefore belongs in **this** atomic commit: a
commit that corrects the false premise everywhere except the tracker the
pre-registration cites would ship a corrected `PRE_REGISTRATION.md` pointing at a
contradictory document — the exact intermediate-contradiction state the atomic
package exists to prevent. It does not warrant deferral on size grounds: the
corrected RQ6 framing is already fully ratified (Correction 1, re-grounded
Correction 3, Part 1B), so this is application of settled content, not new design.

**Handling.** Unlike Part 1D, this is an in-place rewrite, not a pointer:
`RESEARCH_QUESTIONS.md` is a live tracker the pre-registration cites as current, so
it must read as current. The superseded flip-rate metric and the temperature-0
framing are removed entirely.

**Corrected RQ6 entry content.** Ratified. Claude Code maps it onto
`RESEARCH_QUESTIONS.md`'s actual RQ-entry format and preserves the file's
status-field convention.

> **RQ6 — API non-determinism characterization (operational reproducibility of the
> deployed agents).**
> - *Question:* How run-to-run reproducible is each model's decision-making at the
>   configuration it is actually deployed and traded at?
> - *Operationalization:* Measured at the deployed configuration — provider-default
>   sampling, no temperature parameter set — not at temperature 0. The deployed
>   pipeline sends no temperature parameter to any model, so temperature 0 would
>   characterize an off-deployment configuration for the entire cohort; the original
>   temperature-0 framing is withdrawn. Per-model temperature behavior (four of six
>   models honor temperature) is a disclosed fact in the per-model API-configuration
>   table, not the basis for the reframe.
> - *Metric:* Δ_m — per-model run-to-run decision divergence across repeated calls at
>   the deployed configuration. Replaces the superseded flip-rate metric.
> - *Type:* Characterization research question, not a null-hypothesis test.
> - *Multiple-testing:* Not a member of the Benjamini-Hochberg FDR family.
> - *Cross-reference:* METHODOLOGY § 'API Non-Determinism (RQ6) — Deployed-Configuration
>   Basis'; `PRE_REGISTRATION.md` RQ6 entry and §3.6.

---

## Part 2 — Forced-Change / Deprecation Exposure (NEW SECTION for `PRE_REGISTRATION.md`)

This section does not exist in the committed `PRE_REGISTRATION.md`. It is authored
here as new infrastructure and lands whole. It is self-contained — it does not
reference a "resolved-identity check," which is not committed.

> ### Forced-Change / Deprecation Exposure — Confirmatory Model Set
>
> **Purpose.** The confirmatory window runs November 1, 2026 – November 1, 2027.
> Some pinned models carry documented or structural exposure to a provider-forced
> model change inside that window. This section discloses the exposure and
> pre-specifies the handling.
>
> **Exposure table.** Populated from the model-lifecycle monitor as of the OSF
> deposit date.
>
> | Model | Provider | Lifecycle status | Retirement floor / change exposure | Inside confirmatory window? | Source |
> |---|---|---|---|---|---|
> | `claude-sonnet-4-6` | Anthropic | Active | Retirement floor: not sooner than 2027-02-17 | Yes | `<Anthropic deprecations page — fill URL>` |
> | `claude-opus-4-6` | Anthropic | Active | Retirement floor: not sooner than 2027-02-05 | Yes | `<Anthropic deprecations page — fill URL>` |
> | Gemini 3.1 Pro (`gemini-3.1-pro-preview`) | Google | Active — preview (pre-GA) build | Structural preview-supersession risk; no dated retirement floor published | Yes — undated, structural | `<Google model documentation — fill URL>` |
> | GPT-5.4 | OpenAI | `[VERIFY — deprecation audit pending]` | `[VERIFY]` | `[VERIFY]` | `<OpenAI deprecations page — fill URL>` |
> | Grok 4.20 Reasoning | xAI | `[VERIFY — deprecation audit pending]` | `[VERIFY]` | `[VERIFY]` | `<xAI model page — fill URL>` |
> | DeepSeek v4-pro | DeepSeek | Active | No retirement announced as of audit | No (none published) | `<DeepSeek docs — fill URL>` |
>
> The GPT-5.4 and Grok 4.20 rows are pending the OpenAI and xAI deprecation audit,
> which must complete before the OSF deposit.
>
> **Anthropic retirement-floor exposure.** `claude-sonnet-4-6` and `claude-opus-4-6`
> carry documented "not sooner than" retirement floors inside the confirmatory
> window. These are floors — earliest-possible dates, which may be extended. No
> current Anthropic model has a floor past 2027-11-01, so the exposure cannot be
> removed by snapshot selection.
>
> **Gemini preview-build supersession exposure.** Gemini 3.1 Pro is pinned to
> `gemini-3.1-pro-preview`, a preview (pre-GA) build; Google exposes no dated,
> general-availability snapshot for this model. Preview builds carry elevated
> supersession risk: a preview is typically deprecated or replaced when the provider
> ships the general-availability version. If a GA `gemini-3.1-pro` is released during
> the confirmatory window, the pinned preview build may be deprecated, retired, or
> repointed — a provider-forced change inside the window. This is forced-change
> exposure of the same class as the Anthropic floors; the difference is that it is
> undated and structural, not a published floor.
>
> **Pre-specified handling.**
> 1. *No mid-window migration.* If a pinned model undergoes a provider-forced change
>    during the confirmatory window, the response is truncation, not replacement —
>    the pipeline does not migrate to a successor model. Mid-window migration would
>    itself be the regime change the pinned-snapshot methodology exists to prevent.
> 2. *Truncation point = the change date.* The affected model's confirmatory series
>    ends there; data after it is exploratory only.
> 3. Surviving models continue, unaffected.
> 4. The incident is logged and reported in the paper's limitations.
>
> **Per-RQ degradation.** Per-model RQs (RQ2, RQ3, RQ4, RQ5) degrade gracefully — the
> affected model's series truncates, the others continue. RQ6 (characterization) is
> unaffected. RQ1 (cross-model herding) is a cohort-level construct and does not
> degrade per-model: a mid-window cohort-size reduction changes the set of model
> pairs the herding statistic is computed over, so RQ1's headline statistic is not
> directly comparable across a cohort-size change. RQ1's handling of a mid-window
> cohort reduction is a registered open methodology item, to be finalized at the
> September methodology review and locked in the OSF pre-registration before the
> confirmatory phase begins. No estimator is claimed in the interim.
>
> **Monitoring mechanism.** A weekly model-lifecycle monitoring job watches all six
> providers' model-lifecycle and deprecation pages, alerting on a lifecycle status
> change, a retirement-floor date change, or a retirement announcement.
> *Gemini alias coverage:* the job explicitly covers `gemini-3.1-pro-preview`,
> watching Google's model documentation and release notes for (a) a published change
> to the underlying build the alias resolves to, (b) a deprecation or retirement
> notice, and (c) the release of a GA `gemini-3.1-pro`. This is the compensating
> control for the fact that Google's API echoes only the alias and no dated build, so
> a repoint of the underlying build is not detectable from API response metadata. The
> control is **partial**: it detects announced changes, not a fully silent,
> unannounced repoint; that residual is disclosed as a limitation. The monitor's
> state as of the OSF deposit date populates the exposure table. Implementation is an
> Operations task.
>
> **September pre-read.** This disclosure is a component of the September
> data-integrity pre-read.

---

## Part 3 — Gemini snapshot-identifier limitation (for the data-integrity section)

The original Flag 2 "resolved-identity check exception" depended on an uncommitted
safeguard. Restated as a self-contained data-integrity limitation:

> **Gemini snapshot-identifier limitation.** Gemini 3.1 Pro is pinned to the alias
> `gemini-3.1-pro-preview`. Google's API echoes only this alias in response metadata
> and exposes no dated, immutable build identifier. Consequently, if Google repoints
> the alias to a different underlying build, the change is not detectable from API
> response metadata, and Gemini's snapshot stability cannot be verified from the
> logs. This is a known data-integrity limitation. The compensating control —
> model-lifecycle monitoring of the alias — is specified in the Forced-Change /
> Deprecation Exposure section and is partial (it detects announced changes, not a
> fully silent repoint).

---

## Part 4 — Reconciliation audit: result

The reconciliation audit is complete. It compared the RQ, methodology, and
data-integrity content discussed in this chat against the committed
`PRE_REGISTRATION.md`, `METHODOLOGY.md`, and `v1.json`. The chat-vs-repo gap is
three items:

1. **RQ1 cohort-reduction estimator** — absent everywhere. The committed RQ1 entry
   specifies standard pairwise herding only; it contains no estimator for a
   mid-window cohort-size change. Registered as an open methodology item, to be
   finalized at the September methodology review and locked in the OSF
   pre-registration before the confirmatory phase. Part 2's per-RQ degradation
   paragraph reflects this; no estimator is claimed in the interim.
2. **METHODOLOGY "resolved-identity check" section** — absent. This package does not
   depend on it: Part 3 states the Gemini snapshot-identifier limitation
   self-contained.
3. **Forced-Change / Deprecation Exposure section** — absent. Authored by Part 2 as
   new infrastructure.

Everything else is committed and verified: all six RQ entries, the per-model
API-configuration table (its placeholder cells not yet filled with the verified
values — a separate pre-deposit edit), the three METHODOLOGY sections targeted by
Corrections 1 and 2, the full Phase A data-integrity disclosure, and the entire RQ5
change.

With audit items 2 and 3 handled by this package and audit item 1 registered as a
September open item, the package — Corrections 1 and 2, re-grounded Correction 3,
the §3.6 correction (Part 1B), the research_metrics.py docstring correction
(Part 1C), the docs/RQ5-RQ6-specification.md superseded-by pointer (Part 1D), the
corrected docs/RESEARCH_QUESTIONS.md RQ6 entry (Part 1E), the Forced-Change section,
and the Gemini data-integrity item — is clear to land atomically. The pre-landing
grep for the false temperature premise is fully covered: all seven occurrences have
ratified handling in this commit.
