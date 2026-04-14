"""News intelligence layer.

Pulls per-stock + macro headlines before each pipeline tick so the trading
prompt has fundamental context, not just technicals. The function the rest
of the pipeline calls is `fetch_news(symbols, settings)` — it returns a
clean dict ready to inject into the prompt:

    {
        "AAPL": [
            {"title": "...", "source": "Reuters", "datetime": "2026-04-09T13:30:00Z", "url": "..."},
            ...
        ],
        ...
        "macro": [
            {"title": "Fed minutes signal...", "source": "WSJ", "datetime": "...", "url": "..."},
            ...
        ],
    }

Provider chain (first one with a key + a non-empty response wins):
  1. Finnhub        — primary, 60 calls/min on free tier
  2. Alpha Vantage  — fallback, 25 calls/day on free tier
  3. NewsAPI        — fallback, 100 calls/day on free tier

Caching is on by default with a 60-minute TTL because:
  - News doesn't change every 30 minutes
  - Free-tier rate limits would otherwise be a real concern at 13 ticks/day
  - The cache file is JSON in /data/news_cache/cache.json — single file, no
    per-ticker fragmentation, atomic write on refresh

Failures are non-fatal: if every provider errors and there's no usable cache,
fetch_news returns an empty dict and the pipeline logs the gap. Models trade
as if no news context exists, exactly like the pre-news pipeline.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

from ..config_loader import NEWS_CACHE_DIR, load_settings, universe_symbols

logger = logging.getLogger("llmlab.news")

CACHE_FILE = NEWS_CACHE_DIR / "cache.json"
MACRO_TOP_CACHE_FILE = NEWS_CACHE_DIR / "macro_top.json"
HTTP_TIMEOUT = 10  # seconds — keep tight; news is best-effort

# ---- Public API ----------------------------------------------------------

def fetch_news(
    symbols: Iterable[str] | None = None,
    settings: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return per-stock + macro headlines for the active universe.

    Reads from the on-disk cache if it's still inside the configured TTL.
    Otherwise calls the provider chain, writes the cache, and returns.
    """
    if settings is None:
        settings = load_settings()
    news_cfg = settings.get("news", {}) or {}
    if not news_cfg.get("enabled", True):
        logger.info("News fetch disabled in settings — skipping")
        return {}

    if symbols is None:
        symbols = universe_symbols()
    symbols = list(symbols)

    ttl_minutes = int(news_cfg.get("cache_ttl_minutes", 60))
    headlines_per_stock = int(news_cfg.get("headlines_per_stock", 3))
    macro_count = int(news_cfg.get("macro_headlines", 5))

    # Try cache first
    if not force_refresh:
        cached = _load_cache(ttl_minutes, symbols)
        if cached is not None:
            logger.info("Using cached news (age: %ds, %d stocks, %d macro)",
                        cached["_age_seconds"], len(cached.get("stocks", {})),
                        len(cached.get("macro", [])))
            return _flatten_cache(cached, headlines_per_stock, macro_count)

    # Cache miss / expired — refresh from providers
    logger.info("Fetching fresh news for %d symbols (cache miss or expired)", len(symbols))
    fresh = _fetch_from_providers(symbols, headlines_per_stock, macro_count)

    if fresh.get("stocks") or fresh.get("macro"):
        _write_cache(fresh, ttl_minutes)
    else:
        logger.warning("All news providers returned empty / failed — pipeline will run without headlines")

    return _flatten_cache(fresh, headlines_per_stock, macro_count)


def fetch_top_macro_headlines(
    settings: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Return macro headlines for the Market Brief banner.

    NewsAPI is the primary source — its `top-headlines?category=business`
    endpoint pulls from CNN, Reuters, CNBC, Bloomberg, etc., which gives
    much better macro coverage than Finnhub's general feed for the
    Welcome banner specifically.

    Cached separately from `fetch_news()` at /data/news_cache/macro_top.json
    on the same hourly TTL so we stay well under NewsAPI's 100/day free
    tier (24 calls/day per the default 60-minute TTL).

    Falls back to whatever Finnhub macro is sitting in the main news cache
    if NewsAPI is unavailable, unkeyed, or returns empty.
    """
    if settings is None:
        settings = load_settings()
    news_cfg = settings.get("news", {}) or {}
    ttl_minutes = int(news_cfg.get("cache_ttl_minutes", 60))
    macro_count = int(news_cfg.get("macro_headlines", 5))

    if not force_refresh:
        cached = _load_macro_top_cache(ttl_minutes)
        if cached is not None:
            logger.info("Using cached macro headlines (age: %ds, provider: %s, n=%d)",
                        cached["_age_seconds"], cached.get("provider", "?"),
                        len(cached.get("macro", [])))
            return list(cached.get("macro", []))[:macro_count]

    # --- Primary: NewsAPI ---
    api_key = os.getenv("NEWS_API_KEY", "").strip()
    if api_key:
        try:
            macro = _fetch_newsapi_top_business(api_key, macro_count)
            if macro:
                _write_macro_top_cache(macro, ttl_minutes, "newsapi")
                logger.info("Fetched %d top macro headlines from NewsAPI", len(macro))
                return macro
            logger.info("NewsAPI returned no macro headlines — falling back to Finnhub cache")
        except requests.RequestException as e:
            logger.warning("NewsAPI macro fetch failed: %s — falling back to Finnhub cache", e)

    # --- Fallback: Finnhub macro from the main news cache ---
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                main = json.load(f)
            macro = list(main.get("macro", []) or [])[:macro_count]
            if macro:
                _write_macro_top_cache(macro, ttl_minutes, "finnhub_fallback")
                logger.info("Using %d Finnhub macro headlines as NewsAPI fallback", len(macro))
                return macro
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read main news cache for macro fallback")

    return []


def _fetch_newsapi_top_business(api_key: str, count: int) -> list[dict[str, Any]]:
    """Pull top US business headlines from NewsAPI's top-headlines endpoint."""
    r = requests.get(
        "https://newsapi.org/v2/top-headlines",
        params={"category": "business", "country": "us", "pageSize": max(count, 5)},
        headers={"X-Api-Key": api_key},
        timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        logger.warning("NewsAPI top-headlines HTTP %d: %s", r.status_code, r.text[:200])
        return []
    articles = (r.json() or {}).get("articles", []) or []
    out: list[dict[str, Any]] = []
    for a in articles:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "source": (a.get("source") or {}).get("name", "NewsAPI"),
            "datetime": a.get("publishedAt", ""),
            "url": a.get("url", ""),
            "summary": (a.get("description") or "")[:300],
        })
        if len(out) >= count:
            break
    return out


def _load_macro_top_cache(ttl_minutes: int) -> dict[str, Any] | None:
    """Read the dedicated macro-headline cache if it's still inside its TTL."""
    if not MACRO_TOP_CACHE_FILE.exists():
        return None
    try:
        with open(MACRO_TOP_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read macro_top cache — treating as miss")
        return None
    fetched_at = payload.get("fetched_at", "")
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > ttl_minutes * 60:
        return None
    payload["_age_seconds"] = int(age)
    return payload


def _write_macro_top_cache(macro: list[dict[str, Any]], ttl_minutes: int,
                           provider: str) -> None:
    NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "ttl_minutes": ttl_minutes,
        "provider": provider,
        "macro": macro,
    }
    tmp = MACRO_TOP_CACHE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(MACRO_TOP_CACHE_FILE)


# ---- Cache layer ---------------------------------------------------------

def _load_cache(ttl_minutes: int, symbols: list[str]) -> dict[str, Any] | None:
    """Return cache contents if fresh enough, else None.

    The cache is invalidated if the universe changes meaningfully (different
    set of tickers), so adding a new ticker forces a refresh next tick.
    """
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("News cache unreadable, ignoring: %s", e)
        return None

    fetched_at_str = data.get("fetched_at")
    if not fetched_at_str:
        return None
    try:
        fetched_at = datetime.fromisoformat(fetched_at_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    now = datetime.now(timezone.utc)
    age = now - fetched_at
    if age > timedelta(minutes=ttl_minutes):
        return None

    cached_symbols = set((data.get("stocks") or {}).keys())
    requested = set(symbols)
    # If the requested set is a strict superset, we need new tickers
    if requested - cached_symbols:
        logger.info("Cache missing tickers %s — forcing refresh",
                    sorted(requested - cached_symbols))
        return None

    data["_age_seconds"] = int(age.total_seconds())
    return data


def _write_cache(payload: dict[str, Any], ttl_minutes: int) -> None:
    NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "ttl_minutes": ttl_minutes,
        "stocks": payload.get("stocks", {}),
        "macro": payload.get("macro", []),
        "provider": payload.get("provider", "unknown"),
    }
    tmp = CACHE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    tmp.replace(CACHE_FILE)


def _flatten_cache(cache: dict[str, Any], per_stock: int, macro: int) -> dict[str, Any]:
    """Trim cached data to the configured headline counts and return the
    {ticker: [...], "macro": [...]} shape consumers expect."""
    stocks = cache.get("stocks", {}) or {}
    out: dict[str, Any] = {ticker: items[:per_stock] for ticker, items in stocks.items()}
    out["macro"] = (cache.get("macro", []) or [])[:macro]
    return out


# ---- Provider chain ------------------------------------------------------

def _fetch_from_providers(
    symbols: list[str],
    per_stock: int,
    macro_count: int,
) -> dict[str, Any]:
    """Run the provider chain. First non-empty response wins."""
    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    av_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    newsapi_key = os.getenv("NEWS_API_KEY", "").strip()

    if finnhub_key:
        try:
            result = _fetch_finnhub(finnhub_key, symbols, per_stock, macro_count)
            if result.get("stocks") or result.get("macro"):
                result["provider"] = "finnhub"
                return result
        except Exception as e:
            logger.warning("Finnhub fetch failed: %s — trying fallback", e)

    if av_key:
        try:
            result = _fetch_alpha_vantage(av_key, symbols, per_stock, macro_count)
            if result.get("stocks") or result.get("macro"):
                result["provider"] = "alpha_vantage"
                return result
        except Exception as e:
            logger.warning("Alpha Vantage fetch failed: %s — trying fallback", e)

    if newsapi_key:
        try:
            result = _fetch_newsapi(newsapi_key, symbols, per_stock, macro_count)
            if result.get("stocks") or result.get("macro"):
                result["provider"] = "newsapi"
                return result
        except Exception as e:
            logger.warning("NewsAPI fetch failed: %s", e)

    if not (finnhub_key or av_key or newsapi_key):
        logger.info("No news provider keys configured — skipping news fetch")
    else:
        logger.warning("All configured news providers returned empty")
    return {"stocks": {}, "macro": []}


# ---- Finnhub -------------------------------------------------------------

def _fetch_finnhub(
    api_key: str,
    symbols: list[str],
    per_stock: int,
    macro_count: int,
) -> dict[str, Any]:
    """Pull company news + general market news from Finnhub.

    Per-stock: /api/v1/company-news?symbol=X&from=YYYY-MM-DD&to=YYYY-MM-DD
    Macro:     /api/v1/news?category=general
    """
    base = "https://finnhub.io/api/v1"
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)
    stocks_out: dict[str, list[dict[str, Any]]] = {}

    for sym in symbols:
        try:
            r = requests.get(
                f"{base}/company-news",
                params={
                    "symbol": sym,
                    "from": week_ago.strftime("%Y-%m-%d"),
                    "to": today.strftime("%Y-%m-%d"),
                    "token": api_key,
                },
                timeout=HTTP_TIMEOUT,
            )
            if r.status_code == 429:
                logger.warning("Finnhub rate-limited on %s — backing off", sym)
                break
            if not r.ok:
                logger.warning("Finnhub %s returned %d: %s", sym, r.status_code, r.text[:200])
                continue
            items = r.json() or []
            normalized = []
            for it in items[: per_stock * 2]:  # over-fetch then filter
                title = (it.get("headline") or "").strip()
                if not title:
                    continue
                normalized.append({
                    "title": title,
                    "source": it.get("source", "Finnhub"),
                    "datetime": _from_unix(it.get("datetime")),
                    "url": it.get("url", ""),
                    "summary": (it.get("summary") or "")[:300],
                })
            if normalized:
                stocks_out[sym] = normalized[:per_stock]
        except requests.RequestException as e:
            logger.warning("Finnhub request error for %s: %s", sym, e)
            continue

    # Macro / general market news
    macro_out: list[dict[str, Any]] = []
    try:
        r = requests.get(
            f"{base}/news",
            params={"category": "general", "token": api_key},
            timeout=HTTP_TIMEOUT,
        )
        if r.ok:
            items = r.json() or []
            for it in items[: macro_count * 2]:
                title = (it.get("headline") or "").strip()
                if not title:
                    continue
                macro_out.append({
                    "title": title,
                    "source": it.get("source", "Finnhub"),
                    "datetime": _from_unix(it.get("datetime")),
                    "url": it.get("url", ""),
                    "summary": (it.get("summary") or "")[:300],
                })
            macro_out = macro_out[:macro_count]
    except requests.RequestException as e:
        logger.warning("Finnhub general news error: %s", e)

    return {"stocks": stocks_out, "macro": macro_out}


# ---- Alpha Vantage -------------------------------------------------------

def _fetch_alpha_vantage(
    api_key: str,
    symbols: list[str],
    per_stock: int,
    macro_count: int,
) -> dict[str, Any]:
    """Alpha Vantage NEWS_SENTIMENT endpoint.

    Free tier is 25 calls/day so we batch all tickers into a single call
    with comma-separated tickers, then split the response.
    """
    url = "https://www.alphavantage.co/query"
    try:
        r = requests.get(
            url,
            params={
                "function": "NEWS_SENTIMENT",
                "tickers": ",".join(symbols[:50]),  # AV caps at 50
                "limit": per_stock * len(symbols),
                "apikey": api_key,
            },
            timeout=HTTP_TIMEOUT,
        )
        if not r.ok:
            return {"stocks": {}, "macro": []}
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Alpha Vantage error: %s", e)
        return {"stocks": {}, "macro": []}

    feed = data.get("feed") or []
    stocks_out: dict[str, list[dict[str, Any]]] = {}
    macro_out: list[dict[str, Any]] = []

    for item in feed:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        normalized = {
            "title": title,
            "source": item.get("source", "AlphaVantage"),
            "datetime": item.get("time_published", ""),
            "url": item.get("url", ""),
            "summary": (item.get("summary") or "")[:300],
        }
        ticker_sent = item.get("ticker_sentiment") or []
        if ticker_sent:
            for ts in ticker_sent:
                t = ts.get("ticker")
                if t in symbols:
                    stocks_out.setdefault(t, []).append(normalized)
        else:
            macro_out.append(normalized)

    # Trim per-stock lists
    for k in list(stocks_out.keys()):
        stocks_out[k] = stocks_out[k][:per_stock]
    macro_out = macro_out[:macro_count]
    return {"stocks": stocks_out, "macro": macro_out}


# ---- NewsAPI -------------------------------------------------------------

def _fetch_newsapi(
    api_key: str,
    symbols: list[str],
    per_stock: int,
    macro_count: int,
) -> dict[str, Any]:
    """NewsAPI fallback. Last-resort path because the free tier is the
    most restrictive (100/day) and the per-ticker query has to be
    keyword-based which is noisy.
    """
    base = "https://newsapi.org/v2"
    headers = {"X-Api-Key": api_key}
    stocks_out: dict[str, list[dict[str, Any]]] = {}

    for sym in symbols[:5]:  # very conservative — burns through 100/day fast
        try:
            r = requests.get(
                f"{base}/everything",
                params={"q": sym, "pageSize": per_stock, "sortBy": "publishedAt", "language": "en"},
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            if not r.ok:
                continue
            articles = (r.json() or {}).get("articles", [])
            normalized = [
                {
                    "title": a.get("title", ""),
                    "source": (a.get("source") or {}).get("name", "NewsAPI"),
                    "datetime": a.get("publishedAt", ""),
                    "url": a.get("url", ""),
                    "summary": (a.get("description") or "")[:300],
                }
                for a in articles if a.get("title")
            ]
            if normalized:
                stocks_out[sym] = normalized[:per_stock]
        except requests.RequestException as e:
            logger.warning("NewsAPI error for %s: %s", sym, e)
            continue

    # Macro: top business headlines
    macro_out: list[dict[str, Any]] = []
    try:
        r = requests.get(
            f"{base}/top-headlines",
            params={"category": "business", "country": "us", "pageSize": macro_count},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        if r.ok:
            articles = (r.json() or {}).get("articles", [])
            macro_out = [
                {
                    "title": a.get("title", ""),
                    "source": (a.get("source") or {}).get("name", "NewsAPI"),
                    "datetime": a.get("publishedAt", ""),
                    "url": a.get("url", ""),
                    "summary": (a.get("description") or "")[:300],
                }
                for a in articles if a.get("title")
            ][:macro_count]
    except requests.RequestException as e:
        logger.warning("NewsAPI macro error: %s", e)

    return {"stocks": stocks_out, "macro": macro_out}


# ---- helpers -------------------------------------------------------------

def _from_unix(ts: Any) -> str:
    """Convert a unix timestamp (Finnhub uses these) to an ISO string."""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return str(ts)


def hash_news_payload(news: dict[str, Any]) -> str:
    """Stable sha256 hash of the news payload — used by the decision log so
    every trade record can be correlated back to the exact headline set the
    model saw at decision time."""
    import hashlib

    def _norm(items: list[dict[str, Any]]) -> list[str]:
        return sorted((i.get("title", "") + "|" + i.get("datetime", "")) for i in (items or []))

    payload = {
        "stocks": {k: _norm(v) for k, v in (news or {}).items() if k != "macro"},
        "macro": _norm((news or {}).get("macro", [])),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
