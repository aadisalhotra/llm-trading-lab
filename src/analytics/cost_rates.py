"""Static USD cost rates per million tokens for each LLM provider/model.

These mirror published list pricing (no negotiated discounts) so the
cost-performance comparison in the daily report tracks the *out-of-pocket*
cost a researcher running this lab would actually incur. Numbers are
hand-edited as providers update pricing — there is no live billing API.

Used by:
- adapters/anthropic_adapter.py to populate DecisionResult.metadata with
  per-call USD cost based on returned token usage
- analytics/__init__.py.compute_total_api_cost() to sum across all calls
- reports/daily_report.py expansion cohort section
"""
from __future__ import annotations

# USD per 1,000,000 tokens. Keys are model_id strings (the same string
# stored in settings.json under models.<key>.model and echoed back by the
# adapter on every call).
COST_PER_MTOK: dict[str, dict[str, float]] = {
    # Anthropic — Sonnet is roughly 5x cheaper than Opus on both ends
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00},
    # OpenAI flagship chat models
    "gpt-5.4":                {"input": 10.00, "output": 30.00},
    # Google Gemini
    "gemini-3.1-pro-preview": {"input": 3.50,  "output": 14.00},
    # xAI Grok — keep base "grok-4" entry so the prefix-fallback resolves
    # legacy returned IDs like "grok-4-0709"; the production string we
    # send is now grok-4.20-0309-reasoning.
    "grok-4":                      {"input": 5.00,  "output": 15.00},
    "grok-4.20-0309-reasoning":    {"input": 5.00,  "output": 25.00},
    # DeepSeek — v4-pro is the production string we now send (the
    # deepseek-reasoner alias was repointed to deepseek-v4-pro on 2026-05-21).
    # v4-flash is what the provider returned 2026-04-24..2026-05-21 after it
    # drifted the reasoner alias; kept so a historical recompute of that window
    # resolves a real cost. Rates are DeepSeek's STANDARD post-promotional list
    # pricing — NOT the 75%-off promo, which expires 2026-05-31 — so the table
    # reflects the cost that applies going forward. Reasoning traces count as
    # output tokens, so output bills higher. chat/reasoner are retained as
    # legacy entries for older returned IDs.
    "deepseek-chat":     {"input": 0.27,  "output": 1.10},
    "deepseek-reasoner": {"input": 0.55,  "output": 2.19},
    "deepseek-v4-pro":   {"input": 1.74,  "output": 3.48},
    "deepseek-v4-flash": {"input": 0.14,  "output": 0.28},
}


def _resolve_rates(model_id: str) -> dict[str, float] | None:
    """Look up rates with prefix fallback for versioned model IDs.

    Providers return date-versioned strings like `gpt-5.4-2026-03-05` or
    `grok-4-0709`. The rate table is keyed by the base model name. We try
    the full ID first, then progressively shorter dash-delimited prefixes
    until we find a match. This avoids having to add every datestamped
    snapshot manually as providers cut new pinned versions.

    Stops at 1 segment to avoid degenerate matches like "gpt" or "grok".
    """
    if not model_id:
        return None
    if model_id in COST_PER_MTOK:
        return COST_PER_MTOK[model_id]
    parts = model_id.split("-")
    while len(parts) > 1:
        parts.pop()
        candidate = "-".join(parts)
        if candidate in COST_PER_MTOK:
            return COST_PER_MTOK[candidate]
    return None


def compute_call_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Compute the USD cost of a single API call.

    Returns None if we don't have a rate for the model — caller should
    treat that as "unknown" rather than zero so the report can flag it.
    """
    rates = _resolve_rates(model_id)
    if not rates:
        return None
    return (
        (input_tokens / 1_000_000.0) * rates["input"]
        + (output_tokens / 1_000_000.0) * rates["output"]
    )
