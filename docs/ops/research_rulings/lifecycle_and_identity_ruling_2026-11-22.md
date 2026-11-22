# Lifecycle Facts of 2026-11-22 — Expected Truncation, the xAI Identity Check, and RQ6's Second Run

- **Status:** Research's rulings of 2026-11-22 on H-RES-03. They are folded into `docs/PRE_REGISTRATION.md` §3.10, the identity-monitoring registration, `rq1_cohort_reduction_and_decision_domain.md` and the RQ6 texts at the December freeze. §2's decision rule is fixed here before the probe it governs is run. Zero markers.
- **Basis:** the lifecycle facts in H-RES-03, read by the hub from provider pages on 2026-11-22; `identity_monitoring_table_v3.md`; `docs/PRE_REGISTRATION.md` §3.10 as drafted in the 2026-11-26 splice; the NYSE calendar.

## 1. The forced-change table states Claude truncation as expected

**Agreed. It is the expected case and is written as one.**

1. **What is expected.** Recent Anthropic retirements came 12 to 14 months after release. `claude-opus-5-5` and `claude-sonnet-5-5` were released on 2026-09-22 and 2026-09-28, so retirement is expected between late September and late November 2027, inside a window that ends on 2028-01-03. The two Claude arms are expected to run between about 180 and 225 of its 252 sessions.
2. **What that leaves.** A cohort of five for the first eight to ten months, then three. Every per-model minimum is met well before the floors. The within-vendor pair ends at the first of the two retirements.
3. **The other three rows state what the pages state:** OpenAI, no date and at least six months' notice for a generally available model; Google, no shutdown date announced and no minimum notice stated; xAI, no notice and no stated policy, with the 2026-05-15 retirements on about nine days' notice as its only record.

## 2. `grok-4.7` and a redirect that echoes the requested name

### 2.1 What is already registered

Behavioural-profile monitoring with the cross-model control arm is the registered primary instrument for every cell (`identity_monitoring_table_v3.md` §2). `grok-4.7` has it like any other arm. No new behavioural instrument is invented for it: an undemonstrated instrument adds confidence without adding detection.

### 2.2 The doctrine's trigger is not met, and is close

The registered doctrine is symmetric: a demonstrated silent substitution on any line triggers the freezability review the DeepSeek line received. The 2026-05-15 event was announced, on about nine days' notice, and touched no identifier in this cohort. It is an announced retirement with a redirect, not a silent substitution. It is also the nearest conduct on record for any confirmatory provider. A substitution on `grok-4.7` without notice would put the xAI line where the DeepSeek line is.

### 2.3 A probe, with its decision rule fixed first

**xAI's redirect exists today, so what it returns can be observed.** No provider identity field in this project has ever been tested against an actual substitution. This one can be.

**The probe.** Before 2026-12-07, off-cycle and tagged as an induced test, Operations calls one identifier that xAI retired on 2026-05-15, and calls `grok-4.3` directly, several times each. It records the model named in each response and the `system_fingerprint`.

**The rule, by what the probe shows.**

| Case | The redirected response | What governs `grok-4.7` |
|---|---|---|
| A | names the served model, not the one requested | The echoed-name check detects a redirect of this kind. It is the primary field; the fingerprint is logged as a corroborant. |
| B | names the requested model, and carries the fingerprint of the served one | The fingerprint is the detector. Any change in `grok-4.7`'s fingerprint is a candidate identity event. The same day, xAI's lifecycle pages are checked and the profile is read against the control arm. |
| C | is indistinguishable in both fields | A redirect cannot be seen in response metadata. The profile instrument and the announcement check of §2.4 are the only controls. The deposit says so, and the arm is recorded as the cohort's weakest identity cell. |

**In every case** the two fields are captured on both legs, screening and decision, for `grok-4.7`. The monitoring table's scope statement records that the screening leg is unobserved cohort-wide. For this arm that gap is closed. Closing it for all five while the adapters are being rebuilt is recommended.

**A limit, stated.** The probe shows how xAI's redirect behaves now, on those identifiers. It does not bind xAI's behaviour on `grok-4.7` later.

### 2.4 The audit interval has to be shorter than the notice

The registered lifecycle audit is by hand at each monthly build, with a weekly automated monitor registered as a December 2026 upgrade. xAI's only recorded notice is about nine days. A monthly audit can miss a retirement entirely, and a weekly one sees it once.

**Rule.** For each confirmatory provider, the interval between automated checks of its lifecycle page is at most one third of the shortest notice that provider has given or states. For xAI that is three days. Where a provider states no notice and has no record, three days applies as well. The monitor is in place before 2027-01-04.

**A detected retirement inside the window truncates the arm** at the last session before it takes effect, as registered.

## 3. The cohort-reduction estimator

**The estimator needs no change.** It is defined on the pairs present at each tick, its test is valid for any membership path, and its three companions exist for this case (`rq1_cohort_reduction_and_decision_domain.md` §1). Three things are stated beside it.

1. **The expectation is registered.** Both Claude arms are expected to leave in the last quarter of the window. On that path about nine tenths of the headline's pair-session weight comes from the five-arm segment.
2. **Nothing is conditioned on when it happens.** Segment boundaries are the ledgered truncation dates. The window is not shortened to end before a retirement, and no analysis is run early because one is announced.
3. **The composition change will coincide with calendar time.** A late shift in agreement could be either. The segment table separates the two, and the headline is never read as if it did.

## 4. RQ6's second run comes before the first floor

**A retired model can never be replayed.** RQ6's second confirmatory-window run is registered at roughly month 9, which is about October 2027 and after both Claude floors.

1. **The second run is completed between 2027-09-04 and 2027-09-21.** That is the start of month 9 and the day before the earliest floor. It is still "roughly month 9".
2. **A pre-retirement run.** When a retirement is announced for any arm, an RQ6 run for that arm is made before the retirement takes effect, on the registered design. Sixty days' notice allows it for Anthropic. Nine days may not for xAI, which is one more reason for §2.4.
3. **The first run,** at roughly month 3, is unchanged.
