# IBKR OAuth Web API — application fact-find

**Lane:** Operations — broker de-risking (fact-find only)
**Date:** 2026-08-12
**Scope:** Identify the IBKR OAuth Web API application process for the account type in
question, prepare what the account holder needs to submit, report findings.
**Explicitly out of scope:** adapter code, executor changes, pipeline changes. The
broker migration remains undecided until the Sept 15 gate.

**Status: HALT — application NOT submitted.** A blocking eligibility finding (below)
changes what should be submitted and by whom. Relay packet is prepared and ready
in §5; it needs one decision from the PI before the account holder sends it.

---

## 1. Headline finding

**IBKR does not list Individual Accounts as a supported account type for either
OAuth 1.0a or OAuth 2.0.** Per IBKR's Web API authentication overview
(`/docs/web-api/authentication/introduction`, retrieved 2026-08-12), supported
account types are:

| Auth method | Supported account types (verbatim from IBKR docs) |
|---|---|
| **Client Portal Gateway** | Individual Accounts |
| **OAuth 1.0a** | Advisor Accounts · Broker & FCM Accounts · Proprietary Trading Group Accounts · Hedge and Mutual Fund Accounts · Institutional Hedge Fund Investors · Third Party Software Developers |
| **OAuth 2.0** | Advisor Accounts · Broker & FCM Accounts · Proprietary Trading Group Accounts · Hedge and Mutual Fund Accounts · Institutional Hedge Fund Investors |

The First Party OAuth registration page is consistent with this and narrower than
the hub's working assumption:

> "Interactive Brokers classifies first party entities as institutions that will be
> trading on behalf of themselves or their institution. The same entity developing
> with the API platform will be the same entity that will be using it for trading.
> Examples of first party entities include financial advisors, hedge funds, and
> organizations looking to trade their own capital."

An adult-owned **individual** account is not one of the listed examples. It is not
explicitly *excluded* in prose either — the docs enumerate supported types rather
than prohibited ones — so the eligibility question is genuinely open and can only
be resolved by asking IBKR directly. That ask is the whole value of this lane, and
it is cheap.

**This does not kill the IBKR branch. It relocates the unbounded variable.** The
unknown was "how long does OAuth approval take." The unknown is now "is this
account type eligible at all, and if not, what structure is." That is a better
question to be asking on Aug 12 than on Sept 15, which is the point of the lane.

## 2. The hub's premise on CI-compatibility is confirmed

The Client Portal Gateway is the one method that *does* support individual
accounts, and it is structurally incompatible with a CI runner. Verbatim
limitations:

> - "Users must log in through the browser on the same machine as Client Portal
>   Gateway in order to authenticate."
> - "All API Endpoint calls must be made on the same machine where the Client
>   Portal Gateway was authenticated."

Plus session mechanics: a session lasts **at most 24 hours**, resetting at midnight
(New York / Zug / Hong Kong by nearest connection), and **times out after ~6 minutes**
without traffic or a maintained `/tickle`.

An ephemeral GitHub Actions runner has no persistent machine, no browser, and no
human to complete an interactive SSO login — and a daily-cadence job cannot hold a
6-minute-timeout session across a 24-hour cycle. CPGW would require a
permanently-authenticated host outside CI. So OAuth really is the only
CI-compatible IBKR route, exactly as the hub reasoned.

## 3. The two application routes, priced

### First Party OAuth — the route to try
- **Submit to:** `apiintegration@interactivebrokers.com`
- **Form:** none. A plain email answering three questions:
  1. What do you intend to do with OAuth access?
  2. Please list all accounts that will use the developed OAuth program.
  3. Will the client application be developed in-house or by a third-party developer?
- **Who submits:** the account holder (the accounts must be theirs).
- **On approval:** IBKR provides a Self Service Portal link — *"This link will be
  provided directly to approved entities during the onboarding process"* — used to
  generate the consumer key, encryption keys, and access tokens.
- **Published timeline: none.** IBKR states no estimate for the first-party path.
  This is the variable the lane was chartered to bound, and it cannot be bounded
  from public documentation. It has to be asked.
- **Post-approval gotcha (already logged for the eventual adapter, if any):** a newly
  registered consumer key is not valid until after midnight in the applicable region;
  using it earlier returns `401 Invalid Consumer`. Budget one extra day between
  approval and first green CI run.

### Third Party OAuth — dead for Sept 15, do not pursue
- **Submit to:** `api-solutions@interactivebrokers.com`
- **Eligibility:** *"any organization offering a platform or medium of trading to
  individuals outside of the organization"* — and applicants *"must have an
  established platform with other brokerage firms, or a full proof of concept with
  an integration using the Web API."* Compliance additionally expects a completed
  public website detailing the offering.
- **Published timeline:** 2–3 weeks initial vetting + 3–6 weeks Compliance enhanced
  due diligence and three-tier approval + 3–5 weeks legal/keys = **8–14 weeks**, and
  IBKR notes these are estimates that can stretch.

Aug 12 → Sept 15 is **4.8 weeks**. The third-party route cannot complete before the
gate under its own published best case. If the lab is ever classified third-party,
the IBKR branch is foreclosed for Sept 15 regardless of how fast anyone moves.

## 4. The decision this surfaces for the PI

The eligibility answer depends on a structural fact that is *prospective, not
settled*: the F&F structure.

- **Individual account trading only the holder's own capital** → arguably "trading on
  behalf of themselves," in-house development, no outside users. This is the
  strongest first-party framing available and the one the relay packet uses.
- **Pooling or managing friends-and-family money** → moves toward advisor/third-party
  classification in IBKR's taxonomy, which imports the 8–14 week track and a
  materially heavier compliance posture. It also raises questions well outside this
  lane's scope about managing other people's money, which the PI should settle with
  a qualified professional before it is described to IBKR in writing.

**Recommendation:** apply now under the narrowest true framing — individual account,
own capital, in-house development, non-commercial research, no outside users — and
ask IBKR the eligibility question directly rather than assuming the answer. Do not
describe a prospective F&F structure that does not yet exist; describing a structure
that isn't real is both inaccurate and the fastest way to get routed into the
third-party track. If F&F is later adopted, that is a new conversation with IBKR.

**The one thing needed from the PI before send:** confirmation that the account is
currently individual, own-capital-only, with no outside participants — i.e. that the
§5 packet is factually accurate as written.

## 5. Relay packet — for the account holder

The account holder must send this; it references their accounts, and IBKR is being
asked to grant programmatic trading access to them. Steps:

1. Confirm with the PI that §4's framing is accurate as of today.
2. Fill in the IBKR account number(s) at the marked field. **Do not** include the
   account password, security-device codes, or any other credential — IBKR does not
   ask for these and no legitimate request will.
3. Send from the email address on the IBKR account (matching sender speeds
   identity verification).
4. To: `apiintegration@interactivebrokers.com`
5. Subject: `First Party OAuth access request — individual account, automated research trading`
6. Forward IBKR's reply to the PI when it arrives.

---

> **Body:**
>
> Hello,
>
> I would like to request First Party OAuth access to the Web API for my individual
> account, and to confirm whether my account type is eligible for it. Your
> documentation lists Individual Accounts under the Client Portal Gateway but not
> under OAuth 1.0a or OAuth 2.0, so I want to ask directly rather than assume.
>
> Answering the three questions from your registration page:
>
> **1. What do you intend to do with OAuth access?**
> Automated daily order placement and portfolio reads for a non-commercial research
> project running on my own account and my own capital. The project runs several
> large language models as independent model portfolios in US equities and ETFs, at
> small notional size, to study their decision-making. Orders are submitted by a
> scheduled job on a hosted CI runner with no interactive browser session and no
> persistent host, which is why the Client Portal Gateway is not workable for us —
> it requires browser login on, and API calls from, the same machine. Endpoints
> needed are limited to account/positions/balances reads and equity order placement.
> This is not a commercial product and is not offered to anyone outside the account.
>
> **2. Please list all accounts that will use the developed OAuth program.**
> [ACCOUNT HOLDER: enter your IBKR account number(s) here — nothing else]
>
> **3. Will the client application be developed in-house or by a third-party developer?**
> In-house. It is built and operated entirely by our household for our own use.
> There is no external vendor, no distribution to other users, and no third-party
> platform involved.
>
> Two additional questions, if you are able to answer them:
>
> - Is an individual account eligible for First Party OAuth? If not, what account
>   structure would be required?
> - What is the typical timeline from this request to issued credentials? We have an
>   internal decision point in mid-September and are trying to understand whether
>   that is realistic.
>
> Thank you,
> [ACCOUNT HOLDER NAME]

---

## 6. Inquiry log

| Broker | Status | Date | Sent by | Content |
|---|---|---|---|---|
| **Alpaca** | **ANSWERED** | sent 2026-08-12, replied 2026-08-12 | PI | Multi-account structure, framework status, restriction scope. **Supersedes the #58291 reference, which was never sent.** Response summarized and corroborated in §6.1. |
| **IBKR** | **PENDING** — packet prepared, awaiting §4 confirmation then account-holder send | 2026-08-12 | Account holder (pending) | First Party OAuth eligibility + timeline, per §5 |

### 6.1 Alpaca response — content and corroboration

Response received 2026-08-12. Three claims, as relayed:

1. One live account per retail individual; a six-book structure requires entity
   accounts or a Broker API partnership.
2. PDT lifted / intraday framework implemented, effective 2026-08-13.
3. IMD restrictions are per-account, with no contagion indicated.

**The reply is AI-generated support output and is not itself a citable source.** The
two load-bearing claims were checked against Alpaca's published documentation. One
survives with a corrected date; one does not reach doc grade at all.

#### Claim 2 — PDT / intraday framework: SUBSTANCE CORROBORATED, DATE WRONG

The substance holds. Per Alpaca's own announcement and docs: FINRA amended Rule 4210;
the Pattern Day Trader designation and all PDT-based restrictions are retired; day
trade counting and Day Trade Buying Power are gone; **Intraday Buying Power** replaces
them, computed in real time from equity, positions, and intraday P&L; Intraday Margin
Calls replace Day Trade Margin Calls. The minimum equity for 4x intraday buying power
*"has been lowered from $25,000 to $2,000."* The ordinary **$2,000 Reg T / Rule 4210
minimum still applies.** Previously PDT-restricted accounts were unrestricted.

**The date is wrong, by about ten weeks.** Alpaca states FINRA's amendments carried
*"the effective date of June 4, 2026,"* and that Alpaca lifted PDT in production on
that same date for both Trading API and Broker API. Sandbox testing opened June 22,
2026, and the deprecated API fields were *"completely removed from the API by July 6,
2026."* **No August 2026 date appears anywhere in Alpaca's published material.**

The framework is therefore not a future dependency arriving 2026-08-13 — it has been
live since 2026-06-04. That is favourable for the lab, but **2026-08-13 must not enter
the pre-registration.** Cite 2026-06-04.

#### Claim 1 — one live account per retail individual: NOT CORROBORATED at doc grade

**No Alpaca documentation or support page states this.** Checked:

- *Trading Account / account plans* — describes individual (non-retirement) and entity
  accounts; **no account-count limit stated.**
- *What types of accounts does Alpaca offer?* — *"free paper trading accounts and
  commission-free brokerage accounts for individuals (non-retirement) and entities"*;
  **no account-count limit stated.**
- *Who can apply for an Alpaca brokerage account?* — enumerates eligibility (18+, valid
  SSN, US residential address, citizen/permanent resident/valid visa). **Silent on
  number of accounts, duplicate accounts, and per-identity restrictions.**

The only source asserting a one-account-per-person rule is a commercial blog operated
by a multi-accounting / anti-detect browser vendor — a party with a direct interest in
the topic, and not doc-grade by any standard. **It is not cited here and must not be
cited in the pre-registration.**

The claim is *plausible* — one funded account per verified identity is common KYC/AML
practice across US retail brokers — but plausible is not documented. **To make this
citable, obtain written confirmation from a named Alpaca representative, or a policy
URL.** Until then the pre-reg should characterize it as an unpublished operational
policy conveyed by support on 2026-08-12, not as documented Alpaca policy.

#### Unprompted finding — the proposed remedy is capital-infeasible

Alpaca's suggested path for a six-book structure was "entity accounts or Broker API
partnership." On Alpaca's own published terms, the entity route is gated well above
the lab's Phase B sizing:

- Business trading accounts are an **invite-only beta** — *"We are currently running a
  beta program for the business trading account"* — accepting Corporations, LLCs, and
  Partnerships in the U.S., with ineligible applicants placed on a **waitlist**.
- *"There's no charge to open a business trading account, but it does have a **$30,000
  account minimum**."*
- Business accounts are classified as **professional data subscribers**, which changes
  market-data entitlements and cost assumptions.

Phase B as specified is $1K per model across six books — **$6K total, against a $30,000
floor.** The entity route is therefore not merely slow, it is capital-infeasible at
current sizing, and it would additionally reclassify the lab's market-data status. The
Broker API partnership route carries a formal partnership agreement and ongoing
compliance obligations, which is heavier still.

**This is a decision input for the Sept 15 gate, not a lane decision.** Flagging only:
the six-book structure may not be reachable at either broker without either a capital
increase of roughly 5x or a change in book structure. Claim 3 (IMD restrictions
per-account, no contagion) was not independently corroborated — it was not nominated
as load-bearing, and no public Alpaca page addresses cross-account IMD contagion.

## 7. Sources

All retrieved 2026-08-12. `interactivebrokers.com` blocks automated fetchers; pages
were read through a browser session.

- [Web API — Authentication Introduction](https://www.interactivebrokers.com/docs/web-api/authentication/introduction) — supported account types per auth method
- [First Party OAuth — Registration Process](https://www.interactivebrokers.com/docs/web-api/authentication/oauth-1a/first-party-oauth/registration-process) — first-party definition, three questions, Self Service Portal, consumer-key midnight validity
- [Third Party OAuth — Registration Process](https://www.interactivebrokers.com/docs/web-api/authentication/oauth-1a/third-party-oauth/registration-process) — third-party definition, 8–14 week timeline
- [Limitations of the Client Portal Gateway](https://www.interactivebrokers.com/docs/web-api/authentication/cpgw/limitations-of-the-client-portal-gateway) — same-machine login and call constraints
- [Authentication FAQ](https://www.interactivebrokers.com/docs/web-api/authentication/faq) — 24-hour session ceiling, ~6-minute idle timeout

Alpaca (§6.1), all retrieved 2026-08-12:

- [FINRA Retires the PDT Rule: Introducing Alpaca's New Intraday Margin Framework](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/) — June 4 2026 effective + production date, June 22 sandbox, July 6 field removal, $25,000 → $2,000
- [Understanding FINRA's New Intraday Margin Rule and the End of PDT](https://docs.alpaca.markets/us/docs/understanding-finras-new-intraday-margin-rule-and-the-end-of-pdt-1) — Rule 4210, $2,000 Reg T minimum, portfolio-margin thresholds
- [Trading Account (account plans)](https://docs.alpaca.markets/us/docs/account-plans) — individual vs entity; no account-count limit stated
- [What types of accounts does Alpaca offer?](https://alpaca.markets/support/types-accounts-alpaca-offers) — individuals (non-retirement) and entities; no account-count limit stated
- [Who can apply for an Alpaca brokerage account?](https://alpaca.markets/support/requirements-alpaca-brokerage-account) — eligibility criteria; silent on number of accounts per person
- [Does Alpaca offer business accounts?](https://alpaca.markets/support/alpaca-business-accounts) — invite-only beta, $30,000 account minimum, professional data subscriber classification
