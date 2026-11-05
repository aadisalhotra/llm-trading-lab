# Competitor-Monitor Escalation Resolution — CLQT (arXiv 2606.29771)

- **Artifact:** `reports/competitor_assessments/clqt_arxiv_2606.29771.md` — the monitor escalation's documented resolution. Committed per the standing convention.
- **Paper:** Qu & Chen, "CLQT: A Closed-Loop, Cost-Aware, Strategy-Consistent Benchmark for Diagnostic Evaluation of LLM Portfolio-Management Agents," arXiv 2606.29771 (v1 2026-06-29; flagged version v2, revised Aug 2026). cs.AI, cs.LG, q-fin.CP, q-fin.PM.
- **Escalation basis:** criterion (a) — multi-model closed-loop trading. **Resolution: NOT A COMPETITOR — citable adjacent benchmark.** Criterion (a) fails on its operative clause: the paper never measures cross-model agreement.
- **Verification basis:** full text (HTML, arXiv), not the abstract — §2 (related work/Table 1 landscape), §3.5 (ConsistencyScore definition), §5.5 (live track), §6.4 (APM-CS axes), §7 (campaign), §8.8 (future work).

## The decisive construct check
"Strategy-consistency scoring" is **within-agent**: ConsistencyScore 𝒞ₜ = 0.25·(1−style drift) + 0.25·turnover consistency + 0.25·signal adherence + 0.25·mandate alignment (§3.5) — an agent's fidelity to *its own* strategy and mandate across rounds. The five-axis scorecard (Coherence, Acuity, Composure, Discipline, Reliability; §6.4) is per-agent throughout; the only cross-model operation in the paper is cohort-percentile mapping for radar display. Their headline finding — the +0.33/+0.34 agreement-vs-judge coherence gap — is a within-agent stating-versus-doing deficit. Nowhere does CLQT ask whether different models make the *same* decisions.

## Per-RQ overlap assessment
- **RQ1 (herding — primary): zero overlap; CLQT becomes the gap-defining citation.** They run 5–6 models on identical data in a closed loop (§7.2, §5.5) and never measure cross-model convergence. The field's most diagnostic closed-loop benchmark scores every agent individually — our axis is unoccupied, citable as such.
- **RQ2 (disposition): absent.** No gain/loss realization-asymmetry measurement anywhere.
- **RQ3 (calibration): absent.** No stated-confidence elicitation tested against outcomes; their "Coherence" is narrative-allocation consistency, LLM-judged — a different construct.
- **RQ4 (factor exposure): absent.** Passive style exposure appears only via citation to leakage work (Profit Mirage; memory-controlled masking); no factor regressions on agents.
- **RQ5 (drawdown response): low adjacency — differentiate and cite.** Their Composure axis regresses per-round turnover on a volatility proxy (overreaction; §6.4③); ours regresses trade-driven concentration change on drawdown depth (path dependence). One related-work sentence separates the constructs.
- **RQ6 (reproducibility): low adjacency — motivating, not preempting.** Their repeated-run protocol documents run-to-run variance (Sharpe ±0.4–1.2; APM-CS up to ~25 percentile points across identical-config runs; §7.7) used as an ablation noise band — evidence that non-determinism is first-order, without characterizing per-model divergence at deployed configuration as an estimand. Cite as RQ6 motivation.

## Citable corroborations of this project's infrastructure findings
1. **Output headroom / cap-equity:** their qwen3-235b at a 2048-token cap truncated structured JSON mid-answer; raised to 8192 it completed cleanly (§7.5, §7.8) — the effective-budget principle found independently.
2. **Model-specific schema unreliability:** gemini-2.5-flash-lite's persistent structured-schema parse failures despite retries (§7.5) — the same failure family as our Gemini remediation history.
3. **Post-cutoff live data as decontamination:** their live track's rationale (§5.2, §5.5) matches our look-ahead handling.
Additional kinship, citable in methods: their hash-chained DecisionRound audit trail (§4.4) parallels our integrity ledger + conservation audit.

## Differentiation summary (for the paper's related work)
CLQT: diagnostic *instrument* — per-agent capability profiles, 26 bi-weekly backtest rounds + 9 live paper-broker days, no pre-registration, virtual $1M. This project: pre-registered *behavioral audit* — cross-model behavioral estimands under identical inputs, ~4,700 thirty-minute decisions per model over the experiment, live real-capital confirmatory phase. Complementary designs; disjoint claim spaces; their Table 1 benchmark landscape serves as our related-work survey anchor.

## Framing consequence
Improves our position: the frontier benchmark paper itself argues evaluation must move from returns-leaderboards to behavioral/process diagnosis — then scores agents individually. We ride the turn they document and hold the axis they leave empty, plus pre-registration, longitudinal depth, and real capital.

## Watch item (monitor standing terms)
§8.8 future work lists a **regime-stratified facet** and pairwise-dominance passes; the paper was revised in August — the authors are active. Standing queries: CLQT successors and Qu/Chen follow-ons, escalating on any movement onto regime-stratified *behavioral* analysis or cross-model agreement measurement.

## Disposition
Logged citable-adjacent in `competitor_index`; enters related work as the landscape anchor and RQ1 gap citation; no registered-RQ consequence; no pre-registration change.

## Provenance
- 2026-08-13: escalated by the monitor's 90-day live scan (criterion (a)); hub flagged the construct question.
- 2026-08-14: full-text verification and per-RQ assessment delivered (Research); verdict citable-adjacent; resent 2026-08-22 in the receipt-numbered batch after relay loss.
- 2026-11-04: produced as a committed artifact (Direction 2 remediation; the escalation's resolution previously had no repo path).
