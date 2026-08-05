"""USD cost rates per million tokens for each LLM provider/model, as RATE HISTORY.

Restructured 2026-08-02 (cost-reconciliation task): the table is now a list of
dated rate periods per model, not a single flat rate. Cost attribution is
historical — a call made in April must be priced at April's list rate, not
today's — so every lookup takes an ``on_date``.

The 2026-08-02 reconciliation against providers' official/archived price pages
found that four of the six carried rates were NEVER a published list price at
any date (they were entry errors, not stale prices):

  * claude-opus-4-6:  carried $15/$75 (the Opus 4.1-generation rate). Actual:
    $5/$25 since the Opus line's cut at Opus 4.5 launch (2025-11-24), confirmed
    unchanged at 4.6 launch (2026-02-05).      [anthropic.com/news/claude-opus-4-6]
    Single tier: 4.6-generation models bill the full 1M-token window at
    standard pricing (the >200K premium carried here until 2026-08-05 was a
    denied rate — removed by hub ruling 2026-08-05).
  * gpt-5.4:          carried $10/$30. Actual: $2.50/$15 from launch day
    (2026-03-05) through today, verified by archived pricing-page snapshots
    (Mar 5 / Apr 25 / Jul 10 / Aug 1 all identical).
  * grok-4.20-*:      carried $5/$25. Actual: $2/$6 at launch, cut to
    $1.25/$2.50 when grok-4.3 shipped. The cut date is not directly
    attested: the last old-rate capture is 2026-05-01 07:03 UTC and the
    first new-rate capture is 2026-05-06 16:31 UTC, so the archive bounds
    it to (May 1 07:03, May 6 16:31] UTC. We use 2026-05-07 — old rate
    until a new rate is attested (hub ruling 2026-08-05, strict
    archive-conservative). Press reporting places the grok-4.3 launch wave
    at May 4, which the archive does not attest; the 05-07 boundary
    therefore overstates our own cost by $0.49 relative to a 05-04
    boundary, which is the correct direction for a cost claim. See
    CORRECTIONS.md for the ambiguity-window disclosure.
  * gemini-3.1-pro-preview: carried flat $3.50/$14. Actual: tiered
    $2/$12 (prompt ≤200K tokens) / $4/$18 (>200K) since release (2026-02-19).
  * deepseek-v4-pro:  carried $1.74/$3.48 ("post-promo standard"). The 75%-off
    launch rate $0.435/$0.87 was extended (~2026-05-01) and then made the
    PERMANENT list price (2026-05-22) — the standard rate never applied for a
    single day. Peak/off-peak 2x pricing is ANNOUNCED but NOT in effect as of
    2026-08-02 (no effective date published); do not model it until it ships.
  * claude-sonnet-4-6: carried $3/$15 — confirmed CORRECT throughout.

These mirror published list pricing (no negotiated discounts) so the
cost-performance comparison tracks the *out-of-pocket* cost a researcher
running this lab would actually incur. Numbers are hand-edited as providers
update pricing — there is no live billing API. When a provider changes a
price, append a new period; never edit a past period (that falsifies history).

Not modeled (all inert for lab traffic): cached-input discounts (adapters
don't request caching and the logs carry no cached-token split), batch-tier
discounts, and OpenAI's Priority tier.

Used by:
- the five provider adapters to price each call at call time (no date arg →
  rates as of today)
- analytics/performance.py's cost summary, which backfills null-cost records
  from token counts (passes the record's date so the backfill uses the rate
  in force when the call ran)
- scripts/gemini_finish_reason_probe.py (call-time pricing)
"""
from __future__ import annotations

from datetime import datetime, timezone

# Rate history. Keys are model_id strings (the same strings stored in
# settings.json under models.<key>.model and echoed back by adapters).
#
# Each model maps to a list of periods, ascending by effective_from.
#   effective_from: "YYYY-MM-DD" (UTC date) the rates took effect, or None
#                   for "since before this table's horizon".
#   tiers: prompt-size tiers, checked in order; a call belongs to the first
#          tier whose max_input_tokens is None or >= the call's input tokens.
#          Providers bill the WHOLE request at the tier's rate once the
#          prompt crosses the threshold, which is exactly what per-call tier
#          selection by input_tokens reproduces.
#   note:  provenance — where the rate/date comes from.
RATE_HISTORY: dict[str, list[dict]] = {
    # Anthropic — Sonnet 4.6 verified correct for the whole lab window.
    "claude-sonnet-4-6": [
        {
            "effective_from": None,
            "tiers": [{"max_input_tokens": None, "input": 3.00, "output": 15.00}],
            "note": "Confirmed vs current Anthropic list 2026-08-02; unchanged over the lab window.",
        },
    ],
    # Opus 4.6 launched 2026-02-05 at $5/$25 ("Pricing remains the same at
    # $5/$25" — anthropic.com/news/claude-opus-4-6); the Opus line moved off
    # $15/$75 at Opus 4.5's launch, 2025-11-24. SINGLE tier: the provider page
    # states 4.6-generation models bill the FULL 1M-token context window at
    # standard pricing, so the >200K long-context premium ($10/$37.50) carried
    # here until 2026-08-05 was a denied rate, not merely an inert one. Removed
    # by hub ruling — see CORRECTIONS.md.
    "claude-opus-4-6": [
        {
            "effective_from": None,
            "tiers": [{"max_input_tokens": None, "input": 5.00, "output": 25.00}],
            "note": "$5/$25 since launch 2026-02-05, full 1M window at standard pricing (no long-context tier); the old $15/$75 entry was the Opus 4.1-generation rate and never applied to 4.6.",
        },
    ],
    # OpenAI — $2.50/$15 from launch day (2026-03-05) through today, verified
    # by archived pricing-page snapshots across the model's entire life. The
    # >272K-context tier bills the full request at $5/$22.50.
    "gpt-5.4": [
        {
            "effective_from": None,
            "tiers": [
                {"max_input_tokens": 272_000, "input": 2.50, "output": 15.00},
                {"max_input_tokens": None, "input": 5.00, "output": 22.50},
            ],
            "note": "Flat since 2026-03-05 launch; the old $10/$30 entry matched no published tier at any date.",
        },
    ],
    # Google — tiered from release (2026-02-19), inherited unchanged from
    # Gemini 3 Pro; the old flat $3.50/$14 was never a Google list price.
    "gemini-3.1-pro-preview": [
        {
            "effective_from": "2026-02-19",
            "tiers": [
                {"max_input_tokens": 200_000, "input": 2.00, "output": 12.00},
                {"max_input_tokens": None, "input": 4.00, "output": 18.00},
            ],
            "note": "ai.google.dev/gemini-api/docs/pricing; unchanged since release.",
        },
    ],
    # xAI Grok — keep base "grok-4" entry so the prefix-fallback resolves
    # legacy returned IDs like "grok-4-0709"; the production string we send
    # is grok-4.20-0309-reasoning. (Legacy entry retained as originally
    # recorded — pre-4.20 rates were not part of the 2026-08 reconciliation;
    # no logged cost ever resolved through it.)
    "grok-4": [
        {
            "effective_from": None,
            "tiers": [{"max_input_tokens": None, "input": 5.00, "output": 15.00}],
            "note": "Legacy pre-4.20 entry, unverified; retained for old returned-ID resolution.",
        },
    ],
    # The 4.20 line launched ~2026-03-09 at $2/$6 (<200K prompt tier) and was
    # cut to $1.25/$2.50 when grok-4.3 shipped. The cut date is bounded, not
    # attested: last old-rate capture May 1 07:03 UTC, first new-rate capture
    # May 6 16:31 UTC. Boundary set at 2026-05-07 — old rate until a new rate
    # is attested (hub ruling 2026-08-05). The old $5/$25 entry matched no
    # published 4.20-family rate at any date.
    "grok-4.20-0309-reasoning": [
        {
            "effective_from": None,
            "tiers": [
                {"max_input_tokens": 200_000, "input": 2.00, "output": 6.00},
                {"max_input_tokens": None, "input": 4.00, "output": 12.00},
            ],
            "note": (
                "Launch pricing (earliest archive capture 2026-03-11). "
                "UNVERIFIED: the >200K tier ($4/$12) could not be confirmed "
                "against any archived capture of the period-1 price page. "
                "Inert for lab traffic (no logged call exceeds ~11K input "
                "tokens), so it never priced a record; do not rely on it "
                "without re-verification."
            ),
        },
        {
            "effective_from": "2026-05-07",
            "tiers": [
                {"max_input_tokens": 200_000, "input": 1.25, "output": 2.50},
                {"max_input_tokens": None, "input": 2.50, "output": 5.00},
            ],
            "note": (
                "Cut to match grok-4.3; unchanged through Grok 4.5 GA "
                "(2026-07-08) and today. Effective date is archive-conservative, "
                "not attested — see CORRECTIONS.md (ambiguity window "
                "2026-05-01 07:03 -> 2026-05-06 16:31 UTC)."
            ),
        },
    ],
    # DeepSeek — reasoning traces count as output tokens, so output bills
    # higher than a non-thinking call of the same visible length.
    # chat/reasoner are legacy entries for older returned IDs; both aliases
    # were hard-stopped by the provider 2026-07-24 15:59 UTC.
    "deepseek-chat": [
        {
            "effective_from": None,
            "tiers": [{"max_input_tokens": None, "input": 0.27, "output": 1.10}],
            "note": "Legacy alias, retired 2026-07-24.",
        },
    ],
    "deepseek-reasoner": [
        {
            "effective_from": None,
            "tiers": [{"max_input_tokens": None, "input": 0.55, "output": 2.19}],
            "note": "Legacy alias, retired 2026-07-24; rate in force for the lab's Apr 8-24 reasoner era.",
        },
    ],
    # V4-Pro launched 2026-04-24 at $0.435/$0.87 (framed as 75% off a $1.74/
    # $3.48 list). The discount was extended (~2026-05-01) and then made the
    # PERMANENT list price (2026-05-22) — $1.74/$3.48 never applied for a
    # single day. Cache-hit input ($0.003625) is not modeled (no cached-token
    # split in the logs). Peak/off-peak 2x pricing (peak 09:00-12:00 and
    # 14:00-18:00 Beijing = 01:00-04:00 / 06:00-10:00 UTC) is ANNOUNCED but
    # NOT in effect as of 2026-08-02 — the lab session 13:30-21:00 UTC would
    # be off-peak anyway; add a period only when the provider publishes an
    # effective date.
    "deepseek-v4-pro": [
        {
            "effective_from": "2026-04-24",
            "tiers": [{"max_input_tokens": None, "input": 0.435, "output": 0.87}],
            "note": "Launch rate made permanent 2026-05-22 (api-docs.deepseek.com/quick_start/pricing).",
        },
    ],
    "deepseek-v4-flash": [
        {
            "effective_from": "2026-04-24",
            "tiers": [{"max_input_tokens": None, "input": 0.14, "output": 0.28}],
            "note": "Rate in force across the Apr 24 - May 21 reasoner-alias drift window; matches current list.",
        },
    ],
}


# The flat table that priced every call logged before the 2026-08 restructure,
# frozen verbatim. It is NOT pricing policy — every rate in it is superseded by
# RATE_HISTORY above, and four of the six were never a published list price.
#
# It survives for exactly one job: back-solving screening input tokens. The
# decision log stores a screening call's OUTPUT tokens and the USD cost that
# was computed at call time, but not its input tokens. For any record written
# before this table was replaced, that stored cost is a two-unknowns equation
# with one unknown removed — inverting it recovers the input-token count to
# within the 6dp rounding of the stored cost (±0.2 tokens at these rates).
#
# Records written from 2026-08-05 onward carry `screening_input_tokens`
# directly and never touch this path. Do not add entries here; when the last
# pre-2026-08 record leaves the analysis window, delete it.
LEGACY_FLAT_TABLE_PRE_2026_08: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-6": (15.00, 75.00),
    "gpt-5.4": (10.00, 30.00),
    "gemini-3.1-pro-preview": (3.50, 14.00),
    "grok-4": (5.00, 15.00),
    "grok-4.20-0309-reasoning": (5.00, 25.00),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v4-pro": (1.74, 3.48),
    "deepseek-v4-flash": (0.14, 0.28),
}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _legacy_rates(model_id: str) -> tuple[float, float] | None:
    """Legacy flat-table lookup with the same prefix fallback the adapters used."""
    if not model_id:
        return None
    if model_id in LEGACY_FLAT_TABLE_PRE_2026_08:
        return LEGACY_FLAT_TABLE_PRE_2026_08[model_id]
    parts = model_id.split("-")
    while len(parts) > 1:
        parts.pop()
        candidate = "-".join(parts)
        if candidate in LEGACY_FLAT_TABLE_PRE_2026_08:
            return LEGACY_FLAT_TABLE_PRE_2026_08[candidate]
    return None


def backsolve_screening_input_tokens(
    model_id: str,
    screening_cost_usd: float | None,
    screening_output_tokens: int | None,
) -> int | None:
    """Recover a pre-2026-08 screening call's input tokens from its logged cost.

    The logged cost was computed at call time as
        cost = in/1e6 * old_input_rate + out/1e6 * old_output_rate
    under the flat table frozen in LEGACY_FLAT_TABLE_PRE_2026_08. Output tokens
    are logged, so this inverts for input. Exact to ±0.2 tokens (the stored
    cost is rounded to 6dp).

    Returns None when the model has no legacy rate, when either input is
    missing, or when the inversion goes materially negative — which would mean
    the stored cost did not come from the legacy table and the assumption
    behind this whole path is void for that record. Callers treat None as
    "screening cost unknown", never zero.
    """
    if screening_cost_usd is None or screening_output_tokens is None:
        return None
    rates = _legacy_rates(model_id)
    if rates is None or rates[0] <= 0:
        return None
    in_rate, out_rate = rates
    tokens = (
        float(screening_cost_usd) * 1_000_000.0
        - float(screening_output_tokens) * out_rate
    ) / in_rate
    if tokens < -1:
        return None
    return max(0, round(tokens))


def _resolve_history(model_id: str) -> list[dict] | None:
    """Look up a model's rate history with prefix fallback for versioned IDs.

    Providers return date-versioned strings like `gpt-5.4-2026-03-05` or
    `grok-4-0709`. The table is keyed by the base model name. We try the
    full ID first, then progressively shorter dash-delimited prefixes until
    we find a match, so datestamped snapshots don't need their own entries.

    Stops at 1 segment to avoid degenerate matches like "gpt" or "grok".
    """
    if not model_id:
        return None
    if model_id in RATE_HISTORY:
        return RATE_HISTORY[model_id]
    parts = model_id.split("-")
    while len(parts) > 1:
        parts.pop()
        candidate = "-".join(parts)
        if candidate in RATE_HISTORY:
            return RATE_HISTORY[candidate]
    return None


def _rates_for(model_id: str, on_date: str, input_tokens: int) -> dict | None:
    """Resolve the (input, output) rates in force for a call.

    Picks the last period whose effective_from is <= on_date (None = always),
    then the first tier whose max_input_tokens is None or >= input_tokens.
    Returns None when the model is unknown OR the date predates the model's
    first period (e.g. a deepseek-v4-pro date before its 2026-04-24 launch) —
    callers treat None as "unknown cost", never zero.
    """
    history = _resolve_history(model_id)
    if not history:
        return None
    period = None
    for p in history:
        eff = p.get("effective_from")
        if eff is None or eff <= on_date:
            period = p
        else:
            break
    if period is None:
        return None
    for tier in period["tiers"]:
        cap = tier.get("max_input_tokens")
        if cap is None or input_tokens <= cap:
            return tier
    return None


def compute_call_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    on_date: str | None = None,
) -> float | None:
    """Compute the USD cost of a single API call at the rate in force on a date.

    ``on_date`` is a "YYYY-MM-DD" UTC date; omit it (the adapters' call-time
    path) to price at today's rates. Pass the decision-log record's ``date``
    when re-pricing historical calls so attribution follows the provider's
    rate timeline, not the current price.

    Returns None if we don't have a rate for the model on that date — caller
    should treat that as "unknown" rather than zero so the report can flag it.
    """
    rates = _rates_for(model_id, on_date or _today_utc(), int(input_tokens or 0))
    if not rates:
        return None
    return (
        (input_tokens / 1_000_000.0) * rates["input"]
        + (output_tokens / 1_000_000.0) * rates["output"]
    )


def _current_flat_view() -> dict[str, dict[str, float]]:
    """Today's first-tier rates, flattened to the pre-2026-08 table shape.

    Kept so existing imports of COST_PER_MTOK keep working; every lab call
    sits in tier 1, so the flat view is exact for lab traffic. Prefer
    compute_call_cost_usd() for anything new.
    """
    today = _today_utc()
    flat: dict[str, dict[str, float]] = {}
    for model_id in RATE_HISTORY:
        rates = _rates_for(model_id, today, 0)
        if rates:
            flat[model_id] = {"input": rates["input"], "output": rates["output"]}
    return flat


# Backward-compatible view of current rates (see _current_flat_view).
COST_PER_MTOK: dict[str, dict[str, float]] = _current_flat_view()
