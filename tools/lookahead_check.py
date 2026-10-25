#!/usr/bin/env python3
"""Read-only point-in-time (lookahead) leakage check for signal functions.

For each signal f(closes, **params) -> position list, on a synthetic price history:
  1. LEAK:     truncate closes at t, recompute, compare pos[t] with the full-history run.
  2. SAME_BAR: flag signals that depend on close[t] when the executor would trade that
               same bar (pos[t] earning bar t's own return). Probed on the real executor
               (backtest.evaluate_pos) so a regression there is caught too.
  3. ERROR:    import failure, missing function, exception, bad output length/type.
Nothing is ever skipped silently: every target gets a row. Exit 1 on any non-OK row,
naming module.function. Synthetic data only; no network, no strategy changes.

Usage: python tools/lookahead_check.py [--seed 7] [--bars 500] [--step 5]
                                       [--target module:func ...]
"""
import argparse
import importlib
import math
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def synthetic_closes(seed=7, n=500, drift=0.0003, vol=0.01, start=100.0):
    rng = random.Random(seed)
    px, out = start, []
    for _ in range(n):
        px *= math.exp(drift + vol * rng.gauss(0, 1))
        out.append(px)
    return out


def _same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is b
    try:
        if math.isnan(a) and math.isnan(b):
            return True
    except TypeError:
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _run(fn, closes):
    out = list(fn(list(closes)))
    if len(out) != len(closes):
        raise ValueError(f"returned {len(out)} values for {len(closes)} bars")
    return out


def find_leaks(fn, closes, step=5):
    """Return bars t where pos[t] from closes[:t+1] != pos[t] from the full history."""
    full = _run(fn, closes)
    n = len(closes)
    ts = sorted(set(range(0, n, max(1, step))) | {n - 1})
    return [t for t in ts if not _same(_run(fn, closes[:t + 1])[t], full[t])]


def uses_same_bar_close(fn, closes, step=5):
    """True if pos[t] changes when ONLY close[t] is perturbed (signal reads the bar's close)."""
    base = _run(fn, closes)
    n = len(closes)
    for t in sorted(set(range(0, n, max(1, step))) | {n - 1}):
        for mult in (0.5, 2.0):
            c = list(closes[:t + 1])
            c[t] *= mult
            if not _same(_run(fn, c)[t], base[t]):
                return True
    return False


def executor_realizes_same_bar(evaluate_pos):
    """Probe an executor: flat prices, one +10% jump at bar j. If holding only at bar j-1
    earns the jump, execution is next-bar (safe); if holding only at bar j earns it, the
    executor trades the same bar's close (flagged)."""
    n, j = 12, 6
    closes = [100.0] * n
    for i in range(j, n):
        closes[i] = 110.0
    def total(pos):
        return evaluate_pos(closes, pos, 0.0)[0]["total_return_pct"]
    prev_bar = total([1 if i == j - 1 else 0 for i in range(n)])
    same_bar = total([1 if i == j else 0 for i in range(n)])
    return same_bar > 1.0 and prev_bar < 1.0


def edge(closes, pos):
    """Mean next-bar return when long minus when flat, per bar (pos[t] earns t->t+1)."""
    on, off = [], []
    for t in range(len(closes) - 1):
        (on if pos[t] else off).append(closes[t + 1] / closes[t] - 1.0)
    if not on or not off:
        return 0.0
    return sum(on) / len(on) - sum(off) / len(off)


def pit_positions(fn, closes):
    """Point-in-time rebuild: pos[t] = fn(closes[:t+1])[t]. Leak-free by construction."""
    return [_run(fn, closes[:t + 1])[t] for t in range(len(closes))]


def check_target(module, name, fn, closes, step=5, same_bar_executor=False):
    """-> (status, detail). status in OK | LEAK | SAME_BAR | ERROR."""
    try:
        leaks = find_leaks(fn, closes, step)
        if leaks:
            return "LEAK", f"pos[t] differs on truncation at {len(leaks)} bars, first t={leaks[0]}"
        if same_bar_executor and uses_same_bar_close(fn, closes, step):
            return "SAME_BAR", "signal reads close[t] and executor trades bar t at that close"
        return "OK", ""
    except Exception as e:  # never skip: surface it
        return "ERROR", f"{type(e).__name__}: {e}"


def _bind(fn, kwargs):
    return lambda c: fn(c, **kwargs)


def default_targets():
    """[(module, name, resolver)] where resolver() -> callable or raises. REGISTRY is
    enumerated dynamically so new strategies are checked automatically."""
    sys.path.insert(0, str(BACKEND))
    targets = []
    try:
        strategies = importlib.import_module("strategies")
        for key in sorted(strategies.REGISTRY):
            targets.append(("strategies", key, (lambda k=key: strategies.REGISTRY[k])))
    except Exception as e:
        def boom(e=e):
            raise e
        targets.append(("strategies", "REGISTRY", boom))

    def model_pos():
        m = importlib.import_module("model")
        return lambda c: m._positions(c, {"entry": "sma_cross", "trend_filter": True})
    targets.append(("model", "_positions(trend_filter)", model_pos))
    return targets


def parse_target(spec):
    mod, _, fn = spec.partition(":")
    def resolve():
        sys.path.insert(0, str(BACKEND))
        return getattr(importlib.import_module(mod), fn)
    return (mod, fn, resolve)


def run(targets, closes, step=5, executor_same_bar=None):
    """-> list of (module, name, status, detail)."""
    rows = []
    if executor_same_bar is None:
        try:
            sys.path.insert(0, str(BACKEND))
            executor_same_bar = executor_realizes_same_bar(importlib.import_module("backtest").evaluate_pos)
        except Exception as e:
            rows.append(("backtest", "evaluate_pos", "ERROR", f"{type(e).__name__}: {e}"))
            executor_same_bar = False
    elif executor_same_bar:
        rows.append(("backtest", "evaluate_pos", "SAME_BAR", "executor realizes pos[t] into bar t"))
    for mod, name, resolve in targets:
        try:
            fn = resolve()
            if not callable(fn):
                raise TypeError("not callable")
        except Exception as e:
            rows.append((mod, name, "ERROR", f"{type(e).__name__}: {e}"))
            continue
        status, detail = check_target(mod, name, fn, closes, step, bool(executor_same_bar))
        rows.append((mod, name, status, detail))
    return rows


def format_table(rows):
    head = ("MODULE", "FUNCTION", "STATUS", "DETAIL")
    data = [head] + [tuple(r) for r in rows]
    w = [max(len(str(r[i])) for r in data) for i in range(3)]
    return "\n".join(f"{r[0]:<{w[0]}}  {r[1]:<{w[1]}}  {r[2]:<{w[2]}}  {r[3]}" for r in data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--bars", type=int, default=500)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--target", action="append", default=[], help="module:func (replaces defaults)")
    a = ap.parse_args(argv)
    targets = [parse_target(s) for s in a.target] or default_targets()
    rows = run(targets, synthetic_closes(a.seed, a.bars), a.step)
    print(format_table(rows))
    bad = [r for r in rows if r[2] != "OK"]
    for m, n, s, d in bad:
        print(f"FAIL {s}: {m}.{n} {d}", file=sys.stderr)
    print(f"\n{len(rows) - len(bad)}/{len(rows)} OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
