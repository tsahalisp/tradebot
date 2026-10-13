"""Backtester cost/accounting tests on synthetic prices only (no network, no data feed).

evaluate_pos() only returns (metrics, trades), so the raw equity curve / per-bar
returns are captured by wrapping backtest.compute_metrics. Every test is reported
against a benchmark: hand-computed buy-and-hold, or a seeded random-signal placebo.
"""
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest  # noqa: E402


@pytest.fixture
def run(monkeypatch):
    """run(closes, pos, fee_bps) -> (metrics, trades, equity, rets)."""
    cap = {}
    real = backtest.compute_metrics

    def spy(equity, rets, pos, trades, gran, bh):
        cap["equity"], cap["rets"] = list(equity), list(rets)
        return real(equity, rets, pos, trades, gran, bh)

    monkeypatch.setattr(backtest, "compute_metrics", spy)

    def _run(closes, pos, fee_bps=10.0):
        m, t = backtest.evaluate_pos(closes, pos, fee_bps)
        return m, t, cap["equity"], cap["rets"]
    return _run


def _walk(seed, n=250, step=0.01):
    """Zero-mean symmetric random walk: simple returns are +/-step."""
    rng = random.Random(seed)
    px = [100.0]
    for _ in range(n - 1):
        px.append(px[-1] * (1 + rng.choice([-step, step])))
    return px


def _final(closes, pos, fee_bps, run):
    return run(closes, pos, fee_bps)[2][-1] - 1


# (1) zero signals ---------------------------------------------------------
def test_zero_signals_flat_equity_no_trades(run):
    closes = _walk(1)
    m, trades, equity, rets = run(closes, [0] * len(closes), 25.0)
    assert equity == [1.0] * len(closes)
    assert all(r == 0.0 for r in rets)          # no return and no fee drag => zero turnover
    assert trades == []
    assert m["num_trades"] == 0 and m["exposure_pct"] == 0.0
    assert m["total_return_pct"] == 0.0 and m["max_drawdown_pct"] == 0.0
    # benchmark: buy&hold is reported separately and is NOT what a flat book earns
    assert m["alpha_vs_hold_pct"] == pytest.approx(-m["buy_hold_return_pct"])


# (2) hand-computed cases --------------------------------------------------
def test_buy_and_hold_fee_free_matches_hand_value(run):
    closes = [100.0, 110.0, 121.0, 133.1, 146.41]
    m, trades, equity, _ = run(closes, [1] * 5, fee_bps=0.0)
    assert equity[-1] == pytest.approx(1.4641, abs=1e-12)
    assert m["total_return_pct"] == 46.41
    assert m["buy_hold_return_pct"] == 46.41 and m["alpha_vs_hold_pct"] == 0.0
    assert trades[0]["open"] and trades[0]["pnl_pct"] == 46.41


def test_round_trip_with_fees_matches_hand_value(run):
    # flat, enter at bar1 (fee), +10%, +10%, exit at bar3 (fee), flat.
    closes = [100.0, 100.0, 110.0, 121.0, 121.0]
    pos = [0, 1, 1, 0, 0]
    fee = 0.001
    m, trades, equity, rets = run(closes, pos, fee_bps=10.0)
    expected = (1 + 0.10 - fee) * (1 + 0.10) * (1 - fee)
    assert equity[-1] == pytest.approx(expected, rel=1e-12)
    assert m["total_return_pct"] == round((expected - 1) * 100, 2)
    assert len(trades) == 1 and trades[0]["pnl_pct"] == round((1.21 - 1 - 2 * fee) * 100, 2)
    # benchmark: gross (fee-free) value is the hand-computed 1.1*1.1; costs only subtract
    assert _final(closes, pos, 0.0, run) == pytest.approx(0.21, abs=1e-12)
    assert equity[-1] - 1 < 0.21


def test_one_bar_lag_no_lookahead(run):
    # position chosen at bar i earns bar i->i+1 only; pos[-1] never earns anything.
    closes = [100.0, 200.0, 100.0]
    assert _final(closes, [0, 1, 1], 0.0, run) == pytest.approx(-0.5)
    assert _final(closes, [1, 0, 0], 0.0, run) == pytest.approx(1.0)


@pytest.mark.xfail(strict=True, reason=(
    "ACCOUNTING BUG: evaluate_pos charges the fee only when `i > 0 and pos[i] != pos[i-1]`, "
    "so a position already long at bar 0 (buy-and-hold, or any strategy long from the first "
    "bar) enters for free. Equity ignores the entry fee even though the trade log "
    "(pnl_pct, 2*fee) charges it, so equity and trade pnl disagree by one fee."))
def test_initial_entry_pays_fee(run):
    closes = [100.0, 110.0, 121.0]
    fee = 0.001
    expected = (1 - fee) * 1.21
    assert _final(closes, [1, 1, 1], 10.0, run) + 1 == pytest.approx(expected, rel=1e-12)


# (3) cost monotonicity / proportionality ----------------------------------
def test_higher_fee_never_increases_return(run):
    rng = random.Random(7)
    for seed in range(30):
        closes = _walk(seed, n=120)
        pos = [rng.choice([0, 1]) for _ in closes]
        finals = [_final(closes, pos, bps, run) for bps in (0, 1, 5, 10, 25, 50, 100)]
        assert all(a >= b - 1e-15 for a, b in zip(finals, finals[1:])), (seed, finals)


def test_cost_drag_proportional_to_turnover(run):
    # flat prices => all PnL is cost. k position changes => equity (1-fee)^k.
    fee_bps, fee = 20.0, 0.002
    closes = [100.0] * 41
    drags = {}
    for k in (1, 2, 4, 8, 16):
        pos = [0] * 41
        # toggle k times starting at bar 1 (bar 0 is excluded from fee charging)
        state = 0
        for j in range(k):
            state ^= 1
            for t in range(1 + j * 2, 41):
                pos[t] = state
        _, _, equity, _ = run(closes, pos, fee_bps)
        assert equity[-1] == pytest.approx((1 - fee) ** k, rel=1e-12)
        drags[k] = -math.log(equity[-1])
    base = drags[1]
    for k, d in drags.items():
        assert d / base == pytest.approx(k, rel=1e-3)   # drag ~ turnover
    # doubling fee doubles the (log) drag for the same turnover
    pos = [0, 1, 0, 1, 0] + [0] * 36
    d1 = -math.log(run(closes, pos, 10.0)[2][-1])
    d2 = -math.log(run(closes, pos, 20.0)[2][-1])
    assert d2 / d1 == pytest.approx(2.0, rel=1e-2)


# placebo: random signals have no edge, and costs make net strictly worse ---
def test_random_signal_placebo_has_no_net_edge(run):
    n_runs, gross, net = 400, [], []
    for seed in range(n_runs):
        rng = random.Random(1000 + seed)
        closes = _walk(seed, n=250)
        pos = [rng.choice([0, 1]) for _ in closes]
        gross.append(_final(closes, pos, 0.0, run))
        net.append(_final(closes, pos, 10.0, run))
    mean = lambda x: sum(x) / len(x)
    se = (sum((g - mean(gross)) ** 2 for g in gross) / (n_runs - 1)) ** 0.5 / n_runs ** 0.5
    assert abs(mean(gross)) < 4 * se            # no gross edge from random signals
    assert mean(net) < mean(gross)              # fees strictly cost
    assert mean(net) < 4 * se                   # no positive net edge => no fake edge


# (4) bad data -------------------------------------------------------------
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "BUG (silent bad data): evaluate_pos never validates closes. A NaN bar propagates "
    "into equity and metrics (total_return/sharpe become NaN or garbage) with no error "
    "or flag; callers such as validate/run_backtest would report it as a result."))
@pytest.mark.parametrize("bad", [float("nan"), None])
def test_nan_or_missing_bar_is_rejected_or_flagged(run, bad):
    closes = [100.0, 101.0, bad, 103.0, 104.0, 105.0]
    pos = [1] * len(closes)
    try:
        m, _, equity, _ = run(closes, pos, 10.0)
    except (ValueError, TypeError, ArithmeticError) as e:
        # TypeError on None is an incidental crash, not a clear error message
        assert bad is not None and "nan" in str(e).lower() or "missing" in str(e).lower()
        return
    flagged = any("nan" in str(k).lower() or "invalid" in str(k).lower() for k in m)
    assert flagged or all(math.isfinite(e) for e in equity), "NaN silently produced numbers"
    assert flagged, f"NaN bar silently produced metrics: {m}"
