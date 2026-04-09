"""VADER sentiment scoring for news headlines.

VADER is a rule-based sentiment analyzer designed for social media + short
text — it works well on news headlines without needing a fine-tuned model.
The `compound` score it returns is already normalized to [-1.0, +1.0]
which is exactly what the trading prompt expects.

Public API:
  - score_headline(text)            -> float
  - aggregate_sentiment(headlines)  -> float    (mean compound across a list)
  - compute_sentiment_dict(news)    -> dict     (per-ticker scores ready for prompt)

The NLTK VADER lexicon is auto-downloaded on first use if not already
installed in the local NLTK data path. Costs ~127KB once. Cached by NLTK.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("llmlab.sentiment")

_analyzer = None


def _get_analyzer():
    """Lazy-init the SentimentIntensityAnalyzer.

    The first call may trigger an NLTK lexicon download — about 127KB.
    We do it lazily so importing this module doesn't block the pipeline
    if NLTK is missing or the lexicon hasn't been fetched yet.
    """
    global _analyzer
    if _analyzer is not None:
        return _analyzer
    try:
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
    except ImportError as e:
        raise RuntimeError(
            "nltk is not installed. Add it to requirements.txt and pip install."
        ) from e

    try:
        _analyzer = SentimentIntensityAnalyzer()
    except LookupError:
        logger.info("VADER lexicon missing — downloading (one-time, ~127KB)")
        import nltk
        nltk.download("vader_lexicon", quiet=True)
        _analyzer = SentimentIntensityAnalyzer()
    return _analyzer


def score_headline(text: str) -> float:
    """Return the VADER compound score for a single headline string.

    Compound is the normalized aggregate of pos/neg/neu — strictly within
    the range [-1.0, +1.0]. Returns 0.0 for empty / unscoreable input.
    """
    if not text or not text.strip():
        return 0.0
    try:
        analyzer = _get_analyzer()
        return float(analyzer.polarity_scores(text).get("compound", 0.0))
    except Exception as e:
        logger.warning("Failed to score headline: %s", e)
        return 0.0


def aggregate_sentiment(headlines: list[dict[str, Any]] | list[str]) -> float:
    """Mean compound score across a list of headlines.

    Accepts either a list of {title: ...} dicts (the news.py output shape)
    or a plain list of strings. Returns 0.0 for an empty list.
    """
    if not headlines:
        return 0.0
    scores: list[float] = []
    for h in headlines:
        if isinstance(h, dict):
            text = h.get("title", "") or ""
        else:
            text = str(h)
        scores.append(score_headline(text))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def compute_sentiment_dict(news: dict[str, Any]) -> dict[str, float]:
    """Per-ticker aggregate sentiment dict ready for prompt injection.

    Walks the {ticker: [...], "macro": [...]} structure produced by
    news.fetch_news() and returns {ticker: compound_score, "macro": compound_score}.
    """
    if not news:
        return {}
    out: dict[str, float] = {}
    for key, headlines in news.items():
        out[key] = aggregate_sentiment(headlines)
    return out


def sentiment_label(score: float) -> str:
    """Human-readable bucket for a compound score.

    Buckets follow the canonical VADER cutoffs (compound ≥ 0.05 positive,
    ≤ -0.05 negative, otherwise neutral). Used by the prompt formatter to
    give models a quick categorical signal alongside the raw number.
    """
    if score >= 0.5:
        return "very positive"
    if score >= 0.05:
        return "positive"
    if score <= -0.5:
        return "very negative"
    if score <= -0.05:
        return "negative"
    return "neutral"
