"""Tests for the dated-rate-period cost table (2026-08-02 restructure).

Covers period resolution by date, tier selection by prompt size, prefix
fallback for versioned model IDs, the call-time default path, and the
backward-compatible COST_PER_MTOK flat view.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analytics.cost_rates import (  # noqa: E402
    COST_PER_MTOK,
    compute_call_cost_usd,
)

MTOK = 1_000_000
# Lab-sized call (~100K in / 100K out) — sits in every model's first tier.
# rate_sum() scales it back up so assertions read as (input + output) $/MTok.
LAB_IN = 100_000
LAB_OUT = 100_000


def rate_sum(model: str, on_date: str) -> float | None:
    cost = compute_call_cost_usd(model, LAB_IN, LAB_OUT, on_date=on_date)
    return None if cost is None else round(cost * 10, 6)


def test_sonnet_rate_unchanged():
    # Sonnet 4.6 was verified correct at $3/$15 for the whole lab window.
    assert rate_sum("claude-sonnet-4-6", "2026-07-15") == 3.00 + 15.00


def test_opus_46_is_5_25_not_15_75():
    # The old $15/$75 entry was never a real Opus 4.6 price.
    assert rate_sum("claude-opus-4-6", "2026-04-09") == 5.00 + 25.00


def test_gpt54_flat_since_launch():
    # $2.50/$15 from launch day through today — no cut ever happened.
    for d in ("2026-04-08", "2026-06-15", "2026-08-02"):
        assert rate_sum("gpt-5.4", d) == 2.50 + 15.00


def test_grok_420_period_split_at_may_1():
    model = "grok-4.20-0309-reasoning"
    # Launch pricing through Apr 30 (cut bounded Apr 30 -> May 1 UTC;
    # 2026-05-01 is the conservative effective date).
    assert rate_sum(model, "2026-04-15") == 2.00 + 6.00
    assert rate_sum(model, "2026-04-30") == 2.00 + 6.00
    assert rate_sum(model, "2026-05-01") == 1.25 + 2.50
    assert rate_sum(model, "2026-07-31") == 1.25 + 2.50


def test_deepseek_v4_pro_promo_rate_throughout():
    # The 75%-off launch rate was made permanent; $1.74/$3.48 never applied.
    for d in ("2026-04-24", "2026-06-01", "2026-07-31"):
        assert rate_sum("deepseek-v4-pro", d) == 0.435 + 0.87


def test_deepseek_v4_pro_predates_launch_is_unknown():
    # A date before the model existed must resolve to None (unknown), not zero.
    assert rate_sum("deepseek-v4-pro", "2026-04-01") is None


def test_gemini_tier_selection():
    model = "gemini-3.1-pro-preview"
    # Lab-sized prompt: <=200K tier at $2/$12.
    small = compute_call_cost_usd(model, 100_000, 10_000, on_date="2026-07-01")
    assert small is not None
    assert abs(small - (0.1 * 2.00 + 0.01 * 12.00)) < 1e-9
    # Oversized prompt: whole request bills at the >200K tier ($4/$18).
    big = compute_call_cost_usd(model, 250_000, 10_000, on_date="2026-07-01")
    assert big is not None
    assert abs(big - (0.25 * 4.00 + 0.01 * 18.00)) < 1e-9


def test_prefix_fallback_for_versioned_ids():
    # Datestamped snapshot IDs resolve through the base entry.
    assert rate_sum("gpt-5.4-2026-03-05", "2026-07-01") == 2.50 + 15.00
    # Legacy grok-4 returned IDs resolve to the retained legacy entry.
    assert rate_sum("grok-4-0709", "2026-04-10") == 5.00 + 15.00


def test_unknown_model_is_none():
    assert rate_sum("not-a-model", "2026-07-01") is None


def test_default_date_is_today():
    # Call-time path (adapters pass no date) must price at current rates.
    assert compute_call_cost_usd("claude-sonnet-4-6", LAB_IN, LAB_OUT) is not None
    assert round(compute_call_cost_usd("claude-sonnet-4-6", LAB_IN, LAB_OUT) * 10, 6) == 18.00
    assert round(compute_call_cost_usd("deepseek-v4-pro", LAB_IN, LAB_OUT) * 10, 6) == 1.305


def test_cost_per_mtok_compat_view():
    # The flat view keeps old imports working and reflects current tier-1 rates.
    assert COST_PER_MTOK["claude-sonnet-4-6"] == {"input": 3.00, "output": 15.00}
    assert COST_PER_MTOK["claude-opus-4-6"] == {"input": 5.00, "output": 25.00}
    assert COST_PER_MTOK["grok-4.20-0309-reasoning"] == {"input": 1.25, "output": 2.50}
