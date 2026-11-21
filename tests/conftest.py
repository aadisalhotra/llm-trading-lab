"""Suite-wide isolation from the things production changes under the tests.

STATE_DIR. Reporting reads each book's own state file for its epoch anchor.
Without this, a test that seeds a temporary performance log under a real model
key would be measured against whatever data/state/<key>.json holds that day, so
the suite would pass on Thursday and fail on Friday when the cutover books appear.

DAILY CACHE. The prompt frame (src/data/prompt_frame.py) caches daily bars
under data/market/daily/ and fetches them from yfinance on a miss. The suite
never writes there and never fetches: the cache goes to tmp_path and the fetch
raises, so a test reaching it without its own stub sees the fallback path.

mode. Most of this suite tests simulator logic and builds Executor() from the
committed settings. Once the committed mode is broker_paper, those tests stop
testing what they say: they demand Alpaca credentials or trip the epoch guard.
So the suite's default is the simulator, whatever is committed. A test of a venue
mode patches load_settings itself, which overrides this, and every venue test
already does. test_mode_taxonomy pins the committed value by reading the file.
"""
import sys

import pytest

from src import config_loader
from src.data import prompt_frame as prompt_frame_mod
from src.portfolio import portfolio as portfolio_mod

_real_load_settings = config_loader.load_settings


def _simulator_settings(*args, **kwargs):
    settings = _real_load_settings(*args, **kwargs)
    settings["mode"] = "paper"
    return settings


@pytest.fixture(autouse=True)
def _isolate_from_production_state(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "STATE_DIR", tmp_path / "isolated_state")
    monkeypatch.setattr(prompt_frame_mod, "DAILY_CACHE_DIR", tmp_path / "isolated_daily")

    def _no_daily_fetch(*a, **k):
        raise prompt_frame_mod.DailyFrameError("the test suite never fetches daily bars")
    monkeypatch.setattr(prompt_frame_mod, "fetch_daily_bars", _no_daily_fetch)
    # Modules bind load_settings by name at import, so each binding is patched.
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith(("src", "scripts", "test")) \
                and getattr(module, "load_settings", None) is _real_load_settings:
            monkeypatch.setattr(module, "load_settings", _simulator_settings)
