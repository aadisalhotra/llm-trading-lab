"""December plumbing (a)/(c): eleven books, the shakedown, the handover.

cohort_refresh_ruling.md rev. 2: the five refreshed arms (cb_*) decide on
internal paper from 2026-12-01 (section 3 item 8, the shakedown), the venue path
passes to them at the handover of 2026-12-05/06 (section 4 item 2) with the
barrier rotating over five, and each snapshot's venue book closes as a series
and incepts fresh on internal paper at the registered Phase B book capital
(section 4 item 4). Hub H-OPS-07 ruling 1: no cb_* book is public before the
open of 2026-12-07. Hub H-OPS-14: the snapshots and DeepSeek come under the
tick deadline and the finish-reason rule from the handover.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config_loader as cl  # noqa: E402
from src.adapters import get_adapter  # noqa: E402
from src.adapters.deepseek_adapter import DeepSeekAdapter  # noqa: E402
from src.execution.barrier import submission_order  # noqa: E402
from src.portfolio import portfolio as pf  # noqa: E402

ET = ZoneInfo("America/New_York")
SNAPSHOTS = ["claude", "gpt", "gemini", "grok", "claude_opus"]
REFRESHED = ["cb_sonnet", "cb_opus", "cb_astra", "cb_grok", "cb_argon"]
SHIPPED = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def et(*a) -> datetime:
    return datetime(*a, tzinfo=ET)


def handed_over(settings: dict) -> dict:
    """The settings as the transition script leaves them: the venue cohort is
    the five refreshed arms."""
    s = copy.deepcopy(settings)
    s["capital_structure"]["live_cohort_keys"] = list(REFRESHED)
    return s


# ------------------------------------------------------------ configuration

def test_eleven_books_are_configured_and_the_refreshed_arms_are_the_registered_keys():
    models = SHIPPED["models"]
    assert list(models) == ["claude", "gpt", "gemini", "grok", "deepseek", "claude_opus", *REFRESHED]
    for key, model_id in cl.REFRESHED_BOOK_KEYS.items():
        cfg = models[key]
        assert cfg["model"] == model_id and cfg["enabled"] is True
        assert cfg["cohort"] == "refreshed" and cfg["active_from"] == "2026-12-01"
        assert cfg["public_from"] == "2026-12-07T09:30:00-05:00"


def test_no_book_key_is_a_prefix_of_another():
    keys = list(SHIPPED["models"])
    clashes = [(a, b) for a in keys for b in keys if a != b and b.startswith(a)]
    # claude/claude_opus is the recorded pre-existing exception.
    assert clashes == [("claude", "claude_opus")]


def test_the_venue_cohort_is_unchanged_until_the_transition_script_runs():
    assert cl.live_cohort_keys(SHIPPED) == ["claude", "claude_opus", "gpt", "gemini", "grok"]
    for key in REFRESHED + ["deepseek"]:
        assert cl.effective_mode(key, SHIPPED) == "paper"


# ------------------------------------------------------- which books run when

@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 30), 6),
    (date(2026, 12, 1), 11),     # the first shakedown session
    (date(2026, 12, 7), 11),
])
def test_the_refreshed_arms_join_on_their_first_session(day, expected):
    keys = cl.active_model_keys(SHIPPED, day)
    assert len(keys) == expected
    assert all(k in keys for k in SNAPSHOTS + ["deepseek"])


def test_activity_is_read_on_the_eastern_date():
    # 2026-12-01 03:00Z is still 11-30 in New York.
    from datetime import timezone
    assert not cl.book_active("cb_grok", SHIPPED, datetime(2026, 12, 1, 3, tzinfo=timezone.utc))
    assert cl.book_active("cb_grok", SHIPPED, datetime(2026, 12, 1, 14, 35, tzinfo=timezone.utc))


def test_a_disabled_book_is_never_active():
    s = copy.deepcopy(SHIPPED)
    s["models"]["cb_argon"]["enabled"] = False
    assert "cb_argon" not in cl.active_model_keys(s, date(2026, 12, 8))


# ----------------------------------------------------- H-OPS-07 ruling 1

@pytest.mark.parametrize("at,public", [
    (et(2026, 12, 1, 16, 5), False),    # shakedown EOD
    (et(2026, 12, 4, 16, 5), False),    # last shakedown EOD
    (et(2026, 12, 7, 9, 29), False),    # a minute before the open
    (et(2026, 12, 7, 9, 30), True),     # the open of the first validated session
    (et(2026, 12, 7, 16, 5), True),
])
def test_no_refreshed_book_is_public_before_the_open_of_12_07(at, public):
    keys = cl.public_model_keys(SHIPPED, at)
    assert all(k in keys for k in SNAPSHOTS + ["deepseek"])
    assert all((k in keys) is public for k in REFRESHED)


def test_public_from_must_name_its_offset():
    s = copy.deepcopy(SHIPPED)
    s["models"]["cb_opus"]["public_from"] = "2026-12-07T09:30:00"
    with pytest.raises(ValueError, match="UTC offset"):
        cl.book_public("cb_opus", s, et(2026, 12, 8, 10))


def _stub_dashboard(monkeypatch, tmp_path, settings):
    """Drive build_dashboard_payload with every reader stubbed, recording the
    book keys each panel is built from."""
    from src.dashboard import build_data as bd
    seen: dict[str, list] = {}

    def rec(name, ret):
        def f(keys, *a, **k):
            seen[name] = list(keys)
            return ret() if callable(ret) else ret
        return f

    loaded: list[str] = []

    def load_portfolio(key):
        loaded.append(key)
        return SimpleNamespace(last_updated="2026-12-04 21:00:00",
                               snapshot=lambda prices: {"model_key": key, "holdings": []})

    monkeypatch.setattr(bd, "load_settings", lambda: settings)
    monkeypatch.setattr(bd, "load_universe", lambda: {"tickers": []})
    monkeypatch.setattr(bd, "build_leaderboard",
                        rec("leaderboard", lambda: [{"model_key": k} for k in seen["leaderboard"]]))
    monkeypatch.setattr(bd, "_recent_summaries_per_model", rec("summaries", {}))
    monkeypatch.setattr(bd, "compute_spy_benchmark_metrics", lambda **k: None)
    monkeypatch.setattr(bd, "_starting_capital_guarded", lambda *a, **k: 2000.0)
    monkeypatch.setattr(bd, "load_portfolio", load_portfolio)
    monkeypatch.setattr(bd, "load_eod_history", lambda key: pd.DataFrame())
    monkeypatch.setattr(bd, "annotate_cash_flags", lambda *a, **k: None)
    monkeypatch.setattr(bd, "_equity_curve", lambda key: [])
    monkeypatch.setattr(bd, "canonical_spy_series", lambda s: None)
    monkeypatch.setattr(bd, "_intraday_curve", lambda key, d: [])
    monkeypatch.setattr(bd, "_build_cost_tracker", rec("cost_tracker", []))
    monkeypatch.setattr(bd, "compute_budget_status", lambda s: {"providers": {}})
    monkeypatch.setattr(bd, "_find_mvp_trade", rec("mvp", None))
    monkeypatch.setattr(bd, "_build_ticker_tape", lambda p, u: [])
    monkeypatch.setattr(bd, "_build_market_brief",
                        lambda lb, p, keys, *a: seen.__setitem__("market_brief", list(keys)) or {})
    monkeypatch.setattr(bd, "_recent_trades", rec("trade_feed", []))
    monkeypatch.setattr(bd, "_consensus_picks",
                        lambda p, keys, r: seen.__setitem__("consensus", list(keys)) or [])
    monkeypatch.setattr(bd, "_compute_trade_analytics", rec("analytics", (None, {}, {})))
    monkeypatch.setattr(bd, "_compute_correlation_matrix", rec("correlation", None))
    monkeypatch.setattr(bd, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bd, "LEADERBOARD_DIR", tmp_path / "leaderboard")
    return bd, seen, loaded


@pytest.mark.parametrize("when,shown", [(et(2026, 12, 3, 16, 5), False), (et(2026, 12, 7, 10, 5), True)])
def test_the_dashboard_shows_no_refreshed_book_before_the_open_of_12_07(monkeypatch, tmp_path, when, shown):
    bd, seen, loaded = _stub_dashboard(monkeypatch, tmp_path, SHIPPED)
    payload = bd.build_dashboard_payload(prices={}, run_date=when)
    expected = SNAPSHOTS[:4] + ["deepseek", "claude_opus"] + (REFRESHED if shown else [])
    assert list(payload["models"]) == expected
    assert [p["model_key"] for p in payload["portfolios"]] == expected
    assert loaded == expected, "a book that is not public must not even be loaded (load incepts)"
    for panel in ("leaderboard", "cost_tracker", "trade_feed", "consensus", "correlation",
                  "market_brief", "mvp"):
        assert seen[panel] == expected, panel


def test_the_daily_report_lists_public_books_only():
    src = (ROOT / "src" / "reports" / "daily_report.py").read_text(encoding="utf-8")
    assert "model_keys = public_model_keys(settings, run_date)" in src


# ----------------------------------------------------------- the shakedown

def test_a_shakedown_book_incepts_on_internal_paper_at_2000(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "STATE_DIR", tmp_path)
    monkeypatch.setattr(pf, "load_settings", lambda: SHIPPED)
    p = pf.load_portfolio("cb_astra")
    assert p.inception_epoch == "paper" and p.cash == 2000.0 and p.inception_value == 2000.0
    # DeepSeek's book is untouched by the key: $100,000 on paper.
    assert cl.book_inception_capital("deepseek", SHIPPED) == 100_000.0


# ------------------------------------------------------------- the handover

def test_after_the_switch_the_barrier_and_reconciliation_cover_the_five_refreshed_books():
    s = handed_over(SHIPPED)
    active = cl.active_model_keys(s, date(2026, 12, 7))
    venue = cl.venue_book_keys(active, s)
    assert sorted(venue) == sorted(REFRESHED)
    for key in SNAPSHOTS + ["deepseek"]:
        assert cl.effective_mode(key, s) == "paper"
    # Rotation over five: every book leads once in five consecutive slots.
    leads = {submission_order(sorted(venue), t)[0] for t in range(100, 105)}
    assert leads == set(REFRESHED)
    assert cl.registered_capital_base(s, "broker_paper") == 10_000.0


def test_each_book_needs_its_archive_and_then_incepts_fresh(monkeypatch, tmp_path):
    """The epoch guard refuses a book carried across the switch; once the
    state file is archived (the transition script's step), the snapshot
    incepts on internal paper and the refreshed arm on the venue, each at the
    registered $2,000. Nothing carries."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(pf, "STATE_DIR", state)
    before = SHIPPED
    monkeypatch.setattr(pf, "load_settings", lambda: before)
    # Before the switch: a snapshot's venue book and a shakedown book.
    venue_book = pf.Portfolio(model_key="gpt", cash=12.5, holdings={}, halted=False,
                              inception_value=2000.0, inception_date="2026-11-06",
                              last_updated="", inception_epoch="broker_paper")
    pf.save_portfolio(venue_book)
    shakedown = pf.load_portfolio("cb_sonnet")
    assert shakedown.inception_epoch == "paper"

    after = handed_over(SHIPPED)
    monkeypatch.setattr(pf, "load_settings", lambda: after)
    for key in ("gpt", "cb_sonnet"):
        with pytest.raises(pf.InceptionEpochError):
            pf.load_portfolio(key)

    archive = tmp_path / "state_archive"
    archive.mkdir()
    for key in ("gpt", "cb_sonnet"):
        (state / f"{key}.json").rename(archive / f"{key}.json")
    gpt = pf.load_portfolio("gpt")
    sonnet = pf.load_portfolio("cb_sonnet")
    assert (gpt.inception_epoch, gpt.cash) == ("paper", 2000.0)
    assert (sonnet.inception_epoch, sonnet.cash) == ("broker_paper", 2000.0)
    assert json.loads((archive / "gpt.json").read_text())["cash"] == 12.5


# ---------------------------------------------- H-OPS-14: the handover rules

@pytest.mark.parametrize("day,applies", [(date(2026, 12, 4), False), (date(2026, 12, 5), True),
                                         (date(2026, 12, 7), True)])
def test_the_handover_rules_start_on_the_handover_date(day, applies):
    assert cl.handover_rules_apply(SHIPPED, day) is applies


def test_without_a_handover_date_the_rules_never_apply():
    s = copy.deepcopy(SHIPPED)
    del s["handover"]
    assert cl.handover_rules_apply(s, date(2027, 1, 4)) is False


def test_tick_rules_bind_a_refreshed_arm_always_and_a_snapshot_from_the_handover():
    snap = get_adapter("openai", "gpt-5.4-2026-03-05")
    arm = get_adapter("openai", "gpt-6-astra")
    assert arm.tick_rules and not snap.tick_rules
    snap.deadline_at = 10.0
    assert snap.remaining() is None
    snap.handover_rules = True
    assert snap.tick_rules and snap.remaining() is not None


class _Resp:
    def __init__(self, finish):
        self.ok, self.status_code, self.text = True, 200, ""
        self._body = {"model": "deepseek-v4-pro",
                      "choices": [{"message": {"content": '{"decisions": [], "overall_reasoning": "x"}'},
                                   "finish_reason": finish}],
                      "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

    def json(self):
        return self._body


@pytest.mark.parametrize("finish,cls", [("length", "DECISION_TRUNCATED"),
                                        ("content_filter", "REFUSAL"),
                                        ("insufficient_system_resource", "ABNORMAL_FINISH")])
def test_deepseek_under_the_handover_rules(monkeypatch, finish, cls):
    calls = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr("src.adapters.deepseek_adapter.requests.post",
                        lambda *a, **k: calls.append(k) or _Resp(finish))
    a = DeepSeekAdapter(model="deepseek-v4-pro")
    a.handover_rules = True
    r = a.generate_decision("S", "U")
    assert r.success is False and r.error_class == cls and len(calls) == 1
    assert r.metadata["finish_class"]


def test_deepseek_before_the_handover_is_unchanged(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr("src.adapters.deepseek_adapter.requests.post", lambda *a, **k: _Resp("length"))
    r = DeepSeekAdapter(model="deepseek-v4-pro").generate_decision("S", "U")
    assert r.success is True and "finish_class" not in r.metadata
    a = DeepSeekAdapter(model="deepseek-v4-pro")
    a.handover_rules = True
    monkeypatch.setattr("src.adapters.deepseek_adapter.requests.post", lambda *a, **k: _Resp("stop"))
    r = a.generate_decision("S", "U")
    assert r.success is True and r.metadata["finish_class"] == "normal"


def test_a_snapshot_under_the_handover_rules_keeps_its_identity_and_level_unchanged(monkeypatch):
    import openai
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    sent = []

    def reply(finish, refusal=None):
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=10, completion_tokens_details=None,
                                prompt_tokens_details=None)
        msg = SimpleNamespace(content='{"decisions": [], "overall_reasoning": "x"}', refusal=refusal)
        return SimpleNamespace(model="gpt-5.4-2026-03-05",
                               choices=[SimpleNamespace(message=msg, finish_reason=finish)],
                               usage=usage, system_fingerprint="fp", service_tier="default")

    for finish, refusal, cls in (("stop", None, None), ("length", None, "DECISION_TRUNCATED"),
                                 ("stop", "no", "REFUSAL")):
        class _C:
            def create(self, **kw):
                sent.append(kw)
                return reply(finish, refusal)
        monkeypatch.setattr(openai, "OpenAI", lambda **k: SimpleNamespace(chat=SimpleNamespace(completions=_C())))
        a = get_adapter("openai", "gpt-5.4-2026-03-05")
        a.handover_rules = True
        r = a.generate_decision("S", "U")
        assert r.error_class == cls if cls else r.success
        assert "reasoning_effort" not in sent[-1] and sent[-1]["max_completion_tokens"] == 16384
        assert "reasoning_effort_sent" not in r.metadata


def test_the_pipeline_sets_the_handover_rules_from_settings():
    src = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")
    assert "adapter.handover_rules = handover_rules_apply(settings, run_date)" in src
    assert 'getattr(adapter, "tick_rules", False) and tick_deadline_at is not None' in src


def test_agreement_counts_stay_inside_each_cohort(monkeypatch):
    from src import pipeline
    holdings = {"gpt": {"AAPL"}, "grok": {"AAPL"}, "cb_astra": {"AAPL"}, "cb_grok": set()}
    monkeypatch.setattr("src.portfolio.load_portfolio",
                        lambda k: SimpleNamespace(holdings={t: 1 for t in holdings.get(k, set())}))
    monkeypatch.setattr(pipeline, "active_model_keys", lambda s, on=None: list(SHIPPED["models"]))
    buy = [SimpleNamespace(executed=True, side="BUY", ticker="AAPL")]
    assert pipeline._compute_agreement_counts("cb_grok", buy, SHIPPED) == {"AAPL": 2}
    assert pipeline._compute_agreement_counts("claude", buy, SHIPPED) == {"AAPL": 3}
