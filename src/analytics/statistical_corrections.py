"""Statistical corrections for the LLM Trading Lab paper track.

Three families of correction, all implemented in pure numpy + math so the
research stack carries zero new dependencies (no scipy, no statsmodels):

  1. Moving-block bootstrap with BCa intervals — the inferential workhorse.
     Block length 5, 10,000 resamples by default. Blocks (rather than IID
     resampling) preserve the short-horizon autocorrelation in daily
     portfolio returns and decision series; BCa (bias-corrected and
     accelerated) corrects for skew and median bias in the bootstrap
     distribution, which a naive percentile interval ignores.

  2. Benjamini-Hochberg FDR control at q = 0.10 — we test six pre-registered
     research questions (several with per-model sub-tests). Controlling the
     family-wise false-discovery rate stops us from declaring a "finding"
     that is just the best of many noisy comparisons.

  3. Deflated / probabilistic Sharpe ratio — raw Sharpe is inflated by
     non-normal returns and by selecting the best of many strategies. The
     deflated Sharpe (Bailey & Lopez de Prado, 2014) discounts the observed
     Sharpe for skew, kurtosis, sample length, and the number of trials.

All thresholds here are the pre-registered defaults from
``docs/PRE_REGISTRATION.md``. They are exposed as function arguments so the
monthly report and ad-hoc analysis can override them, but the paper uses the
defaults.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Callable, Sequence

import numpy as np

# Pre-registered defaults (see docs/PRE_REGISTRATION.md §Methodological commitments)
DEFAULT_BLOCK_LENGTH = 5
DEFAULT_N_RESAMPLES = 10_000
DEFAULT_FDR_Q = 0.10
DEFAULT_CI_ALPHA = 0.10            # 90% interval -> alpha 0.10 (two-sided)
_EULER_MASCHERONI = 0.5772156649015329


# --------------------------------------------------------------------------
# Normal distribution helpers (no scipy)
# --------------------------------------------------------------------------

def norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Standard-normal inverse CDF (quantile function).

    Acklam's rational approximation followed by one Halley refinement step
    using the erf-based CDF. Accurate to ~1e-15 over the open interval (0, 1),
    which is well past what any bootstrap percentile needs. Clamps the input
    away from the exact endpoints so callers never hit ``±inf``.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf

    # Coefficients for Acklam's algorithm
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
            (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)

    # One Halley refinement step
    e = norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


# --------------------------------------------------------------------------
# Moving-block bootstrap + BCa interval
# --------------------------------------------------------------------------

@dataclass
class BootstrapResult:
    point_estimate: float
    ci_low: float
    ci_high: float
    alpha: float
    method: str               # "BCa" or "percentile" (fallback)
    n_resamples: int
    block_length: int
    n_observations: int
    z0: float | None          # bias-correction term
    acceleration: float | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _moving_block_indices(n: int, block_length: int, rng: np.random.Generator) -> np.ndarray:
    """Sample circular moving-block indices covering n observations."""
    if block_length < 1:
        block_length = 1
    n_blocks = int(math.ceil(n / block_length))
    starts = rng.integers(0, n, size=n_blocks)
    idx = (starts[:, None] + np.arange(block_length)[None, :]) % n
    return idx.reshape(-1)[:n]


def moving_block_bootstrap(
    data: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    block_length: int = DEFAULT_BLOCK_LENGTH,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int | None = 12345,
) -> np.ndarray:
    """Return an array of ``n_resamples`` bootstrap replicates of ``statistic``.

    Uses the circular moving-block bootstrap (Kunsch 1989 / Politis & Romano)
    so serial dependence within ``block_length`` is preserved in each resample.
    """
    arr = np.asarray(data, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n == 0:
        return np.array([])
    rng = np.random.default_rng(seed)
    reps = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        idx = _moving_block_indices(n, block_length, rng)
        try:
            reps[i] = statistic(arr[idx])
        except Exception:
            reps[i] = np.nan
    return reps[np.isfinite(reps)]


def _jackknife_blocks(arr: np.ndarray, statistic: Callable[[np.ndarray], float],
                      block_length: int) -> np.ndarray:
    """Delete-one-block jackknife values, for the BCa acceleration term."""
    n = len(arr)
    n_blocks = int(math.ceil(n / block_length))
    jack = []
    for b in range(n_blocks):
        lo = b * block_length
        hi = min(lo + block_length, n)
        kept = np.concatenate([arr[:lo], arr[hi:]])
        if len(kept) >= 2:
            try:
                jack.append(statistic(kept))
            except Exception:
                continue
    return np.asarray(jack, dtype=float)


def bca_bootstrap_ci(
    data: Sequence[float],
    statistic: Callable[[np.ndarray], float] = np.mean,
    alpha: float = DEFAULT_CI_ALPHA,
    block_length: int = DEFAULT_BLOCK_LENGTH,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int | None = 12345,
) -> BootstrapResult:
    """Bias-corrected and accelerated (BCa) moving-block bootstrap interval.

    ``alpha`` is the *total* two-sided miss rate: alpha=0.10 -> 90% interval.
    Falls back to a plain percentile interval (flagged in ``method``/``note``)
    when the data are too short or degenerate for the BCa adjustment.
    """
    arr = np.asarray(data, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 2:
        pt = float(statistic(arr)) if n == 1 else float("nan")
        return BootstrapResult(pt, float("nan"), float("nan"), alpha,
                               "insufficient", n_resamples, block_length, n,
                               None, None, "Need >=2 observations for a bootstrap CI.")

    theta_hat = float(statistic(arr))
    reps = moving_block_bootstrap(arr, statistic, block_length, n_resamples, seed)
    if len(reps) < 2 or np.allclose(reps, reps[0]):
        return BootstrapResult(theta_hat, theta_hat, theta_hat, alpha,
                               "degenerate", n_resamples, block_length, n,
                               None, None, "Bootstrap distribution was degenerate.")

    lo_pct = 100.0 * (alpha / 2.0)
    hi_pct = 100.0 * (1.0 - alpha / 2.0)

    # Bias-correction z0: inverse-normal of the fraction of reps below theta_hat
    prop_less = float(np.mean(reps < theta_hat))
    prop_less = min(max(prop_less, 1.0 / (len(reps) + 1)), 1.0 - 1.0 / (len(reps) + 1))
    z0 = norm_ppf(prop_less)

    # Acceleration via delete-one-block jackknife
    jack = _jackknife_blocks(arr, statistic, block_length)
    accel = 0.0
    if len(jack) >= 2:
        jack_mean = jack.mean()
        diffs = jack_mean - jack
        denom = 6.0 * (np.sum(diffs ** 2) ** 1.5)
        if denom != 0:
            accel = float(np.sum(diffs ** 3) / denom)

    method = "BCa"
    note = ""
    if not (math.isfinite(z0) and math.isfinite(accel)):
        # Fall back to percentile interval
        lo = float(np.percentile(reps, lo_pct))
        hi = float(np.percentile(reps, hi_pct))
        return BootstrapResult(theta_hat, lo, hi, alpha, "percentile",
                               n_resamples, block_length, n, None, None,
                               "BCa terms non-finite; used percentile interval.")

    z_lo = norm_ppf(alpha / 2.0)
    z_hi = norm_ppf(1.0 - alpha / 2.0)

    def _adjust(zq: float) -> float:
        num = z0 + zq
        denom = 1.0 - accel * num
        if denom == 0:
            denom = 1e-12
        return norm_cdf(z0 + num / denom)

    a1 = _adjust(z_lo)
    a2 = _adjust(z_hi)
    if not (math.isfinite(a1) and math.isfinite(a2)) or a1 >= a2:
        lo = float(np.percentile(reps, lo_pct))
        hi = float(np.percentile(reps, hi_pct))
        method, note = "percentile", "BCa adjusted quantiles invalid; used percentile interval."
    else:
        lo = float(np.percentile(reps, 100.0 * a1))
        hi = float(np.percentile(reps, 100.0 * a2))

    return BootstrapResult(theta_hat, lo, hi, alpha, method, n_resamples,
                           block_length, n, float(z0), float(accel), note)


# Convenience aliases used elsewhere in the research stack
def block_bootstrap_mean_ci(data: Sequence[float], **kw: Any) -> BootstrapResult:
    return bca_bootstrap_ci(data, statistic=np.mean, **kw)


# --------------------------------------------------------------------------
# Benjamini-Hochberg FDR control
# --------------------------------------------------------------------------

@dataclass
class FDRResult:
    labels: list[str]
    p_values: list[float]
    reject: list[bool]
    p_adjusted: list[float]
    q: float
    n_tests: int
    n_significant: int
    critical_p: float | None      # largest raw p that passed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def significant_labels(self) -> list[str]:
        return [lab for lab, rej in zip(self.labels, self.reject) if rej]


def benjamini_hochberg(
    p_values: Sequence[float],
    q: float = DEFAULT_FDR_Q,
    labels: Sequence[str] | None = None,
) -> FDRResult:
    """Benjamini-Hochberg step-up FDR control at level ``q``.

    Returns per-test reject flags and BH-adjusted p-values (monotone
    enforced), the number of discoveries, and the critical raw-p threshold.
    NaN p-values are treated as 1.0 (never rejected) so a failed sub-test
    can't crash the family.
    """
    raw = [1.0 if (pv is None or not math.isfinite(pv)) else float(pv) for pv in p_values]
    m = len(raw)
    if labels is None:
        labels = [f"test_{i+1}" for i in range(m)]
    labels = list(labels)
    if m == 0:
        return FDRResult([], [], [], [], q, 0, 0, None)

    order = sorted(range(m), key=lambda i: raw[i])
    ranked = [raw[i] for i in order]

    # Adjusted p-values (monotone from the top)
    adj_sorted = [0.0] * m
    prev = 1.0
    for k in range(m, 0, -1):
        val = ranked[k - 1] * m / k
        prev = min(prev, val)
        adj_sorted[k - 1] = min(prev, 1.0)

    # Largest k with p_(k) <= (k/m) q  -> reject all up to that rank
    max_k = 0
    for k in range(1, m + 1):
        if ranked[k - 1] <= (k / m) * q:
            max_k = k
    critical_p = ranked[max_k - 1] if max_k > 0 else None

    reject_sorted = [k <= max_k for k in range(1, m + 1)]

    # Unsort back to input order
    reject = [False] * m
    p_adjusted = [1.0] * m
    for pos, i in enumerate(order):
        reject[i] = reject_sorted[pos]
        p_adjusted[i] = adj_sorted[pos]

    return FDRResult(labels, raw, reject, p_adjusted, q, m,
                     sum(reject), critical_p)


# --------------------------------------------------------------------------
# Sharpe family: raw, probabilistic, deflated
# --------------------------------------------------------------------------

def sharpe_ratio(returns: Sequence[float], periods_per_year: int = 252,
                 risk_free_per_period: float = 0.0) -> float | None:
    """Annualized Sharpe ratio of a per-period return series."""
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return None
    excess = arr - risk_free_per_period
    sd = excess.std(ddof=1)
    if sd == 0 or not math.isfinite(sd):
        return None
    return float(excess.mean() / sd * math.sqrt(periods_per_year))


def _moments(returns: np.ndarray) -> tuple[float, float, float]:
    """Per-period Sharpe (non-annualized), skew, and (non-excess) kurtosis."""
    sd = returns.std(ddof=1)
    if sd == 0 or not math.isfinite(sd):
        return float("nan"), 0.0, 3.0
    sr = returns.mean() / sd
    z = (returns - returns.mean()) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))       # 3.0 for a normal distribution
    return float(sr), skew, kurt


def probabilistic_sharpe_ratio(
    returns: Sequence[float],
    benchmark_sr_per_period: float = 0.0,
) -> dict[str, Any]:
    """Probabilistic Sharpe Ratio: P(true SR > benchmark) given the sample.

    Uses the non-annualized per-period Sharpe and the sample skew/kurtosis
    so heavy tails and asymmetry deflate the confidence correctly
    (Bailey & Lopez de Prado, 2012).
    """
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 3:
        return {"psr": None, "sr_per_period": None, "n": n,
                "note": "Need >=3 observations."}
    sr, skew, kurt = _moments(arr)
    if not math.isfinite(sr):
        return {"psr": None, "sr_per_period": None, "n": n,
                "note": "Zero variance."}
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2))
    psr = norm_cdf(((sr - benchmark_sr_per_period) * math.sqrt(n - 1)) / denom)
    return {"psr": float(psr), "sr_per_period": float(sr), "skew": skew,
            "kurtosis": kurt, "n": n, "benchmark_sr_per_period": benchmark_sr_per_period}


def expected_max_sharpe(n_trials: int, trial_sr_variance: float) -> float:
    """Expected maximum per-period Sharpe across ``n_trials`` independent trials.

    The benchmark SR* used by the deflated Sharpe: the Sharpe you would expect
    to see by chance as the best of N tries, given the cross-trial variance of
    Sharpe estimates (Bailey & Lopez de Prado, 2014).
    """
    if n_trials < 1 or trial_sr_variance <= 0:
        return 0.0
    sd = math.sqrt(trial_sr_variance)
    if n_trials == 1:
        return 0.0
    a = norm_ppf(1.0 - 1.0 / n_trials)
    b = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - _EULER_MASCHERONI) * a + _EULER_MASCHERONI * b)


def deflated_sharpe_ratio(
    returns: Sequence[float],
    n_trials: int,
    trial_sr_variance: float | None = None,
    trial_sharpes: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Deflated Sharpe Ratio (DSR).

    DSR = P(true SR > SR*) where SR* is the expected best-of-N-trials Sharpe.
    A DSR near 1.0 means the strategy's Sharpe survives the multiple-testing
    deflation; near 0.5 or below means it is indistinguishable from the best
    of ``n_trials`` random strategies.

    ``trial_sr_variance`` is the cross-trial variance of *per-period* Sharpe
    estimates. If you pass ``trial_sharpes`` (e.g. the six models' Sharpes)
    it is estimated from them. With neither, it falls back to the analytic
    1/(n-1) variance of a single Sharpe under the null, which is conservative.
    """
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 3:
        return {"dsr": None, "sr_star": None, "n": n, "n_trials": n_trials,
                "note": "Need >=3 observations."}

    if trial_sr_variance is None and trial_sharpes is not None:
        ts = np.asarray([s for s in trial_sharpes if s is not None and math.isfinite(s)], dtype=float)
        trial_sr_variance = float(ts.var(ddof=1)) if len(ts) >= 2 else None
    if trial_sr_variance is None:
        trial_sr_variance = 1.0 / (n - 1)   # analytic null variance of a Sharpe

    sr_star = expected_max_sharpe(n_trials, trial_sr_variance)
    psr = probabilistic_sharpe_ratio(arr, benchmark_sr_per_period=sr_star)
    return {
        "dsr": psr["psr"],
        "sr_star": float(sr_star),
        "sr_per_period": psr.get("sr_per_period"),
        "skew": psr.get("skew"),
        "kurtosis": psr.get("kurtosis"),
        "n": n,
        "n_trials": n_trials,
        "trial_sr_variance": float(trial_sr_variance),
    }


__all__ = [
    "norm_cdf", "norm_ppf",
    "BootstrapResult", "moving_block_bootstrap", "bca_bootstrap_ci",
    "block_bootstrap_mean_ci",
    "FDRResult", "benjamini_hochberg",
    "sharpe_ratio", "probabilistic_sharpe_ratio", "expected_max_sharpe",
    "deflated_sharpe_ratio",
    "DEFAULT_BLOCK_LENGTH", "DEFAULT_N_RESAMPLES", "DEFAULT_FDR_Q", "DEFAULT_CI_ALPHA",
]
