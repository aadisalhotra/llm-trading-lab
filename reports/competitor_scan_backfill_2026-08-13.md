# Competitor scan — one-off 90-day backfill + retroactive triage

**Generated:** 2026-08-13 20:2x UTC · **Window:** last 90 days (2026-05-15 → 2026-08-13) · **Scope:** one-off, run once under the 2026-08-13 Research triage spec.

Not a weekly digest and not part of the weekly series — the weekly cadence continues in `competitor_digest_YYYY-WW.md`. This exists because the extended monitor's first weekly window (week 2026-33) returned zero hits, and a zero-hit week is indistinguishable from a broken query without a wider run to compare against. It also carries the retroactive triage of weeks 22–33, which has no other home in the repo.

**No rows from this run were written to `competitor_index.jsonl`.** A backfill through `run()` would stamp every historical paper with the *current* scan week, mislabelling 90 days of publications as week 2026-33. The series stays honest; this file is the record instead.

---

## Result

**28 papers** matched the category-scoped query over 90 days: **1 escalate · 12 digest · 15 silent**.

This is the control that proves week 2026-33's zero is a quiet week rather than a dead query.

## ESCALATE — full payload

### CLQT: A Closed-Loop, Cost-Aware, Strategy-Consistent Benchmark for Diagnostic Evaluation of LLM Portfolio-Management Agents

- **Link:** https://arxiv.org/abs/2606.29771v2
- **Venue:** arXiv (cs.AI)  ·  **Categories:** cs.AI, cs.LG, q-fin.CP, q-fin.PM
- **Date:** 2026-06-29
- **Criteria met:** (a) >=2 LLMs on identical inputs, cross-model agreement/convergence/herding
- **Threatened RQ(s):** RQ1 (cross-model decision convergence)
- **Assessment:** Meets (a) of the escalation criteria on title+abstract. Threatens RQ1 (cross-model decision convergence).

**Abstract (opening):**

> LLM agents are increasingly cast as autonomous portfolio managers, and benchmarks have moved from financial question-answering to sequential trading. Yet most still rank agents by returns over a fixed window, a weak proxy: the market path dominates a period's return, and apparent alpha can dissolve once look-ahead leakage is controlled. We introduce CLQT, which reframes closed-loop trading evaluat…

### Attribution caveat — read before ruling

**The escalation is right; the RQ attribution may not be.** The abstract establishes beyond doubt that this is closed-loop *sequential trading* evaluation for *LLM portfolio-management agents*, with look-ahead-leakage control and hash-chained per-round decision records — squarely competitor-class infrastructure, and the single most relevant paper the monitor has surfaced in 90 days.

What I could **not** confirm from the abstract is that CLQT measures *cross-model agreement*. Criterion (a) fired on the co-occurrence of multi-model and agreement-family signals in the full text, but "strategy-consistency scoring" is consistency of an agent with its own stated strategy, which is not RQ1's construct. So **"Threatens RQ1 (cross-model decision convergence)" is the machine's attribution and should be treated as unverified.** The paper may threaten the lab's *evaluation-infrastructure* positioning more than any single registered RQ.

Triage is deliberately biased toward over-escalation: a false escalate costs one read, a false silent costs the priority date. This is that bias working as designed, and the caveat is the reason a human reads before the hub rules.

## Weekly-digest tier (12)

- **OpenPM: Auditable Point-in-Time Evaluation for LLM Portfolio-Management Agents** — arXiv (cs.CE), 2026-08-06 — https://arxiv.org/abs/2608.09988v1
- **A Consensus-Based Framework for Relative Preference Evaluation of Large Language Models** — arXiv (cs.CL), 2026-07-19 — https://arxiv.org/abs/2607.21632v1
- **Fin-Analyst at FinMMEval 2026 Task 3: A Live Hybrid Trading Agent with LLM Specialists and Rule-Based Signals** — arXiv (cs.CL), 2026-07-14 — https://arxiv.org/abs/2607.12233v1
- **SynthAVE: Scalable Synthetic Labeling for E-Commerce with LLM-Arena Validation** — arXiv (cs.CL), 2026-07-08 — https://arxiv.org/abs/2607.07469v1
- **When Calibration Rankings Reverse: Accuracy-Controlled Evaluation for Fair Comparison of LLMs** — arXiv (cs.CL), 2026-06-29 — https://arxiv.org/abs/2606.30814v1
- **Knowing You Is Everything: LLM Agents Achieve Near-Perfect Profile-Consistent Reaction Prediction in Social Media Simulation** — arXiv (cs.HC), 2026-06-19 — https://arxiv.org/abs/2608.07498v1
- **CREDENCE: Claim Reduction for Decomposition & Enhanced Credibility -- Semantic Metrics and Convergence Analysis** — arXiv (cs.CL), 2026-06-18 — https://arxiv.org/abs/2606.19819v1
- **From Knowing to Doing: A Memory-Controlled Benchmark for LLM Trading Agents on Stock Markets** — arXiv (cs.AI), 2026-05-27 — https://arxiv.org/abs/2605.28359v1
- **DeepWeb-Bench: A Deep Research Benchmark Demanding Massive Cross-Source Evidence and Long-Horizon Derivation** — arXiv (cs.AI), 2026-05-20 — https://arxiv.org/abs/2605.21482v1
- **NewsLens: A Multi-Agent Framework for Adversarial News Bias Navigation** — arXiv (cs.CL), 2026-05-17 — https://arxiv.org/abs/2605.17364v1
- **Representation Signatures and Risk-Feedback Alignment in LLM Trading Agents** — arXiv (cs.LG), 2026-05-16 — https://arxiv.org/abs/2605.28850v2
- **The Alpha Illusion: Reported Alpha from LLM Trading Agents Should Not Be Treated as Deployment Evidence** — arXiv (cs.CE), 2026-05-16 — https://arxiv.org/abs/2605.16895v1

## Silent log (15)

Logged, not surfaced.

- Evaluating LLM Trade-offs for Enterprise Automation: Lessons from Workflow Generation in a Production Enterprise Platform — 2026-08-04
- OPERA: Offline Policy-guided Expert Routing and Adaptation for Universal Biomedical Image Analysis — 2026-07-27
- Adversarial Test-Hardening for AI-Written Code: An Instrument Autopsy and a Pre-Registered Causal Estimate of the Critic Loop — 2026-07-25
- Retrieval-Augmented Generation in LLMs for Mental Health: Quantifying the Incremental Contribution of Retrieval Within a Layered Safety Architecture — 2026-07-17
- When LLMs Agree, Are They Right? Auditing Self-Consistency and Cross-Model Agreement as Confidence Signals — 2026-07-09
- Open Problems in Constitutional Preference Reconstruction — 2026-06-29
- Who Owns the AI Recommendation? A Multi-Industry Empirical Map of Brand Category Ownership Across Large Language Models — 2026-06-22
- Measuring Cognitive Engagement in Collaborative Discourse with an Extended ICAP Framework: Comparing Human Annotation, In-Context Learning, and Reflective LLM Agents — 2026-06-07
- Beyond Agent Architecture: Execution Assumptions and Reproducibility in LLM-Based Trading Systems — 2026-06-06
- Automated Essay Scoring and Language Certification: Assessing Generalizability, Agreement and Validity for French — 2026-06-01
- Benchmarks for Vision-Language Models in Urban Perception Should Be Reliability-Aware and Negotiated — 2026-05-30
- Page image classifier fine-tuned on century-spanning archives of scanned documents for further content-specific processing — 2026-05-25
- Ontology-constrained multi-LLM scoring of hypothesis support in the predictive processing literature — 2026-05-23
- Playing Devil's Advocate: Off-the-Shelf Persona Vectors Rival Targeted Steering for Sycophancy — 2026-05-20
- Toxicity in Twitch Chats: An LLM-Based Analysis Across Gaming Communities — 2026-05-18

---

## Retroactive triage — weeks 22–33

**Source:** digest markdown (structured index absent) · **records:** 3 · **weeks:** 2026-23, 2026-29, 2026-30

**Counts:** 0 escalate · 2 digest · 1 silent.

**Zero ESCALATE-class hits in the twelve-week history.** Nothing published between weeks 22 and 33 and captured by the old query meets criteria (a)–(d).

| Week | Tier | Paper |
|---|---|---|
| 2026-23 | digest | From Knowing to Doing: A Memory-Controlled Benchmark for LLM Trading Agents on Stock Markets |
| 2026-30 | digest | Fin-Analyst at FinMMEval 2026 Task 3: A Live Hybrid Trading Agent with LLM Specialists and Rule-Based Signals |
| 2026-29 | silent | Resample or Reroute? Budget-Aware Test-Time Model Selection for Large Language Models |

### Two limits on this result, stated rather than buried

1. **`competitor_index.jsonl` did not exist**, so the triage ran against the digest markdown. The index feature landed in `0424fd8b` (2026-08-10 22:36Z), hours after that week's scheduled run at 15:04Z, and no scan executed between. The fallback parser reports which source it used.
2. **Digest abstracts truncate at ~600 characters.** A signal living only in a cut tail would be missed. Only 3 papers exist across the 12 weeks, so the exposure is small — but it is real, and the 90-day scan above is the compensating control: it re-reads full abstracts from the API and covers weeks 20–33 on the current criteria.

---

*Generated by `scripts/competitor_monitor.py` (`fetch_arxiv --days 90` + `--retro-triage`). One-off; the weekly series is unaffected.*
