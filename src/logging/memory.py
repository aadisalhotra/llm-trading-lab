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
from datetime import datetime
from typing import Any

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
