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
| **Alpaca** | **SENT** | 2026-08-12 | PI | Multi-account structure, framework status, restriction scope. **Supersedes the #58291 reference, which was never sent.** |
| **IBKR** | **PENDING** — packet prepared, awaiting §4 confirmation then account-holder send | 2026-08-12 | Account holder (pending) | First Party OAuth eligibility + timeline, per §5 |

## 7. Sources

All retrieved 2026-08-12. `interactivebrokers.com` blocks automated fetchers; pages
were read through a browser session.

- [Web API — Authentication Introduction](https://www.interactivebrokers.com/docs/web-api/authentication/introduction) — supported account types per auth method
- [First Party OAuth — Registration Process](https://www.interactivebrokers.com/docs/web-api/authentication/oauth-1a/first-party-oauth/registration-process) — first-party definition, three questions, Self Service Portal, consumer-key midnight validity
- [Third Party OAuth — Registration Process](https://www.interactivebrokers.com/docs/web-api/authentication/oauth-1a/third-party-oauth/registration-process) — third-party definition, 8–14 week timeline
- [Limitations of the Client Portal Gateway](https://www.interactivebrokers.com/docs/web-api/authentication/cpgw/limitations-of-the-client-portal-gateway) — same-machine login and call constraints
- [Authentication FAQ](https://www.interactivebrokers.com/docs/web-api/authentication/faq) — 24-hour session ceiling, ~6-minute idle timeout
