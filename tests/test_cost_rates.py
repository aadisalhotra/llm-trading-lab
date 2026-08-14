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
    DEEPSEEK_PEAK_WINDOWS_UTC,
    LEGACY_FLAT_TABLE_PRE_2026_08,
    RATE_HISTORY,
    backsolve_screening_input_tokens,
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


def test_opus_46_has_no_long_context_tier():
    # 4.6-generation models bill the full 1M window at standard pricing, so
    # the >200K premium ($10/$37.50) carried until 2026-08-05 was a rate the
    # provider denies. A 500K-token prompt must still price at $5/$25.
    periods = RATE_HISTORY["claude-opus-4-6"]
    assert len(periods) == 1
    assert len(periods[0]["tiers"]) == 1
    assert periods[0]["tiers"][0]["max_input_tokens"] is None
    huge = compute_call_cost_usd("claude-opus-4-6", 500_000, 1_000, on_date="2026-07-15")
    assert huge == (500_000 / 1e6) * 5.00 + (1_000 / 1e6) * 25.00


def test_screening_backsolve_inverts_legacy_pricing():
    # A screening call's input tokens are recoverable from its logged cost.
    # Build a cost the way the pre-2026-08 table would have, then invert it.
    in_rate, out_rate = LEGACY_FLAT_TABLE_PRE_2026_08["gpt-5.4"]
    true_in, true_out = 8_412, 611
    logged = round((true_in / 1e6) * in_rate + (true_out / 1e6) * out_rate, 6)
    assert backsolve_screening_input_tokens("gpt-5.4", logged, true_out) == true_in
    # Prefix fallback works the same way the adapters resolved versioned IDs.
    assert backsolve_screening_input_tokens("gpt-5.4-2026-03-05", logged, true_out) == true_in


def test_screening_backsolve_refuses_impossible_inputs():
    # Missing pieces, unknown models, and a cost too small to cover the logged
    # output tokens all return None — never a silent zero.
    assert backsolve_screening_input_tokens("gpt-5.4", None, 100) is None
    assert backsolve_screening_input_tokens("gpt-5.4", 0.01, None) is None
    assert backsolve_screening_input_tokens("not-a-model", 0.01, 100) is None
    # $0.000001 cannot have paid for 1M output tokens at $30/MTok.
    assert backsolve_screening_input_tokens("gpt-5.4", 0.000001, 1_000_000) is None


def test_gpt54_flat_since_launch():
    # $2.50/$15 from launch day through today — no cut ever happened.
    for d in ("2026-04-08", "2026-06-15", "2026-08-02"):
        assert rate_sum("gpt-5.4", d) == 2.50 + 15.00


def test_grok_420_period_split_at_may_7():
    model = "grok-4.20-0309-reasoning"
    # The cut date is bounded, not attested: last old-rate archive capture
    # 2026-05-01 07:03 UTC, first new-rate capture 2026-05-06 16:31 UTC.
    # Boundary is archive-conservative — the old (higher) rate holds for the
    # whole ambiguity window, so every day inside it attributes at $2/$6.
    assert rate_sum(model, "2026-04-15") == 2.00 + 6.00
    assert rate_sum(model, "2026-04-30") == 2.00 + 6.00
    for d in ("2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06"):
        assert rate_sum(model, d) == 2.00 + 6.00
    assert rate_sum(model, "2026-05-07") == 1.25 + 2.50
    assert rate_sum(model, "2026-07-31") == 1.25 + 2.50


def test_grok_420_period_1_high_tier_is_marked_unverified():
    # The >200K period-1 tier ($4/$12) could not be confirmed against any
    # archived capture. It must stay flagged in the table's provenance note
    # so a future reader does not treat it as verified list pricing.
    period_1 = RATE_HISTORY["grok-4.20-0309-reasoning"][0]
    assert "UNVERIFIED" in period_1["note"]
    assert period_1["tiers"][1]["input"] == 4.00
    assert period_1["tiers"][1]["output"] == 12.00


def test_deepseek_v4_pro_promo_rate_through_2026_08_15():
    # The 75%-off launch rate was made permanent; $1.74/$3.48 never applied.
    # It held until the 2026-08-16 schedule change (below).
    for d in ("2026-04-24", "2026-06-01", "2026-07-31", "2026-08-15"):
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
    # DeepSeek's rate changes on 2026-08-16, so asserting a literal here would
    # turn this test into a time bomb. Assert the property instead: omitting
    # on_date must equal passing today's UTC date explicitly.
    from src.analytics.cost_rates import _today_utc

    assert (
        compute_call_cost_usd("deepseek-v4-pro", LAB_IN, LAB_OUT)
        == compute_call_cost_usd("deepseek-v4-pro", LAB_IN, LAB_OUT, on_date=_today_utc())
    )


def test_cost_per_mtok_compat_view():
    # The flat view keeps old imports working and reflects current tier-1 rates.
    assert COST_PER_MTOK["claude-sonnet-4-6"] == {"input": 3.00, "output": 15.00}
    assert COST_PER_MTOK["claude-opus-4-6"] == {"input": 5.00, "output": 25.00}
    assert COST_PER_MTOK["grok-4.20-0309-reasoning"] == {"input": 1.25, "output": 2.50}


# --- DeepSeek 2026-08-16 peak/off-peak schedule ---------------------------
#
# Provider announcement 2026-08-14, effective 2026-08-16T16:00Z. Units verified
# against api-docs.deepseek.com/quick_start/pricing: USD per 1M tokens, columns
# cache-hit input / cache-miss input / output. These tests lock the exact
# transcription, because a units slip here is the failure mode that put four of
# the six pre-2026-08 rates in the table wrong.

DS_ANNOUNCED = {
    # model: {period: (cache_hit_input, cache_miss_input, output)}
    "deepseek-v4-pro": {
        "off_peak": (0.022, 0.66, 1.98),
        "peak": (0.044, 1.32, 3.96),
    },
    "deepseek-v4-flash": {
        "off_peak": (0.007, 0.22, 0.66),
        "peak": (0.014, 0.44, 1.32),
    },
}


def _new_period(model: str) -> dict:
    return next(p for p in RATE_HISTORY[model] if p["effective_from"] == "2026-08-16")


def test_deepseek_schedule_transcribed_exactly():
    """Every announced number, all three columns, both models, both schedules."""
    for model, expected in DS_ANNOUNCED.items():
        sched = _new_period(model)["schedule"]
        for period, (hit, miss, out) in expected.items():
            assert sched[period]["cache_hit_input"] == hit, (model, period)
            assert sched[period]["cache_miss_input"] == miss, (model, period)
            assert sched[period]["output"] == out, (model, period)


def test_deepseek_peak_is_exactly_double_off_peak():
    """The announcement defines peak as 2x off-peak on every column."""
    for model in DS_ANNOUNCED:
        sched = _new_period(model)["schedule"]
        for column in ("cache_hit_input", "cache_miss_input", "output"):
            assert abs(sched["peak"][column] - 2 * sched["off_peak"][column]) < 1e-12, (
                model,
                column,
            )


def test_deepseek_lookup_tiers_are_the_off_peak_cache_miss_rates():
    """`tiers` must stay consistent with the schedule it claims to carry.

    The resolver is date-only, so it returns one schedule for every call. That
    schedule is off-peak, and the input rate is cache-MISS (the lab logs carry
    no cached-token split). This test is what catches a future edit that
    updates one of the two representations and not the other.
    """
    for model in DS_ANNOUNCED:
        period = _new_period(model)
        sched = period["schedule"]
        assert sched["basis"] == "off_peak"
        assert len(period["tiers"]) == 1
        tier = period["tiers"][0]
        assert tier["max_input_tokens"] is None
        assert tier["input"] == sched["off_peak"]["cache_miss_input"]
        assert tier["output"] == sched["off_peak"]["output"]


def test_deepseek_peak_windows_are_01_04_and_06_10_utc():
    # 09:00-12:00 and 14:00-18:00 Beijing = 01:00-04:00 and 06:00-10:00 UTC.
    assert DEEPSEEK_PEAK_WINDOWS_UTC == ((1, 4), (6, 10))
    # Every window the published schedule names, and no others. The lab's
    # decision cycles (13:00-21:00 UTC) must not intersect any of them.
    lab_hours = set(range(13, 22))
    peak_hours = {h for start, end in DEEPSEEK_PEAK_WINDOWS_UTC for h in range(start, end)}
    assert lab_hours & peak_hours == set()


def test_deepseek_v4_pro_boundary_at_2026_08_16():
    # Last day of the old rate, first day of the new. 2026-08-16 is a Sunday
    # and no model-calling cron runs on weekends, so the date-granular boundary
    # never has to represent the mid-day 16:00Z switch for a real call.
    assert rate_sum("deepseek-v4-pro", "2026-08-15") == 0.435 + 0.87
    assert rate_sum("deepseek-v4-pro", "2026-08-16") == 0.66 + 1.98
    assert rate_sum("deepseek-v4-pro", "2026-08-17") == 0.66 + 1.98


def test_deepseek_v4_flash_boundary_at_2026_08_16():
    # rate_sum() rounds to 6dp; round the expectation too so binary float
    # noise (0.14 + 0.28 == 0.42000000000000004) isn't read as a rate error.
    assert rate_sum("deepseek-v4-flash", "2026-05-20") == round(0.14 + 0.28, 6)
    assert rate_sum("deepseek-v4-flash", "2026-08-15") == round(0.14 + 0.28, 6)
    assert rate_sum("deepseek-v4-flash", "2026-08-16") == round(0.22 + 0.66, 6)


def test_deepseek_history_stays_priced_at_the_old_rate():
    """Appending a period must not reprice a single historical call.

    The whole point of the dated table: July's costs are July's rates. A
    regression that made the new period retroactive would silently inflate
    every cost figure already published for April-August 15.
    """
    for d in ("2026-04-24", "2026-05-21", "2026-06-15", "2026-07-31", "2026-08-13"):
        assert rate_sum("deepseek-v4-pro", d) == 0.435 + 0.87


def test_deepseek_time_of_day_is_not_resolved_yet():
    """A peak-window call is knowingly underpriced 2x — documented, not silent.

    compute_call_cost_usd takes no timestamp, so it cannot distinguish a
    02:00Z call from a 15:00Z one. Until the per-call classifier lands, the
    entry note must say so in the table itself, where anyone reading a
    DeepSeek cost figure will find it.
    """
    for model in DS_ANNOUNCED:
        note = _new_period(model)["note"]
        assert "peak" in note.lower()
        assert "2026-08-16T16:00Z" in note
    pro_note = _new_period("deepseek-v4-pro")["note"]
    assert "HALF its true cost" in pro_note


# --- rider 1: read-time repricing (2026-08-05) ---------------------------


def test_reprice_record_uses_record_date_not_logged_cost():
    """A record's stored cost is ignored; the date drives the rate period."""
    from src.analytics.performance import reprice_record_usd

    base = {
        "model_id_returned": "grok-4.20-0309-reasoning",
        "input_tokens": 10_000,
        "output_tokens": 2_000,
        # Deliberately absurd stored value — repricing must not read it.
        "cost_usd": 999.99,
    }
    before = reprice_record_usd({**base, "date": "2026-05-06"})
    after = reprice_record_usd({**base, "date": "2026-05-07"})
    # Inside the ambiguity window: old rate $2/$6.
    assert before["decision_usd"] == (10_000 / 1e6) * 2.00 + (2_000 / 1e6) * 6.00
    # From the boundary: cut rate $1.25/$2.50.
    assert after["decision_usd"] == (10_000 / 1e6) * 1.25 + (2_000 / 1e6) * 2.50
    assert before["decision_usd"] > after["decision_usd"]


def test_reprice_record_prefers_logged_screening_input_over_backsolve():
    """New records carry screening_input_tokens; the back-solve is skipped."""
    from src.analytics.performance import reprice_record_usd

    priced = reprice_record_usd({
        "model_id_returned": "claude-sonnet-4-6",
        "date": "2026-08-05",
        "input_tokens": 5_000,
        "output_tokens": 1_000,
        "screening_input_tokens": 4_000,
        "screening_tokens": 500,
        "screening_cost_usd": 0.123456,
    })
    assert priced["screening_backsolved"] is False
    assert priced["screening_usd"] == (4_000 / 1e6) * 3.00 + (500 / 1e6) * 15.00


def test_reprice_record_backsolves_when_input_absent():
    """Historical records with no screening_input_tokens invert their cost."""
    from src.analytics.performance import reprice_record_usd

    in_rate, out_rate = LEGACY_FLAT_TABLE_PRE_2026_08["claude-sonnet-4-6"]
    true_in, true_out = 6_000, 400
    logged = round((true_in / 1e6) * in_rate + (true_out / 1e6) * out_rate, 6)
    priced = reprice_record_usd({
        "model_id_returned": "claude-sonnet-4-6",
        "date": "2026-06-01",
        "input_tokens": 5_000,
        "output_tokens": 1_000,
        "screening_tokens": true_out,
        "screening_cost_usd": logged,
    })
    assert priced["screening_backsolved"] is True
    # Sonnet's rate was correct all along, so the repriced screening cost
    # equals the logged one — the back-solve recovered the right token count.
    assert abs(priced["screening_usd"] - logged) < 1e-6


def test_reprice_record_no_screening_call():
    from src.analytics.performance import reprice_record_usd

    priced = reprice_record_usd({
        "model_id_returned": "gpt-5.4",
        "date": "2026-07-01",
        "input_tokens": 1_000,
        "output_tokens": 100,
    })
    assert priced["screening_usd"] is None
    assert priced["screening_backsolved"] is False
    assert priced["decision_usd"] == (1_000 / 1e6) * 2.50 + (100 / 1e6) * 15.00


def test_reprice_record_untokened_shakedown_record_is_unpriced():
    """April 8-9 records predate token logging — unpriceable, never zero."""
    from src.analytics.performance import reprice_record_usd

    priced = reprice_record_usd({
        "model_id_returned": "claude-opus-4-6",
        "date": "2026-04-08",
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
    })
    assert priced["decision_usd"] is None
    assert priced["screening_usd"] is None
