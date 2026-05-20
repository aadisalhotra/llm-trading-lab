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
_macro_analyzer = None

# Crisis/finance lexicon augmentation used ONLY by score_macro_headline()
# (the trigger #11 market-event gate). VADER's stock lexicon is tuned for
# social media and is domain-blind: it scores "market crash", "sovereign
# default", and "recession" at or near zero, so a plain |compound| > 0.6 gate
# would almost never fire on the very headlines a market-event alert exists to
# catch. We therefore score those candidates with a SEPARATE analyzer whose
# lexicon is augmented with the terms below. This is deliberately kept off the
# shared score_headline() path so the sentiment the trading models see in
# their prompts is completely unchanged. Values follow VADER's own scale
# (roughly -4..+4); they only ever apply to headlines that have already
# matched a high-severity keyword category, so a strongly-negative valence
# here can't create false positives on unrelated news.
_MACRO_CRISIS_LEXICON: dict[str, float] = {
    # market action
    "crash": -3.4, "crashes": -3.4, "crashed": -3.4, "crashing": -3.4,
    "plunge": -2.8, "plunges": -2.8, "plunged": -2.8, "plunging": -2.8,
    "plummet": -3.0, "plummets": -3.0, "plummeted": -3.0,
    "collapse": -3.0, "collapses": -3.0, "collapsed": -3.0,
    "meltdown": -3.0, "rout": -2.6, "selloff": -2.4, "sell-off": -2.4,
    "tumble": -2.2, "tumbles": -2.2, "tumbled": -2.2,
    "nosedive": -2.8, "freefall": -2.8,
    "halts": -1.4, "halted": -1.4, "circuit": -1.6, "breaker": -1.6,
    # macro / credit
    "recession": -3.8, "depression": -3.4, "stagflation": -3.4,
    "contraction": -2.2, "contracts": -2.0,
    "default": -3.2, "defaults": -3.2, "defaulted": -3.2, "sovereign": -1.6,
    "downgrade": -2.8, "downgrades": -2.8, "downgraded": -2.8,
    "crisis": -3.1, "emergency": -2.6, "devaluation": -2.6, "embargo": -2.2,
    "inflation": -1.0, "surge": -1.6, "surges": -1.6, "surging": -1.6,
    "spike": -1.8, "spikes": -1.8, "soar": -1.5, "soars": -1.5, "soaring": -1.5,
    "shock": -2.0, "shocks": -2.0,
    # systemic / health
    "pandemic": -3.4, "epidemic": -3.2, "outbreak": -2.2,
    # geopolitical
    "invasion": -3.4, "invade": -3.2, "invades": -3.2, "invaded": -3.2,
    "war": -3.0, "warfare": -3.0, "missile": -2.8, "missiles": -2.8,
    "airstrike": -2.8, "airstrikes": -2.8, "terrorist": -3.4,
    "terrorism": -3.4, "terror": -3.0, "coup": -3.0,
}


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


def _get_macro_analyzer():
    """Lazy-init a SECOND VADER analyzer augmented with the crisis lexicon.

    Built from a fresh SentimentIntensityAnalyzer and then `lexicon.update`-d,
    so the shared analyzer used by score_headline() (and therefore the trading
    prompts) is never mutated.
    """
    global _macro_analyzer
    if _macro_analyzer is not None:
        return _macro_analyzer
    try:
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
    except ImportError as e:
        raise RuntimeError(
            "nltk is not installed. Add it to requirements.txt and pip install."
        ) from e
    try:
        analyzer = SentimentIntensityAnalyzer()
    except LookupError:
        logger.info("VADER lexicon missing — downloading (one-time, ~127KB)")
        import nltk
        nltk.download("vader_lexicon", quiet=True)
        analyzer = SentimentIntensityAnalyzer()
    analyzer.lexicon.update(_MACRO_CRISIS_LEXICON)
    _macro_analyzer = analyzer
    return _macro_analyzer


def score_macro_headline(text: str) -> float:
    """VADER compound score for a macro headline, using the crisis-augmented
    lexicon. Used only by the trigger #11 market-event gate — see
    _MACRO_CRISIS_LEXICON for why. Strictly within [-1.0, +1.0]; returns 0.0
    for empty / unscoreable input and degrades to the plain score on error."""
    if not text or not text.strip():
        return 0.0
    try:
        analyzer = _get_macro_analyzer()
        return float(analyzer.polarity_scores(text).get("compound", 0.0))
    except Exception as e:
        logger.warning("Failed to score macro headline: %s", e)
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
