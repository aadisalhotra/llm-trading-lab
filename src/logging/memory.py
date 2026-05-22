"""Rolling memory context for model prompts.

Each model should see its own recent BUY/SELL decisions before acting so
it can avoid redundant trades. This module provides two helpers:

  * read_recent_decisions(model_key, limit=10, now=None)
      Walks /data/trades/{model_key}_YYYY-MM.jsonl newest-first and pulls
      the last N executed BUY/SELL trades for that specific model. Each
      model only ever reads its own log — no cross-model leakage.

  * detect_memory_hit(decision_result)
      Scans the returned reasoning text for phrases that indicate the
      model is referencing a prior decision ("already positioned",
      "previously exited", etc). Used to track whether the rolling
      memory context is actually influencing decisions.

HOLDs are intentionally excluded from the recent-decisions stream. The
memory context exists to stop models from re-trading the same ticker; a
wall of "HOLD" rows would crowd out the real signal.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Iterable

from ..config_loader import TRADES_DIR

logger = logging.getLogger("llmlab.memory")


# Phrases that strongly indicate the model is citing prior activity rather
# than reasoning from fresh data. Matched case-insensitively against the
# overall_reasoning paragraph and every per-decision reasoning/summary.
# Intentionally narrow — we want a high-precision signal, not a kitchen-
# sink regex that fires on "already" alone.
_MEMORY_HIT_PATTERNS = [
    r"already (?:position|positioned|hold|holding|long|own|owned|bought|sold|trimmed|added|exited|in position|out of)",
    r"previously (?:bought|sold|exited|trimmed|added|positioned|held|entered)",
    r"recently (?:bought|sold|exited|trimmed|added|positioned|entered)",
    r"\b(?:prior|earlier|last|recent) (?:buy|sell|entry|exit|position|decision|trade|action|tick)\b",
    r"since (?:my |the )?(?:last|prior|recent) (?:buy|sell|trade|decision|entry|exit|tick)",
    r"from (?:my |the )?(?:last|prior|recent) (?:buy|sell|trade|decision|entry|exit)",
    r"\bjust (?:bought|sold|exited|trimmed|added)\b",
    r"per (?:my |the )?(?:last|prior|recent) (?:decision|trade|action)",
    r"avoid(?:ing)? (?:repeating|re-?trading|re-?buying|re-?selling|redundant)",
    r"no (?:new|fresh) catalyst since",
    r"same headline as (?:before|last|my prior)",
]
_MEMORY_HIT_RE = re.compile("|".join(_MEMORY_HIT_PATTERNS), re.IGNORECASE)


def read_recent_decisions(
    model_key: str,
    limit: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the last N executed BUY/SELL decisions for `model_key`, newest first.

    Each returned dict has:
        timestamp (str): execution timestamp (ISO)
        ticker    (str)
        action    (str): "BUY" or "SELL"
        shares    (float)
        confidence (int | None): from the underlying decision dict
        summary   (str): one-sentence decision summary

    Walks the monthly log files in reverse chronological order until it
    has `limit` entries or runs out of history. Failures reading any file
    are logged and swallowed — memory is best-effort, never blocking.
    """
    if limit <= 0:
        return []
    if not TRADES_DIR.exists():
        return []

    pattern = re.compile(rf"^{re.escape(model_key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
    candidates: list[tuple[int, int, Any]] = []
    for fp in TRADES_DIR.iterdir():
        if not fp.is_file():
            continue
        m = pattern.match(fp.name)
        if not m:
            continue
        candidates.append((int(m.group(1)), int(m.group(2)), fp))
    # Newest month first
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)

    out: list[dict[str, Any]] = []
    for _, _, fp in candidates:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("memory: failed to read %s: %s", fp, e)
            continue
        # Process newest record first (each file is append-only, newest at end)
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_ts = rec.get("timestamp", "")
            # Each record can contain multiple executions. Iterate them in
            # reverse so the last action in a tick comes first.
            for ex in reversed(rec.get("executions") or []):
                if not ex.get("executed"):
                    continue
                side = ex.get("side")
                if side not in ("BUY", "SELL"):
                    continue
                decision = ex.get("decision") or {}
                summary = (decision.get("summary") or decision.get("reasoning") or "").strip()
                if summary:
                    # First sentence-ish chunk, capped so the prompt stays compact
                    first = re.split(r"(?<=[.!?])\s", summary, maxsplit=1)[0]
                    summary = first[:200]
                try:
                    shares_val = float(ex.get("shares") or 0)
                except (TypeError, ValueError):
                    shares_val = 0.0
                conf = decision.get("confidence")
                out.append({
                    "timestamp": ex.get("timestamp") or rec_ts,
                    "ticker": ex.get("ticker", ""),
                    "action": side,
                    "shares": shares_val,
                    "confidence": conf if isinstance(conf, (int, float)) else None,
                    "summary": summary,
                })
                if len(out) >= limit:
                    return out
    return out


def read_last_action_per_ticker(
    model_key: str,
    tickers: Iterable[str],
    now: datetime | None = None,
    max_lookback_days: int = 60,
) -> dict[str, dict[str, Any]]:
    """Most-recent executed BUY/SELL per requested ticker, for this model only.

    Complements ``read_recent_decisions``: that returns a global, length-capped
    chronological feed (a recent-activity narrative); this returns a per-ticker
    lookup so a model can see its own last action on EACH ticker it is
    considering — including a ticker it sold to zero, which drops out of both
    the holdings table and the last-N window and is otherwise invisible.

    Walks ``/data/trades/{model_key}_YYYY-MM.jsonl`` newest-first and records the
    first (i.e. most recent) executed BUY/SELL it sees for each requested ticker.
    Bounded work: it early-exits as soon as every requested ticker is resolved,
    and stops once it crosses ``max_lookback_days`` before ``now`` (default 60).
    A ticker with no executed trade in that window is simply absent from the
    result — the caller renders that as "(no prior trade)".

    Returns ``{TICKER: {action, timestamp, date, shares, price, confidence}}``.
    Best-effort: read failures are logged and swallowed, never raised.
    """
    want = {str(t).upper().strip() for t in (tickers or []) if str(t).strip()}
    if not want or not TRADES_DIR.exists():
        return {}

    ref = now or datetime.utcnow()
    try:
        cutoff_str = (ref.date() - timedelta(days=max_lookback_days)).strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        cutoff_str = ""

    pattern = re.compile(rf"^{re.escape(model_key)}_(\d{{4}})-(\d{{2}})\.jsonl$")
    candidates: list[tuple[int, int, Any]] = []
    for fp in TRADES_DIR.iterdir():
        if not fp.is_file():
            continue
        m = pattern.match(fp.name)
        if m:
            candidates.append((int(m.group(1)), int(m.group(2)), fp))
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)  # newest month first

    result: dict[str, dict[str, Any]] = {}
    remaining = set(want)
    for _, _, fp in candidates:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("memory: failed to read %s: %s", fp, e)
            continue
        for line in reversed(lines):  # newest record first within the file
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_date = rec.get("date", "")
            # Logs are append-only chronological and we walk newest->oldest, so
            # the first record older than the lookback cutoff means everything
            # remaining is older too — stop entirely.
            if cutoff_str and rec_date and rec_date < cutoff_str:
                return result
            rec_ts = rec.get("timestamp", "")
            for ex in reversed(rec.get("executions") or []):
                if not ex.get("executed"):
                    continue
                side = ex.get("side")
                if side not in ("BUY", "SELL"):
                    continue
                t = str(ex.get("ticker", "")).upper().strip()
                if t not in remaining:
                    continue
                decision = ex.get("decision") or {}
                try:
                    shares_val = float(ex.get("shares") or 0)
                except (TypeError, ValueError):
                    shares_val = 0.0
                price = ex.get("fill_price")
                conf = decision.get("confidence")
                ts = ex.get("timestamp") or rec_ts
                result[t] = {
                    "action": side,
                    "timestamp": ts,
                    "date": rec_date or (str(ts)[:10] if ts else ""),
                    "shares": shares_val,
                    "price": float(price) if isinstance(price, (int, float)) else None,
                    "confidence": conf if isinstance(conf, (int, float)) else None,
                }
                remaining.discard(t)
                if not remaining:
                    return result  # every requested ticker resolved
    return result


def detect_memory_hit(decision_result: Any) -> bool:
    """Return True if the model's reasoning explicitly references a prior decision.

    Scans `overall_reasoning` plus every per-decision `reasoning` and
    `summary` field for phrases like "already positioned", "previously
    exited", "recent buy", etc. A rough proxy for whether the rolling
    recent-decisions context actually shaped the model's thinking on this
    tick. False positives are possible but rare given the narrow phrase
    list — good enough to average across many runs.
    """
    if decision_result is None:
        return False
    texts: list[str] = []
    overall = getattr(decision_result, "overall_reasoning", None) or ""
    if isinstance(overall, str) and overall:
        texts.append(overall)
    for d in getattr(decision_result, "decisions", None) or []:
        if not isinstance(d, dict):
            continue
        for k in ("reasoning", "summary"):
            v = d.get(k)
            if isinstance(v, str) and v:
                texts.append(v)
    if not texts:
        return False
    combined = "\n".join(texts)
    return bool(_MEMORY_HIT_RE.search(combined))
