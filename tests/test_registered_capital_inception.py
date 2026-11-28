"""Plumbing (f): internal-paper books that incept at the registered Phase B book
capital ($2,000), not the $100,000 paper figure (hub H-OPS-37 Batch 4 (c)).

cohort_refresh_ruling.md section 3 item 8 (refreshed arms' shakedown books from
2026-12-01) and section 4 item 4 (the snapshots' fresh internal-paper books at
the 2026-12-05/06 handover) both say "at the registered Phase B book capital".
Before this, init_portfolio() took starting_capital(effective_mode), which is
$100,000 for "paper".
"""
import json

import pytest

from src import config_loader
from src.analytics import performance
from src.portfolio import portfolio as portfolio_mod

CAPS = {"_pending_confirmation": [], "paper": 100000, "broker_paper": 2000, "live": 2000}


def _settings(**models):
    return {"mode": "broker_paper", "experiment_start_date": "2026-04-09", "starting_capital": dict(CAPS),
            "capital_structure": {"live_cohort_keys": ["claude"], "live_cohort_book_count": 1},
            "models": {"claude": {"enabled": True}, "deepseek": {"enabled": True}, **models}}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(portfolio_mod, "STATE_DIR", state)
    monkeypatch.setattr("src.data.market_data.inception_session_date", lambda reference=None: "2026-12-01")

    def use(settings):
        monkeypatch.setattr(portfolio_mod, "load_settings", lambda: settings)
        monkeypatch.setattr(config_loader, "load_settings", lambda: settings)
        monkeypatch.setattr(performance, "load_settings", lambda: settings)
        return state
    return use


def test_the_registered_capital_is_the_broker_paper_figure():
    assert config_loader.registered_book_capital(_settings()) == 2000.0


def test_without_the_key_every_book_incepts_as_before():
    s = _settings()
    assert config_loader.book_inception_capital("deepseek", s) == 100000.0   # paper
    assert config_loader.book_inception_capital("claude", s) == 2000.0       # venue cohort


def test_an_internal_paper_book_with_the_key_incepts_at_2000(lab):
    s = _settings(cb_sonnet={"enabled": True, "inception_capital": "registered_book_capital"})
    state = lab(s)
    assert config_loader.effective_mode("cb_sonnet", s) == "paper"
    p = portfolio_mod.init_portfolio("cb_sonnet")
    assert (p.cash, p.inception_value, p.inception_epoch, p.inception_date) == (2000.0, 2000.0, "paper", "2026-12-01")
    on_disk = json.loads((state / "cb_sonnet.json").read_text(encoding="utf-8"))
    assert on_disk["inception_value"] == 2000.0 and on_disk["cash"] == 2000.0


def test_deepseek_s_paper_book_is_unchanged(lab):
    lab(_settings())
    p = portfolio_mod.init_portfolio("deepseek")
    assert (p.cash, p.inception_value, p.inception_epoch) == (100000.0, 100000.0, "paper")


def test_a_snapshot_moved_off_the_venue_reincepts_at_2000(lab):
    """The handover: the snapshot leaves the live cohort (its effective mode
    becomes paper) and its fresh internal-paper book is $2,000, not $100,000."""
    s = _settings(gpt={"enabled": True, "inception_capital": "registered_book_capital"})
    lab(s)
    assert config_loader.effective_mode("gpt", s) == "paper"
    assert portfolio_mod.init_portfolio("gpt").inception_value == 2000.0


def test_an_unknown_basis_raises_instead_of_falling_back():
    s = _settings(cb_opus={"enabled": True, "inception_capital": "registered"})
    with pytest.raises(ValueError, match="inception_capital"):
        config_loader.book_inception_capital("cb_opus", s)


def test_a_pending_registered_capital_still_raises():
    s = _settings(cb_opus={"enabled": True, "inception_capital": "registered_book_capital"})
    s["starting_capital"]["_pending_confirmation"] = ["broker_paper"]
    with pytest.raises(config_loader.PendingCapitalError):
        config_loader.book_inception_capital("cb_opus", s)


def test_an_unincepted_registered_book_is_measured_against_2000(lab):
    s = _settings(cb_argon={"enabled": True, "inception_capital": "registered_book_capital"})
    lab(s)
    assert performance.book_epoch("cb_argon", s) == ("2026-04-09", 2000.0)
    assert performance.book_epoch("deepseek", s) == ("2026-04-09", 100000.0)


def test_an_incepted_book_reads_its_own_state_whatever_the_key_says(lab):
    """Return denominators come from the book's own state file, as before."""
    s = _settings(cb_argon={"enabled": True, "inception_capital": "registered_book_capital"})
    state = lab(s)
    (state / "cb_argon.json").write_text(json.dumps({"inception_date": "2026-12-01",
                                                     "inception_value": 2000.0}), encoding="utf-8")
    assert performance.book_epoch("cb_argon", s) == ("2026-12-01", 2000.0)


def test_shipped_settings_set_the_key_on_the_ten_cohort_books_only():
    """Plumbing (a)/(c) sets the key on the five snapshots (their fresh
    internal-paper books at the handover) and the five refreshed arms (their
    shakedown and venue books). DeepSeek has none: its book stays $100,000.
    A book already incepted reads its own state file, so no running book's
    capital moves."""
    shipped = config_loader.load_settings()
    keyed = sorted(k for k, v in shipped["models"].items() if "inception_capital" in (v or {}))
    assert keyed == sorted(["claude", "gpt", "gemini", "grok", "claude_opus",
                            "cb_sonnet", "cb_opus", "cb_astra", "cb_grok", "cb_argon"])
    assert all(shipped["models"][k]["inception_capital"] == "registered_book_capital" for k in keyed)
