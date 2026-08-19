# Broker Confirmations — August 2026

Primary correspondence record for the Phase B venue decision · landed 2026-08-19 ·
cited by the Phase B capital-structure ruling, the forced-change contingency
(venue-regulatory trigger), and the 2026-09-15 branch decision

**Purpose.** Four dated, written broker statements received 2026-08-17 — two from
Interactive Brokers, two from Alpaca — held as a citable record with stable item
identifiers. These are the "dated, in-writing broker confirmations" the forced-change
contingency's venue-regulatory trigger requires. This file is the record; the
structural argument built on it lives in `broker_selection_research.md`.

**Provenance.** Relayed to Operations as **Payload 4** (hub, 2026-08-19), together
with the four rulings issued alongside it. Those rulings — cited throughout this file
as ①–④ — are the **Payload 4 ruling set**, and that set is what every ruling citation
below refers to. Text is reproduced as relayed. Where the relay inserted bracketed
context into a quotation — e.g. `[For margin accounts with Securities NLV below USD
25,000:]` in D-2 — the brackets are the relay's, not the broker's; unbracketed text
inside quotation marks is the broker's wording.

**Ruling-number scoping.** Circled ruling numbers are scoped to the dispatch that
issued them and are **never global identifiers**. Every ruling cited in this file
belongs to the **Payload 4 ruling set** (hub, 2026-08-19) and names that set at each
citation site. A bare "①" carries no meaning without its issuing dispatch, and this
file's ①–④ must not be read against any other dispatch's numbering.

---

## How to cite

**Cite the item, never the date alone.** Two distinct IBKR messages and two distinct
Alpaca messages share 2026-08-17, so a bare date citation is ambiguous and
uncheckable. Every pre-registration, ledger, or research citation names the item id.

| Item | Venue | Subject |
|---|---|---|
| **D-1** | IBKR Client Services (Keelan B) | API scope — auth methods, sub-account allocation, fractional trading |
| **D-2** | IBKR Client Services | Rule 4210 status — migration state, legacy PDT application |
| **D-3** | Alpaca Support (Andy) | Account structure and Rule 4210 implementation |
| **D-4** | Alpaca Support (Andy) | Broker API scoping and fractional trading on cash accounts |

Ratified by ruling ① of the **Payload 4 ruling set** (hub, 2026-08-19).

## Verification tags

Continuous with `broker_selection_research.md`:

| Tag | Meaning |
|---|---|
| `[PRIMARY]` | Regulator or broker's own published documentation |
| `[VENUE]` | Verified against the live account or API |
| `[SUPPORT]` | Broker support statement |

In this file `[SUPPORT]` means a **written, dated, human-authored** broker statement —
not an automated reply. That distinction is load-bearing here: see the standing
fabrication note below.

## Redaction

**None applied**, per ruling ④ of the **Payload 4 ruling set** (hub, 2026-08-19). No
account numbers and no personal email
addresses appear in this correspondence. `api-solutions@interactivebrokers.com` is a
corporate alias, not a personal address. "Keelan B" and "Andy" are support
representatives acting in corporate capacity; first name plus venue plus date is the
provenance that keeps the citation checkable, and is retained for that reason.

---

## D-1 — IBKR Client Services (Keelan B), 2026-08-17 — API scope `[SUPPORT]`

Verbatim, as relayed:

> "Please see [the Web API authentication documentation] for an outline of the various
> authentication methods available… The API does support specifying which sub-account
> to allocate an order to… The API offerings (Web or TWS API) do not support fractional
> trading, with the exception of crypto and forex."

General API contact given as `api-solutions@interactivebrokers.com`.

**What it establishes.**

1. **Per-order sub-account allocation is confirmed in writing.** The F&F advisor
   structure's per-book routing — previously carried on documentation enumeration
   alone (`broker_selection_research.md` §4, the API `account` field) — now has a
   written client-services confirmation behind it. This is the mechanism the six-book
   structure depends on.

2. **No fractional equity trading through either API surface.** Crypto and forex are
   excepted; US equities and ETFs are not. On IBKR the live branch is **whole-share
   sizing**.

**Scope reconciliation — read alongside `broker_selection_research.md` §4, do not
treat as a contradiction.** That section records fractional shorts as supported
`[PRIMARY, KB verbatim]` and a 0.0032-share TSLA round-trip demonstrated in the
available account `[VENUE, trade log 2026-07-01]`. Both records stand. They describe
the **account and platform** capability; D-1 describes the **programmatic** surface.
Phase B routes every order programmatically from CI, so D-1's constraint is the
binding one on the live branch. The §4 line must not be read as an API capability.

**Not answered by D-1:** OAuth eligibility for the F&F advisor class, which remains
the open gate (`ibkr_oauth_application.md` §5; the 2026-08-14 reply established F- and
Organizational-account eligibility, U-accounts ineligible).

**Checkability gap:** no ticket reference was supplied in the relayed text for D-1 or
D-2. The IBKR message-centre ticket of record for the broader inquiry is **#T976605**
(sent 2026-08-11 23:00), but the relay does not state that D-1/D-2 are replies on it.
Recorded as a gap rather than assumed.

---

## D-2 — IBKR Client Services, 2026-08-17 — Rule 4210 status `[SUPPORT]`

Verbatim, as relayed:

> "Your account has not yet been migrated to the new Intraday Margin standards. The
> migration of client accounts from the Pattern Day Trading rules to the new Intraday
> Margin standards is taking place over the implementation period as specified by
> FINRA. We are unable to provide a specific date for when your individual account will
> be migrated… Because your account has not yet been migrated, the Pattern Day Trading
> (PDT) rules currently apply. [For margin accounts with Securities NLV below USD
> 25,000:] Day trade limit: A maximum of 3 day trades are permitted within any rolling
> 5 business day period… Executing 4 or more day trades within a 5 business day period
> results in the account being flagged as a Pattern Day Trader… If all 3 available day
> trades have been used within the last 5 days and the account remains below USD
> 25,000, the account is blocked from opening any new positions until the 5-day period
> expires. This restriction cannot be overridden… PDT rules apply at the individual
> account level. If you hold multiple linked accounts, each account must individually
> maintain a Securities Net Liquidation Value above USD 25,000 to be eligible for
> unlimited day trades. There is no aggregation across accounts."

**What it establishes.**

1. **IBKR is a pre-migration member as of 2026-08-17**, migrating over FINRA's
   implementation period, with no per-account date available. This is the live
   evidence that implementation timing is vendor-specific *within* the phase-in.

2. **Legacy PDT is in force on IBKR margin accounts**, with the 3-in-5 budget and the
   un-overridable new-position block below $25,000 NLV.

3. **No cross-account aggregation** of the $25,000 threshold or the day-trade counter.

**Operative consequence for Phase B: none.** PDT is a margin-account rule. The Phase B
branch is cash accounts, on which the pattern-day-trader designation does not exist at
either venue. Six books under the F-master as cash accounts face no day-trade cap and
no NLV threshold. D-2 describes the margin shape that
`docs/prereg/tier2_novel_sections.md` already disqualified — *"Margin-under-legacy-PDT
— disqualified as a Phase B venue shape"* — and that ruling killed the **shape**, not
the **venue**.

> **Do not derive a "six × $25,000" capital requirement from D-2.** It is arithmetic
> about a branch that is already dead, and it does not enter the 2026-09-15 record.
> Ruled explicitly as a correction issued with the **Payload 4 ruling set** (hub,
> 2026-08-19).

**Both valences of the non-aggregation fact, recorded so citations stay honest.** The
same sentence carries two consequences that read in opposite directions:

- *Protective* — the registered reading. Tier 2 registers *"there is no cross-account
  aggregation under legacy PDT, so the six-book independence requirement is not
  threatened by PDT counters on any branch."* D-2 corroborates this in writing: one
  book's counter cannot contaminate another.
- *Burdensome* — IBKR's framing. Because there is no pooling, an account cannot reach
  the threshold with a sibling's equity.

The prereg cites the protective reading, which is the one that bears on book
independence, and D-2 supports it directly. The burdensome reading applies only to the
disqualified margin shape.

**Standing exposure, unchanged:** the post-migration IMD shared-fate coupling remains
on the forced-change watch as a dated future regime exposure. D-2 confirms that
migration is coming to these accounts on an unstated schedule, which is precisely why
that watch exists.

---

## D-3 — Alpaca Support (Andy), 2026-08-17 — structure and Rule 4210 `[SUPPORT]`

Ticket **336707**, 09:31 EDT — the message already quoted in the 2026-08-17 landing
verification record of `docs/prereg/tier2_novel_sections.md`.

Verbatim, as relayed:

> "Through our standard self-directed Trading API, a retail individual is provisioned a
> single live trading account (plus up to three paper accounts)… Segregated live books
> beyond that are not supported under one individual via the standard Trading API; that
> requires separate entity accounts or an institutional Broker API relationship
> (omnibus/OmniSub)… we've begun rolling out multi-account support for an existing
> holder, but it's currently limited to specific combinations (e.g., an individual
> trading account plus an IRA) — not multiple independent retail books, and not the
> six-book structure you described… [Rule 4210:] Confirmed and implemented. Following
> FINRA's amendments to Rule 4210 (Regulatory Notice 26-10), effective June 4, 2026,
> Alpaca implemented the new intraday margin framework on that date for both Trading
> API users and Broker API partners. As a result, the pattern-day-trader designation,
> day-trade counting, and the $25,000 minimum-equity requirement no longer apply on our
> platform. Existing Regulation T / Rule 4210 maintenance requirements still apply,
> including the standard $2,000 minimum equity for margin accounts."

**What it establishes.**

1. **The one-account rule is now human-confirmed.** `broker_selection_research.md` §3
   carried it as `[SUPPORT, 2026-08-12; human confirmation requested]` — unpublished
   operational policy with an outstanding request. D-3 discharges that request. The
   six-book structure is unavailable at Alpaca under a single individual, and the
   partial multi-account rollout does not reach it.

2. **Alpaca's own implementation status, in writing** — see the 4210 section below for
   how this is scoped and cited.

---

## D-4 — Alpaca Support (Andy), 2026-08-17 — Broker API and fractionals `[SUPPORT]`

Verbatim, as relayed:

> "[Broker API] may not be the right fit for your use case. The Broker API is a B2B
> product for businesses offering trading to their own end customers… It's not scoped
> for a single operator running six of their own research books, and it wouldn't clear
> onboarding without external customers and a compliance program. The practical path
> for your architecture: run a single live account and handle the six-book segregation
> in your own application layer — per-book cash/position attribution and order tagging
> client-side, under one set of API keys… Fractional trading is supported for US-listed
> equities and ETFs on cash accounts (on by default — it's not margin-only). Orders go
> through POST /v2/orders using either qty (fractional) or notional (dollar amount)
> with a Day time-in-force; just confirm the asset returns fractionable: true via the
> Assets API… short selling requires a margin account, so whole-share shorts in ETB
> names wouldn't be available in a pure cash account."

**What it establishes.**

1. **The Broker API path is closed on its merits, not on price.** It requires external
   customers and a compliance program; a single operator running six research books
   does not clear onboarding. This is independent of, and stronger than, the
   entity-floor argument (`broker_selection_research.md` §3: $30,000 minimum,
   invite-only beta, six entities ≈ $180,000).

2. **The Alpaca path is software books.** Six-book segregation runs in the application
   layer — per-book cash and position attribution, client-side order tagging, one key
   set. That is a description of the architecture the pipeline already implements for
   paper trading, which is why keys are available today.

3. **Fractional trading works on cash accounts, default-on** — `qty` (fractional) or
   `notional` (dollar), Day TIF, gated on `fractionable: true` from the Assets API.

4. **A pure cash account cannot short at all.** Short selling requires margin, so not
   only are fractional shorts unavailable — whole-share ETB shorts are unavailable too.
   This corroborates the cash branch's long-only character at the venue level, which is
   what T2.4 registers as *"v4 (cash/long-only branch)"* and what the v4 shorting
   ablation implements. The design property and the venue constraint agree.

---

## Rule 4210 — implementation status across the two venues

### FINRA primary verification (recorded fetch)

Fetched by Operations **2026-08-19** from FINRA directly, per ruling ②(a) of the
**Payload 4 ruling set** (hub, 2026-08-19). This fetch, not any search summary, is the
committed source.

**Source:** <https://www.finra.org/rules-guidance/notices/26-10> `[PRIMARY]`

| Field | Value |
|---|---|
| Notice number | **Regulatory Notice 26-10** |
| Title | *FINRA Adopts New Intraday Margin Standards to Replace the Day Trading Margin Requirements* |
| Published | **April 20, 2026** |
| Effective date | **June 4, 2026** |
| Phase-in ends | **October 20, 2027** (18 months) |
| SEC release | Securities Exchange Act Release No. **105226** (April 14, 2026), 91 FR 20731 (April 17, 2026) |

Verbatim:

> "The effective date of the amendments is June 4, 2026, 45 days from publication of
> this Notice. Members that need more time to implement the rule change will be
> permitted to phase in their implementation over a period of 18 months, until October
> 20, 2027."

The amendments eliminate the pattern-day-trader designation, the day-trade count
mechanism, and the $25,000 minimum equity requirement in their entirety.

**Corroboration of prior records.** The hub's 2026-08-17 search returned effective
2026-06-04 with phase-in through 2027-10-20 — **matched exactly**.
`broker_selection_research.md` §2 records the same dates plus SEC Release 34-105226
(2026-04-14) — **matched exactly** (the `34-` prefix is the Exchange Act series
designation for release 105226). No discrepancy in any direction; the notice number,
both dates, and the release number are now verified against the regulator's own page.

### The two venues' positions

| | Statement | Status |
|---|---|---|
| **Alpaca (D-3)** | Framework implemented 2026-06-04 for Trading API and Broker API; PDT designation, day-trade counting, $25,000 minimum no longer apply | The **venue's written representation of its own implementation status** |
| **IBKR (D-2)** | Account not yet migrated; migrating across FINRA's implementation period; no per-account date available; legacy PDT applies meanwhile | The **venue's written representation of its own implementation status** |

Per ruling ②(b) of the **Payload 4 ruling set** (hub, 2026-08-19), each venue's 4210
content is cited as its representation of
its own status — which is what the pre-registration actually relies on, and which is
true regardless of how any other member has sequenced its migration. The regulator's
own effective date and phase-in window are verified above and cited from the notice.

### These are not inconsistent, and must not be framed as such

Notice 26-10 sets one effective date and grants members an 18-month phase-in to
October 20, 2027. A member that implemented on the effective date and a member still
migrating in August 2026 are **both compliant**; that is what a phase-in is for.
Per-vendor migration timing within the phase-in window is exactly the state of affairs
the amended vendor-specificity clause registers. Stated this way, a reader sees the
mechanism that explains both letters. Stated as a discrepancy, a reader sees a
contradiction that is not there.

The two letters are therefore mutually corroborating on the point that matters: D-2
describes migration "taking place over the implementation period as specified by
FINRA", which is the phase-in the notice grants and under which D-3's early
implementation sits.

### Standing note — automated-response fabrication (carried forward)

Alpaca's **AI** support reply of **2026-08-12** stated the framework applied *"as of
August 13, 2026"*. **That date is fabricated** — no August date appears in any
published FINRA or Alpaca material, and the verified effective date is June 4, 2026.
The note stands. It is the reason `[SUPPORT]` in this file is restricted to
human-authored statements: D-3 and D-4 are human-written and are what supersede the
automated reply, and the earlier fabrication is why venue statements about their own
regulatory status are corroborated against the regulator rather than taken at face
value.

---

## Record corrections landed by this file

### 1. Fractional trading — supersession (ruling ③, Payload 4 ruling set)

**Supersedes:** the record's prior characterization of Alpaca fractional trading as
margin-only.

**Corrected to:** fractional trading is **confirmed on cash accounts, default-on**,
available by `qty` or `notional` for US-listed equities and ETFs, gated on
`fractionable: true` `[SUPPORT, D-4]`.

**Unchanged:** *no fractional shorts.* Short selling requires a margin account, so
fractional shorts remain unavailable — and in a pure cash account, whole-share ETB
shorts are unavailable too `[SUPPORT, D-4]`, consistent with the published
documentation already recorded in `broker_selection_research.md` §3 (*"We do not
support short sales in fractional orders"*; *"All fractional sell orders are marked
long"* `[PRIMARY]`).

*Graph note:* D's landing scope is this file alone, so no reciprocal superseded-by
pointer was written into `broker_selection_research.md` §3. That one-line pointer is
owed and is flagged as a follow-up, not landed here.

### 2. PDT scope

The 3-in-5 day-trade cap and the $25,000 escape threshold in D-2 are **margin-account
rules**. They do not apply to cash accounts at either venue. Any capital requirement
derived from them describes the disqualified margin shape and does not enter the
2026-09-15 record.

### 3. Alpaca account structure — evidentiary status upgrade

`broker_selection_research.md` §3's one-account finding moves from
`[SUPPORT, human confirmation requested]` to `[SUPPORT, human-confirmed — D-3]`. The
substantive finding is unchanged; only its evidentiary standing improves.

---

## Venue matrix as of 2026-08-19

| | **IBKR** | **Alpaca** |
|---|---|---|
| Book structure | **Real books** — six segregated accounts under the F&F advisor structure, per-order sub-account routing confirmed in writing `[SUPPORT, D-1]` | **Software books** — one live account, six-book segregation in the application layer, ruled compliant-with-disclosures `[SUPPORT, D-3, D-4]` |
| Position sizing | **Whole-share** — no fractional equity trading via Web or TWS API `[SUPPORT, D-1]` | **Fractional** — cash accounts, default-on, `qty` or `notional` `[SUPPORT, D-4]` |
| Programmatic access | **OAuth pending** — the open gate; F- and Organizational accounts eligible, U-accounts not (2026-08-14) | **Keys today** — the pipeline's existing integration surface |
| 4210 status | Not yet migrated; legacy PDT on margin accounts `[SUPPORT, D-2]` | Implemented 2026-06-04 `[SUPPORT, D-3]` |
| Cash-branch shorting | n/a on the cash branch (long-only by design) | Unavailable — shorting requires margin `[SUPPORT, D-4]` |

**IBKR's single remaining flaw on the live cash branch is whole-share sizing.** PDT is
not a flaw on this branch; it is a property of a shape already ruled out.

The granularity consequence of whole-share sizing is quantified at
`broker_selection_research.md` §5 and is a live input to the 2026-09-15 decision. That
section's arithmetic is stated for whole-share *shorting* at $4,000 books; the cash
branch is long-only, so the sizing question there is whole-share *long* entries. The
restatement is owed and is not performed here — D's scope is this file.

---

## Not landed here

- **The amended vendor-specificity clause** in `docs/prereg/tier2_novel_sections.md`
  (HALT 1 of the 2026-08-17 landing). D-1 and D-2 supply the IBKR citation whose
  absence caused that HALT — the evidentiary blocker is cleared — but the replacement
  clause is verbatim Research/hub payload text, and Operations does not author payload
  wording. Awaiting the clause text.
- **Any ledger entry.** D touches no ledger; `scripts/phase_a_integrity_ledger.json`
  remains contended by the halted v4 prompt lane, and the quarantine window stays
  closed pending Research's site-2 re-ruling. No rule 7 contention arises from this
  file.
- **The reciprocal supersession pointer** in `broker_selection_research.md` §3, and the
  §5 restatement for long-only whole-share sizing. Both owed, both out of D's scope.
