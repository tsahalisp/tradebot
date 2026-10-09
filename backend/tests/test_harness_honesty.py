"""Honesty guarantees of the research harness, on SYNTHETIC data only (no network):
 1. walk-forward windows never overlap; test starts after train ends
 2. point-in-time: perturbing future prices cannot change earlier signals/positions/equity
 3. every result is reported next to a placebo/benchmark (random / shuffled signals, buy&hold)
Run with `pytest -rP` to see the printed placebo/benchmark lines."""
import math
import random
import statistics

import pytest

import backtest
import validate
from backtest import evaluate_pos
from strategies import REGISTRY

STRATS = sorted(REGISTRY)


def walk(n, seed, drift=0.0004, vol=0.01, start=100.0):
    rng = random.Random(seed)
    px, out = start, []
    for _ in range(n):
        px *= math.exp(drift + vol * rng.gauss(0, 1))
        out.append(px)
    return out


def report(name, strat, bench):
    print(f"[REPORT] {name}: strategy={strat} | placebo/benchmark={bench}")


# ---------------------------------------------------------------- (1) windows

def _run_validate(monkeypatch, n, split, strategy="sma_cross"):
    closes = walk(n, seed=1)
    bars = [{"t": i, "c": c} for i, c in enumerate(closes)]
    monkeypatch.setattr(validate, "get_bars", lambda *a, **k: bars)
    calls = []
    real = validate.evaluate

    def spy(cl, *a, **k):
        calls.append(list(cl))
        return real(cl, *a, **k)

    monkeypatch.setattr(validate, "evaluate", spy)
    res = validate.validate("SYN", strategy, split=split)
    return closes, calls, res


@pytest.mark.parametrize("n,split", [(150, 0.7), (600, 0.7), (600, 0.5), (333, 0.8)])
def test_train_test_windows_disjoint_and_ordered(monkeypatch, n, split):
    closes, calls, res = _run_validate(monkeypatch, n, split)
    assert "error" not in res
    train_calls, test_call = calls[:-1], calls[-1]
    k = res["in_sample"]["bars"]
    # every grid candidate trains on the same prefix; the OOS call is the final one
    assert all(c == closes[:k] for c in train_calls)
    assert test_call == closes[k:]
    # no overlap, no gap, nothing dropped, test strictly after train
    assert res["in_sample"]["bars"] + res["out_of_sample"]["bars"] == n
    assert closes[:k] + test_call == closes
    assert closes.index(test_call[0]) == k > closes.index(train_calls[0][-1])
    report(f"windows n={n} split={split}", f"train=[0,{k}) test=[{k},{n})", "buy&hold alpha=0 by construction")


def test_oos_result_independent_of_future_of_train_only_data(monkeypatch):
    """OOS metrics must be unaffected by changing TRAIN prices once params are fixed."""
    closes, _, r1 = _run_validate(monkeypatch, 400, 0.7, strategy="buy_hold")
    k = r1["in_sample"]["bars"]
    bars = [{"t": i, "c": (c * 3 if i < k else c)} for i, c in enumerate(closes)]
    monkeypatch.setattr(validate, "get_bars", lambda *a, **kw: bars)
    r2 = validate.validate("SYN", "buy_hold", split=0.7)
    assert r1["out_of_sample"] == r2["out_of_sample"]


# ------------------------------------------------------- (2) point-in-time

CUTS = [40, 90, 150, 210, 260]


@pytest.mark.parametrize("name", STRATS)
def test_signal_at_t_ignores_future_prices(name):
    base = walk(320, seed=7)
    fn = REGISTRY[name]
    pos = fn(base)
    rng = random.Random(3)
    checked = 0
    for t in CUTS:
        for trial in range(3):
            pert = base[:t + 1] + [p * rng.uniform(0.3, 3.0) for p in base[t + 1:]]
            if trial == 2:  # extreme: crash / spike
                pert = base[:t + 1] + [base[t] * (0.01 if t % 2 else 100.0)] * (len(base) - t - 1)
            ppos = fn(pert)
            assert ppos[:t + 1] == pos[:t + 1], f"{name}: signal at <= {t} changed when prices > {t} changed"
            checked += 1
    report(f"point-in-time signals {name}", f"{checked} perturbations, 0 changed", "n/a")


@pytest.mark.parametrize("name", STRATS)
def test_backtest_equity_up_to_t_ignores_future_prices(name, monkeypatch):
    base = walk(320, seed=11)
    captured = []
    real = backtest.compute_metrics

    def cap(equity, rets, pos, trades, gran, bh):
        captured.append((list(equity), list(rets), list(pos)))
        return real(equity, rets, pos, trades, gran, bh)

    monkeypatch.setattr(backtest, "compute_metrics", cap)
    backtest.evaluate(base, name)
    eq0, rets0, pos0 = captured[-1]
    for t in (60, 150, 250):
        pert = base[:t + 1] + [p * 1.7 for p in base[t + 1:]]
        backtest.evaluate(pert, name)
        eq1, rets1, pos1 = captured[-1]
        assert pos1[:t + 1] == pos0[:t + 1]
        assert rets1[:t] == rets0[:t]          # return i spans close i -> i+1, so only i < t is knowable at t
        assert eq1[:t + 1] == eq0[:t + 1]
    report(f"point-in-time equity {name}", "equity/positions identical through t", "n/a")


def _leaky(closes):
    """Deliberate lookahead: long iff tomorrow is up. Used to prove the checks bite."""
    return [1 if i + 1 < len(closes) and closes[i + 1] > closes[i] else 0 for i in range(len(closes))]


def test_lookahead_detector_catches_planted_leak_and_it_shows_in_edge():
    base = walk(300, seed=5)
    t = 150
    pert = base[:t + 1] + [p * 0.5 for p in base[t + 1:]]
    assert _leaky(pert)[:t + 1] != _leaky(base)[:t + 1]  # detector would flag it
    m, _ = evaluate_pos(base, _leaky(base), fee_bps=0)
    bh = m["buy_hold_return_pct"]
    report("planted-lookahead sanity", f"return={m['total_return_pct']}%", f"buy&hold={bh}%")
    assert m["alpha_vs_hold_pct"] > 100  # absurd edge = the signature of a leak


def test_position_realized_into_next_bar_only():
    """pos[i] earns the i->i+1 return, never the (i-1)->i return."""
    closes = [100, 110, 100, 120, 120]
    m, _ = evaluate_pos(closes, [0, 1, 0, 0, 0], fee_bps=0)  # long only over 110->100
    assert m["total_return_pct"] == round((100 / 110 - 1) * 100, 2)
    m, _ = evaluate_pos(closes, [1, 0, 0, 0, 0], fee_bps=0)  # long only over 100->110
    assert m["total_return_pct"] == 10.0


# --------------------------------------------------- (3) placebo / benchmark

def _edge(closes, pos):
    m, _ = evaluate_pos(closes, pos, fee_bps=0)
    return m["alpha_vs_hold_pct"]


def test_buy_hold_strategy_matches_benchmark_zero_edge():
    for seed in range(5):
        cl = walk(300, seed)
        m, _ = backtest.evaluate(cl, "buy_hold", fee_bps=0)
        assert m["alpha_vs_hold_pct"] == pytest.approx(0, abs=0.02)
    report("buy_hold vs benchmark", "alpha=0.0", "SPY-like synthetic buy&hold")


def test_random_signal_has_no_edge_on_driftless_walks():
    """Signal-free coin-flip strategy: mean edge vs buy&hold across many paths ~ 0 (|t| < 3.5)."""
    edges = []
    for seed in range(300):
        cl = walk(250, seed, drift=0.0)
        rng = random.Random(10_000 + seed)
        edges.append(_edge(cl, [rng.randint(0, 1) for _ in cl]))
    mean, sd = statistics.mean(edges), statistics.stdev(edges)
    t = mean / (sd / math.sqrt(len(edges)))
    report("random coin-flip signal", f"mean alpha={mean:.2f}% (t={t:.2f}, n={len(edges)})", "buy&hold alpha=0")
    assert abs(t) < 3.5


def test_shuffled_real_signals_destroy_any_edge_and_leak_beats_shuffle():
    """Shuffle each real strategy's positions in time: edge should be ~ random. The planted-leak
    signal must beat its own shuffle by a wide margin (shows the placebo discriminates)."""
    shuf_edges, real_edges, leak_edges, leak_shuf = [], [], [], []
    for seed in range(100):
        cl = walk(300, seed, drift=0.0)
        rng = random.Random(seed)
        pos = REGISTRY["sma_cross"](cl)
        real_edges.append(_edge(cl, pos))
        s = pos[:]; rng.shuffle(s); shuf_edges.append(_edge(cl, s))
        lk = _leaky(cl)
        leak_edges.append(_edge(cl, lk))
        s = lk[:]; rng.shuffle(s); leak_shuf.append(_edge(cl, s))
    ms = statistics.mean(shuf_edges)
    t = ms / (statistics.stdev(shuf_edges) / math.sqrt(len(shuf_edges)))
    report("sma_cross on driftless noise",
           f"mean alpha={statistics.mean(real_edges):.2f}%",
           f"shuffled-signal mean alpha={ms:.2f}% (t={t:.2f})")
    report("planted leak", f"mean alpha={statistics.mean(leak_edges):.1f}%",
           f"shuffled mean alpha={statistics.mean(leak_shuf):.1f}%")
    assert abs(t) < 3.5
    assert statistics.mean(leak_edges) > statistics.mean(leak_shuf) + 50


def test_walk_forward_oos_edge_on_noise_is_not_positive_on_average(monkeypatch):
    """End-to-end: tune on train, test OOS on pure noise. No real edge exists, so mean OOS alpha
    must not be reliably positive (would indicate leakage between train and test)."""
    oos = []
    for seed in range(40):
        cl = walk(600, seed, drift=0.0)
        bars = [{"t": i, "c": c} for i, c in enumerate(cl)]
        monkeypatch.setattr(validate, "get_bars", lambda *a, _b=bars, **k: _b)
        for strat in ("sma_cross", "ts_momentum", "donchian"):
            r = validate.validate("SYN", strat, fee_bps=0)
            oos.append(r["out_of_sample"]["alpha_vs_hold_pct"])
    mean = statistics.mean(oos)
    t = mean / (statistics.stdev(oos) / math.sqrt(len(oos)))
    report("walk-forward OOS on pure noise", f"mean OOS alpha={mean:.2f}% (t={t:.2f}, n={len(oos)})",
           "buy&hold alpha=0")
    assert t < 3.0
