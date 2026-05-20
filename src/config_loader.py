"""Centralized config + path resolution.

Everything that needs to read config or write to data/ goes through here so
paths stay consistent regardless of where the entry point lives.
"""
from __future__ import annotations

import json
import logging
import os
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


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root logger once. Returns the project logger."""
    global _LOG_CONFIGURED
    if not _LOG_CONFIGURED:
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
