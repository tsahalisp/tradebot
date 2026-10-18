"""Tests for the edge statistics (metrics.compute_metrics: Sharpe, drawdown, alpha vs hold)
and for how they separate a planted edge from noise against a shuffled-signal placebo.

The repo has NO dedicated bootstrap/permutation helper, so the placebo here is built in the
test (permute the position series, re-run backtest.evaluate_pos) using the real metrics code.
All data is synthetic with fixed seeds. Tests marked xfail(strict=True) document REAL bugs;
the code is deliberately not fixed.
"""
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest import evaluate_pos  # noqa: E402
from metrics import compute_metrics  # noqa: E402


# ---------- (1) hand-computed fixture ----------
EQUITY = [1.0, 1.1, 0.99, 1.089]
RETS = [0.1, -0.1, 0.1]


def test_sharpe_drawdown_alpha_match_hand_computation():
    # mean=1/30, sample sd=sqrt(0.0133333), Sharpe = mean/sd*sqrt(365) = 5.5154 -> 5.52
    # max DD: peak 1.1 -> 0.99 = -10%; total = 8.9%; buy&hold 5% -> alpha 3.9%
    m = compute_metrics(EQUITY, RETS, [1, 1, 1], [], "1d", 0.05)
    assert m["sharpe"] == pytest.approx(5.52, abs=0.001)
    assert m["max_drawdown_pct"] == -10.0
    assert m["total_return_pct"] == 8.9
    assert m["buy_hold_return_pct"] == 5.0
    assert m["alpha_vs_hold_pct"] == 3.9


def test_sharpe_annualisation_depends_on_granularity():
    d = compute_metrics(EQUITY, RETS, [1, 1, 1], [], "1d", 0.0)["sharpe"]
    h = compute_metrics(EQUITY, RETS, [1, 1, 1], [], "1h", 0.0)["sharpe"]
    assert h / d == pytest.approx(math.sqrt(8760 / 365), rel=0.01)


def test_monotonic_equity_has_zero_drawdown():
    eq = [1.0, 1.01, 1.02, 1.03]
    m = compute_metrics(eq, [0.01, 0.0099, 0.0098], [1, 1, 1], [], "1d", 0.0)
    assert m["max_drawdown_pct"] == 0.0


def test_trade_stats_hand_computed():
    trades = [{"pnl_pct": 4.0}, {"pnl_pct": -2.0}, {"pnl_pct": 2.0}]
    m = compute_metrics(EQUITY, RETS, [1, 0, 1], trades, "1d", 0.0)
    assert m["win_rate_pct"] == pytest.approx(66.7)
    assert m["profit_factor"] == 3.0
    assert m["exposure_pct"] == pytest.approx(66.7)


# ---------- (2) noise vs planted edge, with a shuffled-signal placebo ----------
N = 1500


def make_prices(seed, drift_when_long=0.0):
    """Random-walk closes plus a signal. If drift_when_long>0 the signal has a planted edge:
    bar i+1's return is shifted up when pos[i]==1."""
    rng = random.Random(seed)
    pos = [1 if rng.random() < 0.5 else 0 for _ in range(N)]
    closes = [100.0]
    for i in range(N - 1):
        r = rng.gauss(0.0, 0.01) + (drift_when_long if pos[i] == 1 else 0.0)
        closes.append(closes[-1] * (1 + r))
    return closes, pos


def sharpe_of(closes, pos):
    m, _ = evaluate_pos(closes, pos, fee_bps=0.0)
    return m["sharpe"]


def permutation_p(closes, pos, n_perm=200, seed=7):
    """One-sided p: share of position-shuffles whose Sharpe >= observed (with +1 smoothing)."""
    obs = sharpe_of(closes, pos)
    rng = random.Random(seed)
    shuffled = list(pos)
    ge = 0
    for _ in range(n_perm):
        rng.shuffle(shuffled)
        if sharpe_of(closes, shuffled) >= obs:
            ge += 1
    return obs, (ge + 1) / (n_perm + 1)


def test_pure_noise_edge_near_zero_and_not_significant():
    closes, pos = make_prices(seed=1)
    obs, p = permutation_p(closes, pos)
    assert abs(obs) < 1.5     # |Sharpe| of a random signal on ~6y daily noise is O(1)
    assert p > 0.05


def test_planted_edge_is_detected():
    closes, pos = make_prices(seed=1, drift_when_long=0.003)   # +0.3%/bar when long
    obs, p = permutation_p(closes, pos)
    assert obs > 3.0
    assert p < 0.01


def test_noise_false_positive_rate_is_controlled():
    # Across several noise seeds, "significant at 5%" should be rare (not systematic).
    hits = sum(permutation_p(*make_prices(seed=s), n_perm=100)[1] < 0.05 for s in range(10, 20))
    assert hits <= 2


# ---------- (3) determinism ----------
def test_metrics_deterministic_for_fixed_seed():
    assert evaluate_pos(*make_prices(seed=3)) == evaluate_pos(*make_prices(seed=3))


def test_permutation_p_deterministic_for_fixed_seed():
    closes, pos = make_prices(seed=4, drift_when_long=0.001)
    assert permutation_p(closes, pos, n_perm=50, seed=9) == permutation_p(closes, pos, n_perm=50, seed=9)


# ---------- (4) degenerate inputs ----------
def _is_loud(fn):
    """True if fn raises or returns NaN for sharpe; False if it returns a finite value."""
    try:
        v = fn()
    except (ValueError, IndexError, ZeroDivisionError):
        return True
    return isinstance(v, float) and math.isnan(v)


def test_never_inf_on_degenerate_series():
    for eq, rets, pos in [([1.0, 1.0, 1.0], [0.0, 0.0], [1, 1]),
                          ([1.0, 1.01], [0.01], [1]),
                          ([1.0], [], [])]:
        m = compute_metrics(eq, rets, pos, [], "1d", 0.0)
        for v in m.values():
            assert v is None or not (isinstance(v, float) and math.isinf(v))


@pytest.mark.xfail(strict=True, reason=(
    "BUG: metrics.compute_metrics([], [], ...) crashes with a bare IndexError on equity[0] "
    "(total is guarded by len>1 but the drawdown loop is not) -- an unclear error, not a "
    "deliberate ValueError/NaN. Callers can't tell 'no data' from a code bug."))
def test_empty_series_raises_clear_error_or_nan():
    with pytest.raises(ValueError):
        compute_metrics([], [], [], [], "1d", 0.0)


@pytest.mark.xfail(strict=True, reason=(
    "BUG: constant (zero-variance) returns give sharpe == 0.0 -- a silent zero that looks like "
    "'no edge' rather than 'undefined'. Should be NaN (or raise). Same for the n<=1 case below."))
def test_constant_series_sharpe_is_nan_not_zero():
    m = compute_metrics([1.0, 1.0, 1.0, 1.0], [0.0, 0.0, 0.0], [1, 1, 1], [], "1d", 0.0)
    assert math.isnan(m["sharpe"])


@pytest.mark.xfail(strict=True, reason=(
    "BUG: a too-short series (a single return) gives sharpe == 0.0 silently instead of NaN/error; "
    "a 1-sample Sharpe is undefined."))
def test_too_short_series_sharpe_is_nan_not_zero():
    m = compute_metrics([1.0, 1.01], [0.01], [1], [], "1d", 0.0)
    assert math.isnan(m["sharpe"])


@pytest.mark.xfail(strict=True, reason=(
    "BUG: evaluate_pos on <=1 closes returns metrics with sharpe 0.0 / total 0.0 instead of an error "
    "(run_backtest guards len<35 but evaluate/evaluate_pos, used by validate, do not)."))
def test_evaluate_pos_too_short_is_loud():
    assert _is_loud(lambda: evaluate_pos([100.0], [1])[0]["sharpe"])
