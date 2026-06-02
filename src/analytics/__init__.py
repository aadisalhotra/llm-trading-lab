"""Performance metrics + leaderboard generation."""
from .performance import (
    compute_metrics,
    compute_spy_benchmark_metrics,
    build_leaderboard,
    load_performance_history,
    canonical_perf_frame,
    canonical_spy_series,
    canonical_spy_return,
    compute_api_cost_summary,
    compute_api_cost_summary_window,
    compute_budget_status,
)
from .cost_rates import COST_PER_MTOK, compute_call_cost_usd
from .research_metrics import compute_all_research_metrics
from .regime_classifier import classify_regimes, summarize_regimes, ALL_REGIMES
