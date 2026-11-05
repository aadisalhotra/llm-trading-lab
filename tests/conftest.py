"""Suite-wide isolation.

Reporting reads each book's own state file for its epoch anchor. Without this, a
test that seeds a temporary performance log under a real model key would be
measured against whatever data/state/<key>.json holds that day, so the suite
would pass on Thursday and fail on Friday when the cutover books appear.
"""
import pytest

from src.portfolio import portfolio as portfolio_mod


@pytest.fixture(autouse=True)
def _no_real_book_state(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_mod, "STATE_DIR", tmp_path / "isolated_state")
