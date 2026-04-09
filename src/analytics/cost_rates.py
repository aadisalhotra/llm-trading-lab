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
    # Other providers — placeholders, not used until we add usage capture
    # to those adapters. The expansion cohort section only needs Anthropic
    # rates because both compared models are Anthropic.
    "gpt-5.4":                {"input": 10.00, "output": 30.00},
    "gemini-3.1-pro-preview": {"input": 3.50,  "output": 14.00},
    "grok-4":                 {"input": 5.00,  "output": 15.00},
    "deepseek-chat":          {"input": 0.27,  "output": 1.10},
}


def compute_call_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Compute the USD cost of a single API call.

    Returns None if we don't have a rate for the model — caller should
    treat that as "unknown" rather than zero so the report can flag it.
    """
    rates = COST_PER_MTOK.get(model_id)
    if not rates:
        return None
    return (
        (input_tokens / 1_000_000.0) * rates["input"]
        + (output_tokens / 1_000_000.0) * rates["output"]
    )
