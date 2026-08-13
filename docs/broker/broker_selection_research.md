# Broker Selection Research — Phase B Execution Venue

Consolidated findings with per-claim verification status · 2026-08-12 · cited by the
Phase B capital-structure ruling

**Provenance.** Consolidates: the 2026-08-10 brokerage comparison research
(search-based), subsequent verified corrections (2026-08-11/12), Operations' in-venue
and documentation verifications, and both brokers' support/inquiry responses. Each
claim carries a verification tag:

| Tag | Meaning |
|---|---|
| `[PRIMARY]` | Regulator or broker's own published documentation |
| `[VENUE]` | Verified against the live account or API |
| `[SUPPORT]` | Broker support statement, human confirmation pending |
| `[SEARCH]` | Third-party reporting, uncorroborated |

---

## 1. Requirements

Six segregated books with independent cash/position attribution; fractional share
trading (structurally mandatory at $4,000 books — at a 2% position, 38 of 43 priced
universe names exceed one whole share `[VENUE]`); programmatic per-book API order
routing from ephemeral CI runners; adult-owned structure (custodial accounts are
cash-only at every broker surveyed — no margin, no shorting `[PRIMARY]`).

## 2. Regulatory baseline

**The pattern-day-trader rule no longer exists.** FINRA Rule 4210 amendments —
retiring the PDT designation, the day-trade count, and the $25,000 minimum — effective
**2026-06-04** (SEC Release 34-105226, 2026-04-14; FINRA Regulatory Notice 26-10)
`[PRIMARY, verified against sec.gov and finra.org]`. Replaced by a risk-based intraday
margin framework; broker phase-in permitted through 2027-10-20, so per-broker
implementation must be confirmed in writing (gate d).

The **$2,000 margin minimum** (4210(b)(4)) is a separate, pre-existing rule and still
applies to margin-enabled accounts `[PRIMARY]`. The Reg-T/FINRA **$2,000 equity floor
for short selling** is regulatory, not broker policy `[PRIMARY]`.

## 3. Alpaca (incumbent paper venue)

**Fractional shorts: prohibited.** *"We do not support short sales in fractional
orders"*; *"All fractional sell orders are marked long"* `[PRIMARY, docs verbatim]`.
Whole-share shorts on ETB names only; the 100-share round lot applies to HTB locates,
not ETB.

**Universe:** 79/79 fractionable (longs), 78/79 shortable-ETB (USO fails)
`[VENUE, 2026-08-11 — list subject to change]`.

**Framework implementation:** live since **2026-06-04** for Trading API and Broker API;
sandbox 2026-06-22; deprecated fields removed by 2026-07-06 `[PRIMARY]`.
*Correction record:* Alpaca's AI support reply of 2026-08-12 stated *"as of August 13,
2026"* — **date fabricated**; no August date appears in any published material.

**Account structure:** one live account per retail individual `[SUPPORT, 2026-08-12;
human confirmation requested]`. Not stated in any published Alpaca documentation; the
sole public assertion is a commercially-motivated multi-accounting vendor
`[SEARCH, discounted]`. Treated as unpublished operational policy.

**Entity path: capital-infeasible.** Business trading accounts are invite-only beta,
$30,000 minimum, professional data classification `[PRIMARY]`. Six entities ≈ $180,000
floor.

**IMD/restriction contagion:** described at individual-account level, no contagion
indicated `[SUPPORT, uncorroborated — not load-bearing at Alpaca given the structure
finding]`.

**Live path status:** the pipeline's Alpaca integration has run only `mode: "paper"`;
the live branch is validated against nothing `[VENUE, repo-verified]`.

## 4. IBKR

**Fractional shorts: supported.** *"Short sales in fractional shares of eligible
stocks"* with margin + fractional permissions `[PRIMARY, KB verbatim]`; demonstrated
empirically in the available account (0.0032-share TSLA round-trips, both directions,
trade log 2026-07-01) `[VENUE]`.

**Six-book structure: exists** — Friends-and-Family advisor structure, ≤15 accounts,
exempt-from-registration class, per-order sub-account routing via the API `account`
field `[PRIMARY]`.

**The available account:** adult-owned individual, own capital, no outside
participants, $25,058.74, margin + short permissions active, fractional enabled; prior
personal day-trading use ending `[VENUE, 2026-08-11]`.

**API access from CI: unresolved, and decisive.** Client Portal Gateway requires
browser login and same-machine calls, sessions ≤24h — categorically incompatible with
ephemeral Actions runners `[PRIMARY]`. OAuth is the only CI-compatible route; IBKR's
auth documentation enumerates OAuth account types as institutional classes only —
individual accounts appear under CPGW alone, neither excluded nor listed for OAuth
`[PRIMARY, docs enumeration]`. First-party eligibility inquiry prepared for
account-holder send (`docs/broker/ibkr_oauth_application.md` §5); third-party track
prices at 8–14 weeks `[PRIMARY]`, which forecloses the 2026-09-15 gate by IBKR's own
best case.

**Fallback if OAuth is unavailable:** VPS-hosted gateway — a self-chain architecture
rewrite introducing a persistent single point of failure the current design
deliberately avoids.

## 5. Granularity at $4,000/book (whole-share shorting, Alpaca)

20% sleeve = $800. Against live prices, 43 priced names (the 43 of 79 universe names
with live price checks as of 2026-08-11; §3's eligibility counts cover the full 79)
`[VENUE, 2026-08-11]`:

| Bucket | Count |
|---|---|
| Unshortable (LLY $1,231.94, CAT $837.58) | 2 |
| One share = 50–76% of sleeve | 11 |
| 2–5 shares | 18 |
| 6+ shares | 12 |

This reproduces the effective-parity problem the 2026-08-05 ruling identified, at the
higher capital level.

## 6. Structural conclusion (as of 2026-08-12)

The six-book segregated structure exists at exactly one venue: **IBKR's
Friends-and-Family advisor structure** — which appears on IBKR's OAuth 1.0a supported
account-type enumeration `[PRIMARY]`, making the end state plausibly OAuth-eligible by
IBKR's own documentation. Two variables remain open, both inquiry-bounded: **(a)** the
§5 individual-account OAuth question — an interim question governing whether adapter
work can start against today's account; **a negative answer does not foreclose the
branch**, since the end state is the advisor class, not the individual class;
**(b)** the F&F stand-up timeline — process and duration to establish the structure,
unpriced by IBKR's public docs, **covered by the account-holder's message-centre
inquiry, ticket #T976605, sent 2026-08-11 23:00** (§7). Alpaca offers no retail path to six books at any capital level below
the entity floor. The 2026-09-15 decision therefore chooses between
**IBKR-F&F-if-OAuth-clears** and **a redesign of the book structure itself**; both
brokers require building and validating a live execution path from scratch.

## 7. Inquiry log

**Alpaca:** PI inquiry sent 2026-08-12; AI reply same day; corroboration complete (§3);
human confirmation of the one-account rule requested on-thread.

**IBKR — message centre: SENT**, ticket **#T976605**, account holder, **2026-08-11
23:00**. Five questions: OAuth route; F&F qualification + per-book API routing + Reg-T
application level; restriction contagion (quoting *"across all associated margin
accounts"*); framework implementation status; fractional long + short via API on this
account type. Covers gates (b), (c), (d), the contagion question, and the F&F stand-up
timeline in one ticket. *Framing exposure:* pairs an automated-trading application with
F&F in one general-support message — routing risk assessed low on that channel,
mitigated by the separate first-party email; detail at
`ibkr_oauth_application.md` §6.2.

**IBKR — first-party OAuth eligibility email: PENDING**, account-holder sends, packet
cleared 2026-08-12 and F&F-free as ruled. **The one outstanding send.**
