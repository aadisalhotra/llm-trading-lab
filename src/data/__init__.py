"""Market data + news ingestion."""
from .market_data import (
    fetch_universe_data,
    fetch_intraday_data,
    fetch_index_data,
    get_latest_price,
    is_market_open_today,
    is_market_open_now,
    INDEX_SYMBOLS,
)
from .news import fetch_news, fetch_top_macro_headlines, hash_news_payload
from .sentiment import (
    score_headline,
    aggregate_sentiment,
    compute_sentiment_dict,
    sentiment_label,
)
