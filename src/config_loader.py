"""Centralized config + path resolution.

Everything that needs to read config or write to data/ goes through here so
paths stay consistent regardless of where the entry point lives.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional — env vars may already be set in CI
    def load_dotenv(*_a, **_kw):  # type: ignore
        return False

# Project root = parent of /src
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
LOGS_DIR = PROJECT_ROOT / "logs"
REPORTS_DIR = PROJECT_ROOT / "reports"
MONTHLY_REPORTS_DIR = REPORTS_DIR / "monthly"

# Subdirs under data/
TRADES_DIR = DATA_DIR / "trades"
PERFORMANCE_DIR = DATA_DIR / "performance"
INTRADAY_DIR = DATA_DIR / "intraday"
LEADERBOARD_DIR = DATA_DIR / "leaderboard"
MODEL_VERSIONS_DIR = DATA_DIR / "model_versions"
STATE_DIR = DATA_DIR / "state"
NEWS_CACHE_DIR = DATA_DIR / "news_cache"
# Email-alert state + send log. Committed with the rest of data/ so the
# "fire once per threshold ever" milestone ledger survives across the
# ephemeral GitHub Actions runners.
ALERTS_DIR = DATA_DIR / "alerts"

_LOG_CONFIGURED = False


def ensure_dirs() -> None:
    """Create all expected directories. Safe to call repeatedly."""
    for d in (
        DATA_DIR,
        TRADES_DIR,
        PERFORMANCE_DIR,
        INTRADAY_DIR,
        LEADERBOARD_DIR,
        MODEL_VERSIONS_DIR,
        STATE_DIR,
        NEWS_CACHE_DIR,
        ALERTS_DIR,
        LOGS_DIR,
        REPORTS_DIR,
        MONTHLY_REPORTS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    """Load .env if present. No-op if missing — env vars may already be set in CI."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def load_settings() -> dict[str, Any]:
    with open(CONFIG_DIR / "settings.json", "r", encoding="utf-8") as f:
        return json.load(f)


class PendingCapitalError(RuntimeError):
    """A book's starting capital has not been confirmed for this mode.

    Deliberately fatal. The alternative — a silent fallback to some default —
    is the failure class that produced the builder's hard-coded regime labels:
    a stale default that reads as a real value and is only caught by someone
    noticing the number is wrong. October's validation books must size at the
    scale Phase B actually runs, so an unconfirmed figure has to stop the run
    rather than quietly seed six books at the wrong capital.
    """


def starting_capital(settings: dict[str, Any] | None = None,
                     mode: str | None = None) -> float:
    """Per-book starting capital for `mode`, or raise if it is unconfirmed.

    Every consumer of `starting_capital` routes through here so the
    pending-confirmation guard cannot be bypassed by one call site that still
    does its own `.get(mode, 100_000.0)`.
    """
    settings = settings if settings is not None else load_settings()
    mode = mode or settings.get("mode", "paper")
    caps = settings.get("starting_capital", {}) or {}
    pending = set(caps.get("_pending_confirmation") or [])
    value = caps.get(mode)
    if mode in pending or value is None:
        raise PendingCapitalError(
            f"starting_capital for mode {mode!r} is PENDING CAPITAL CONFIRMATION. "
            f"The registered per-book figure has not been confirmed by the PI, "
            f"and books must not incept at an unconfirmed scale. Set "
            f"config/settings.json -> starting_capital.{mode} and remove {mode!r} "
            f"from _pending_confirmation once the hub confirms the number.")
    return float(value)


class PendingCohortError(RuntimeError):
    """The live cohort's composition has not been confirmed.

    Sibling of `PendingCapitalError`, and fatal for the same reason.

    History, because it is the argument for keeping this guard rather than
    retiring it now that a cohort is named. The 2026-09-11 ruling fixed the
    live cohort at FIVE books without naming them, and five was not derivable
    from tracked content: `config/settings.json` enables six models and the
    registered account structure in `docs/prereg/tier2_novel_sections.md` is a
    six-book structure throughout. The obvious derivation — "the five
    core-cohort books" — would have been WRONG: the 2026-09-14 ruling excludes
    `deepseek` and funds `claude_opus`, which is the expansion book. An
    invented default would have read as a confirmed registration decision and
    been wrong on the merits, which is exactly the failure shape this raises
    to prevent.
    """


def reserve_master(settings: dict[str, Any] | None = None) -> float:
    """Master-level USD reserve, held for fees and settlement rounding.

    **This is ADDITIONAL to the registered capital base and must never enter a
    return denominator.** Five books x $2,000 is the registered base of
    $10,000; the $500 reserve brings committed exposure to $10,500, and a
    return computed on $10,500 would be wrong. `registered_capital_base()`
    deliberately does not call this function — that omission is the invariant,
    not an oversight.
    """
    settings = settings if settings is not None else load_settings()
    structure = settings.get("capital_structure", {}) or {}
    return float(structure.get("reserve_master_usd", 0.0))


def live_cohort_book_count(settings: dict[str, Any] | None = None) -> int:
    """Number of books funded in the live cohort (hub ruling 2026-09-11: five).

    The count is confirmed; the composition is not — see `live_cohort_keys`.
    """
    settings = settings if settings is not None else load_settings()
    structure = settings.get("capital_structure", {}) or {}
    count = structure.get("live_cohort_book_count")
    if count is None:
        raise PendingCohortError(
            "capital_structure.live_cohort_book_count is not set; the registered "
            "capital base cannot be computed without it.")
    return int(count)


def live_cohort_keys(settings: dict[str, Any] | None = None) -> list[str]:
    """The model keys funded in the live cohort, or raise if unconfirmed.

    Raises `PendingCohortError` while `capital_structure.live_cohort_keys` is
    null, and also if a named list disagrees with the registered count — a
    composition that does not match the number the capital base was computed
    from is a reconciliation failure, not a rounding detail.
    """
    settings = settings if settings is not None else load_settings()
    structure = settings.get("capital_structure", {}) or {}
    keys = structure.get("live_cohort_keys")
    if not keys:
        raise PendingCohortError(
            "capital_structure.live_cohort_keys is PENDING COHORT CONFIRMATION. "
            f"The registered book count is {live_cohort_book_count(settings)}, but the "
            "books are not named, and the composition is not derivable from the "
            "`cohort` tags in settings.json -- the ruled Phase B cohort is not the "
            "core cohort. Name them in config/settings.json -> "
            "capital_structure.live_cohort_keys.")
    keys = list(keys)
    expected = live_cohort_book_count(settings)
    if len(keys) != expected:
        raise PendingCohortError(
            f"capital_structure.live_cohort_keys names {len(keys)} books but "
            f"live_cohort_book_count is {expected}; the registered capital base is "
            "computed from the count, so the two must agree.")
    unknown = [k for k in keys if k not in (settings.get("models") or {})]
    if unknown:
        raise PendingCohortError(
            f"capital_structure.live_cohort_keys names unknown model keys: {unknown}")
    return keys


def registered_capital_base(settings: dict[str, Any] | None = None,
                            mode: str | None = None) -> float:
    """Total registered capital for `mode` — the ONLY valid return denominator.

    `live_cohort_book_count` x `starting_capital(mode)`. For Phase B live that
    is 5 x $2,000 = $10,000. `reserve_master()` is excluded by construction.
    """
    settings = settings if settings is not None else load_settings()
    return live_cohort_book_count(settings) * starting_capital(settings, mode)


def load_universe() -> dict[str, Any]:
    with open(CONFIG_DIR / "universe.json", "r", encoding="utf-8") as f:
        return json.load(f)


def universe_symbols() -> list[str]:
    return [t["symbol"] for t in load_universe()["tickers"]]


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Required env var {name} is not set")
    return val


def force_utf8_console() -> None:
    """Force stdout/stderr to UTF-8 so console logs render Unicode glyphs.

    On Windows, when stdout/stderr aren't an interactive console — piped,
    captured by CI, or redirected to a file — Python falls back to the locale
    code page (typically cp1252), which can't encode the em-dashes, arrows, and
    middots we log; they get mangled to '?' or '\\uXXXX'. We reconfigure the
    existing stream objects in place, so any logging handler already holding a
    reference to sys.stderr keeps working with the new encoding. Idempotent, and
    a no-op on streams that don't support reconfigure (None under pythonw, a
    captured StringIO in tests, an already-detached stream).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # Stream closed or otherwise unreconfigurable — leave it as-is.
            pass


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logger once. Returns the project logger."""
    global _LOG_CONFIGURED
    if not _LOG_CONFIGURED:
        force_utf8_console()  # before the StreamHandler grabs sys.stderr
        ensure_dirs()
        log_file = LOGS_DIR / "pipeline.log"
        handlers = [
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ]
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=handlers,
        )
        _LOG_CONFIGURED = True
    return logging.getLogger("llmlab")
