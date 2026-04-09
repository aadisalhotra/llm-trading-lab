"""Market data ingestion."""
from .market_data import (
    fetch_universe_data,
    fetch_index_data,
    get_latest_price,
    is_market_open_today,
    INDEX_SYMBOLS,
)
