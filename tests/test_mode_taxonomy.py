"""P0 — mode taxonomy, fail-closed endpoint binding, capital guard, and the
inception-epoch boundary.

Three ratified requirements are enforced here rather than documented:

  * A `broker_paper` run must be structurally unable to reach the live endpoint
    or authenticate with live keys, and vice versa. The base URL is derived
    from the mode in code and never from `ALPACA_BASE_URL`, so a stale secret
    cannot point a paper run at production.
  * Per-book starting capital for an unconfirmed mode must stop the run, not
    fall back to a default. October must not incept at the wrong scale.
  * Neither phase boundary carries book state. A leftover state file is the
    silent way that ruling gets defeated, so loading one is a hard error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config_loader
from src.config_loader import PendingCapitalError, starting_capital
from src.execution.broker import (
    BROKER_MODES,
    MODE_BROKER_PAPER,
    MODE_LIVE,
    MODE_SIMULATOR,
    BrokerClient,
    BrokerConfigError,
)
from src.portfolio import portfolio as pf_mod
from src.portfolio.portfolio import InceptionEpochError, Portfolio, load_portfolio, save_portfolio

REPO = Path(__file__).resolve().parent.parent


def _clear_alpaca_env(monkeypatch):
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL",
                "ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET",
                "ALPACA_LIVE_KEY", "ALPACA_LIVE_SECRET"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------
# Mode taxonomy
# --------------------------------------------------------------------------
def test_three_modes_and_only_the_broker_pair_reaches_a_venue():
    assert BROKER_MODES == (MODE_BROKER_PAPER, MODE_LIVE)
    assert MODE_SIMULATOR not in BROKER_MODES


def test_simulator_mode_cannot_construct_a_broker_client():
    with pytest.raises(BrokerConfigError, match="reaches no venue"):
        BrokerClient(MODE_SIMULATOR)


def test_committed_settings_still_run_the_simulator():
    # P0 adds the capability; it does not flip the running mode. Phase A must
    # keep running on the simulator until the Oct 1 cutover.
    settings = json.load(open(REPO / "config" / "settings.json", encoding="utf-8"))
    assert settings["mode"] == MODE_SIMULATOR


# --------------------------------------------------------------------------
# Fail-closed endpoint binding
# --------------------------------------------------------------------------
def test_broker_paper_binds_to_the_paper_endpoint(monkeypatch):
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "secret")
    c = BrokerClient(MODE_BROKER_PAPER)
    assert c.base_url == "https://paper-api.alpaca.markets"


def test_live_binds_to_the_live_endpoint(monkeypatch):
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_LIVE_KEY", "AKTEST")
    monkeypatch.setenv("ALPACA_LIVE_SECRET", "secret")
    c = BrokerClient(MODE_LIVE)
    assert c.base_url == "https://api.alpaca.markets"


def test_live_mode_refuses_to_authenticate_with_paper_or_legacy_keys(monkeypatch):
    # The substitution that fail-closed binding exists to prevent: the legacy
    # committed pair is a PAPER credential and must never serve a live run.
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_API_KEY", "PKLEGACY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "secret")
    with pytest.raises(BrokerConfigError, match="ALPACA_LIVE_KEY"):
        BrokerClient(MODE_LIVE)


def test_legacy_keys_still_serve_broker_paper(monkeypatch):
    # The keys already wired into CI are paper keys; broker_paper accepts them
    # so the credential split can land without breaking the cutover.
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_API_KEY", "PKLEGACY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    c = BrokerClient(MODE_BROKER_PAPER)
    assert c.base_url == "https://paper-api.alpaca.markets"


def test_a_wrong_base_url_secret_cannot_redirect_the_endpoint(monkeypatch):
    # The exact live-fire hazard: a stale ALPACA_BASE_URL pointing a paper run
    # at production. The env value is ignored by construction.
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    c = BrokerClient(MODE_BROKER_PAPER)
    assert c.base_url == "https://paper-api.alpaca.markets"


def test_missing_credentials_fail_loudly(monkeypatch):
    _clear_alpaca_env(monkeypatch)
    with pytest.raises(BrokerConfigError, match="No credentials"):
        BrokerClient(MODE_BROKER_PAPER)


# --------------------------------------------------------------------------
# Capital guard
# --------------------------------------------------------------------------
def test_simulator_capital_still_resolves():
    settings = json.load(open(REPO / "config" / "settings.json", encoding="utf-8"))
    assert starting_capital(settings, "paper") == 100_000.0


def test_pending_modes_raise_rather_than_defaulting():
    settings = json.load(open(REPO / "config" / "settings.json", encoding="utf-8"))
    for mode in ("broker_paper", "live"):
        with pytest.raises(PendingCapitalError, match="PENDING CAPITAL CONFIRMATION"):
            starting_capital(settings, mode)


def test_the_stale_1000_dollar_live_figure_is_gone():
    settings = json.load(open(REPO / "config" / "settings.json", encoding="utf-8"))
    assert settings["starting_capital"].get("live") is None
    assert set(settings["starting_capital"]["_pending_confirmation"]) == {
        "broker_paper", "live"}


def test_confirming_a_value_clears_the_guard():
    settings = {"mode": "broker_paper",
                "starting_capital": {"_pending_confirmation": [],
                                     "broker_paper": 4000}}
    assert starting_capital(settings, "broker_paper") == 4000.0


def test_a_value_present_but_still_listed_pending_is_refused():
    # Belt and braces: the marker wins over a value someone dropped in early.
    settings = {"mode": "live",
                "starting_capital": {"_pending_confirmation": ["live"], "live": 4000}}
    with pytest.raises(PendingCapitalError):
        starting_capital(settings, "live")


# --------------------------------------------------------------------------
# Inception epoch — nothing carries across a boundary
# --------------------------------------------------------------------------
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pf_mod, "STATE_DIR", tmp_path)
    return tmp_path


def test_epoch_is_stamped_on_save_and_survives_a_round_trip(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch)
    p = Portfolio(model_key="grok", cash=100.0, inception_epoch="paper")
    save_portfolio(p)
    stored = json.load(open(tmp_path / "grok.json", encoding="utf-8"))
    assert stored["inception_epoch"] == "paper"
    assert load_portfolio("grok").inception_epoch == "paper"


def test_a_phase_a_state_file_is_refused_by_a_broker_paper_run(tmp_path, monkeypatch):
    # The Oct 1 boundary. A carried $100,000 simulator book would load cleanly
    # and quietly stand in for a freshly incepted validation book.
    _state_dir(tmp_path, monkeypatch)
    save_portfolio(Portfolio(model_key="grok", cash=100_000.0, inception_epoch="paper"))
    monkeypatch.setattr(pf_mod, "load_settings", lambda: {"mode": "broker_paper"})
    with pytest.raises(InceptionEpochError, match="do not carry across a phase boundary"):
        load_portfolio("grok")


def test_an_october_state_file_is_refused_by_a_live_run(tmp_path, monkeypatch):
    # The Nov 1 boundary. Confirmatory books incept from broker-authoritative
    # funded reality — no positions, no P&L, no October state.
    _state_dir(tmp_path, monkeypatch)
    save_portfolio(Portfolio(model_key="grok", cash=4_000.0,
                             inception_epoch="broker_paper"))
    monkeypatch.setattr(pf_mod, "load_settings", lambda: {"mode": "live"})
    with pytest.raises(InceptionEpochError):
        load_portfolio("grok")


def test_a_legacy_state_file_without_the_field_reads_as_the_simulator_era(tmp_path, monkeypatch):
    # Every state file written before this build is a Phase A simulator book,
    # so the absent field must mean "paper" and keep Phase A running.
    _state_dir(tmp_path, monkeypatch)
    (tmp_path / "grok.json").write_text(json.dumps({
        "model_key": "grok", "cash": 100_000.0, "holdings": {},
        "inception_value": 100_000.0, "inception_date": "2026-04-09",
    }), encoding="utf-8")
    monkeypatch.setattr(pf_mod, "load_settings", lambda: {"mode": "paper"})
    p = load_portfolio("grok")
    assert p.inception_epoch == "paper"
    assert p.cash == 100_000.0


def test_matching_epoch_loads_normally(tmp_path, monkeypatch):
    _state_dir(tmp_path, monkeypatch)
    save_portfolio(Portfolio(model_key="grok", cash=4_000.0,
                             inception_epoch="broker_paper"))
    monkeypatch.setattr(pf_mod, "load_settings", lambda: {"mode": "broker_paper"})
    assert load_portfolio("grok").cash == 4_000.0
