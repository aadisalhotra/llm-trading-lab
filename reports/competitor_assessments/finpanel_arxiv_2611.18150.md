# Competitor-Monitor Escalation Resolution — FinPanel (arXiv 2611.18150)

- **Artifact:** `reports/competitor_assessments/finpanel_arxiv_2611.18150.md` — the monitor escalation's documented resolution. Committed per the standing convention.
- **Paper:** "Who Should Be on the Panel? Task-Conditioned Persona Selection for LLM Trading Agents," arXiv 2611.18150v1, q-fin.TR, dated 2026-11-13 (identifiers from the week 2026-47 escalation alert, scanned 2026-11-16 23:16 UTC).
- **Escalation basis:** criterion (b) — disposition / calibration / drawdown response in LLM trading agents over a series; threatened RQ5.
- **Resolution: FALSE POSITIVE. Downgraded to the digest tier.** Ruled by the hub on a full read, 2026-11-16; recorded by Research.
- **Verification basis, stated:** the paper facts below are **the hub's full-text reading**. Research could not open the arXiv page on 2026-11-16 (the fetch was rate-limited twice) and has not read the paper. What Research verified independently is the scanner side (§3), against `scripts/competitor_monitor.py` at origin `3d1c04a`.

## 1. Why it is not a competitor (hub's reading)

- **One backbone.** A single model (gpt-oss-120b); prior agent frameworks are re-implemented on it. There is no cross-model comparison of any kind.
- **Design.** A backtest over 2025 on eight US stocks, each traded separately in a long-or-flat position; there is no cross-stock portfolio. Authors Yang et al. (Tokyo); accepted at the ICAIF 2026 AI4F workshop.
- **Drawdown appears only as a performance metric** (maximum drawdown beside return figures). No behavioural response to drawdown is measured — nothing about how an agent's risk-taking changes with drawdown depth or path.

## 2. Per-RQ overlap

| RQ | Overlap | Reason |
|---|---|---|
| RQ1 (cross-model herding) | None | One backbone; persona agreement inside one model is not cross-model convergence |
| RQ2 (disposition) | None | No realization-asymmetry measurement |
| RQ3 (calibration) | None | No stated confidence tested against outcomes |
| RQ4 (factor exposure) | None | No portfolio: each stock is traded alone, long or flat |
| RQ5 (drawdown response) | None | MDD is an outcome metric, not a behavioural estimand |
| RQ6 (non-determinism) | None | Not studied |

**Related-work use (optional):** its look-ahead defence — a backbone whose training cutoff precedes the test year — is the backtest-side answer to contamination. This project's answer is structural: decisions are made forward in time, so no cutoff argument is needed. One contrasting sentence in the methods; not a required citation.

## 3. Why the scanner escalated — two causes, not one

Criterion (b) fires when an LLM signal, a trading signal, a behaviour signal and a series signal all match title plus abstract (`scripts/competitor_monitor.py`, `triage`).

1. **"drawdown" as a reported metric** matched the behaviour signal (`_SIG_BEHAV` contains the bare token `drawdown`). This is the cause the hub named.
2. **"Panel" in the title matched the series signal.** `_SIG_SERIES` contains the bare token `panel`, intended as panel data; here it is a panel of personas. Research confirmed this on the live `triage()`: the paper's title plus any abstract sentence containing "drawdown" escalates on (b) with no other series word present.

Fixing only the first leaves the second in place: any multi-agent "panel" paper that mentions calibration would still escalate.

## 4. Triage-spec amendment, criterion (b) (Research; for the December scanner change)

> **(b), amended.** Disposition, calibration, or a **behavioural response to drawdown** in LLM trading agents over a series. Drawdown qualifies only with response language — the agent's behaviour during, after or as a function of drawdown (risk-taking, de-risking, position sizing, exposure, path dependence, "drawdown response"). Drawdown reported as a performance metric (maximum drawdown, MDD, beside Sharpe or return) does not qualify. The series signal requires "panel data" or "panel regression"; the bare word "panel" does not count.

Two riders:

1. **Fail soft, not silent.** An LLM-trading paper whose only (b) hit is metric drawdown lands in the **digest** tier with the note "drawdown reported as a metric", so the weekly read can catch a wrong demotion. It must never land silent.
2. **Do not tighten further.** A false escalation costs one read; a missed competitor costs the novelty claim. Calibration keeps its wide match. This amendment removes one metric homonym and one noun homonym and nothing else.

**Evidence for the amendment (run by Research on the live `triage()` at `3d1c04a`, with a candidate rule; all abstracts constructed, since the real abstract could not be read):**

| Case | Current (b) | Amended (b) |
|---|---|---|
| This paper's title + a metric-only abstract | fires | does not fire; lands digest |
| Same, with "daily" in the abstract | fires | does not fire; lands digest |
| "adjust risk-taking after drawdowns over time" | fires | fires |
| "position concentration following deep drawdowns", longitudinal | fires | fires |
| Disposition effect across sequential episodes | fires | fires |
| The existing test fixture (`test_escalate_criterion_b_behavioral_over_a_series`) | fires | fires |

Operations owns the patterns and the tests. Required test additions: this paper's title with a metric-only abstract must triage to digest; a behavioural-drawdown abstract must still escalate; the existing 36 tests must pass unchanged. The scanner's index stores abstract hashes, not abstracts, so the amendment cannot be replayed over past scans; the first real check is this paper's own abstract, which Operations has in the scan run.

## 5. Provenance

- 2026-11-16 23:16 UTC: escalated by the weekly scan on criterion (b), RQ5.
- 2026-11-16: read in full by the hub and ruled a false positive; downgraded to digest; scanner note for December.
- 2026-11-16: recorded by Research. The second cause (the `panel` token) found on reading the triage code; criterion (b) amended as above.
- 2026-11-16 (re-issue): the first issue said "single-stock", relaying the hub's first summary. The paper uses eight US stocks, each traded alone. Caught by Operations' rule-10 check; §1's design line and the RQ4 row corrected. The ruling is unchanged. Also recorded: the hub ran the live `triage()` on the paper's real title and abstract. It escalates on (b); the behaviour signal matches only "drawdown", and the series signal matches both "panel" and "daily". On the real abstract the `panel` fix alone therefore changes nothing and the metric-drawdown rule is what demotes it. The real title plus the abstract's drawdown sentence is the December regression fixture and replaces the constructed cases in §4.
