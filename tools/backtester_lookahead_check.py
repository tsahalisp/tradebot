#!/usr/bin/env python3
"""Read-only point-in-time harness for the backtester entry point(s).

A backtester under test is `fn(closes, signal[, fee_bps]) -> (metrics, trades)` (the
shape of backend/backtest.py:evaluate_pos). `signal[i]` is the 0/1 position the caller
claims to know at the close of bar i; a correct backtester must not let it earn bar i's
own return. metrics needs "total_return_pct"; trades are dicts with exit_i / pnl_pct
(and "open": True for unclosed trades).

Probes (all on fixed-seed synthetic prices written to a temp dir only):
  shift       signal = next-day return sign (deliberate look-ahead). A backtester that
              enforces a lag shows no edge; a large positive edge is flagged.
  truncation  closed trades up to T must be identical with and without data after T.
  placebo     random fixed-seed signals must show ~0 edge vs buy-and-hold (zero-drift
              series, averaged over several seeds, no fees).
Fewer than MIN_OBS observations -> 'insufficient data' flag, nothing computed.
Nothing is skipped silently: import failures and exceptions are reported as ERROR rows.
Exit code 0 = all probes clean, 1 = any flag / error (stderr names function + probe).
"""
import argparse
import csv
import importlib
import inspect
import math
import random
import sys
import tempfile
from pathlib import Path

MIN_OBS = 20
SHIFT_EDGE_PCT = 25.0      # leaky signal beating this (pct points) => look-ahead
PLACEBO_TOL_PCT = 10.0     # |mean placebo edge vs buy&hold| must stay under this
PLACEBO_SEEDS = 20
PROBES = ("shift", "truncation", "placebo")
DEFAULT_TARGETS = ("backtest:evaluate_pos",)
BACKEND = Path(__file__).resolve().parents[1] / "backend"


def synth_closes(n, seed, mu=0.0003, sigma=0.01, start=100.0):
    rng = random.Random(seed)
    px, out = start, []
    for _ in range(n):
        px *= math.exp(rng.gauss(mu, sigma))
        out.append(px)
    return out


def write_and_load(closes, path):
    """Round-trip the synthetic series through a CSV in the temp workdir."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["i", "close"])
        for i, c in enumerate(closes):
            w.writerow([i, repr(c)])
    with open(path, newline="") as f:
        return [float(r["close"]) for r in csv.DictReader(f)]


def _call(fn, closes, signal, fee_bps):
    if "fee_bps" in inspect.signature(fn).parameters:
        return fn(closes, signal, fee_bps=fee_bps)
    return fn(closes, signal)


def _total(fn, closes, signal, fee_bps):
    m, _ = _call(fn, closes, signal, fee_bps)
    return float(m["total_return_pct"])


def probe_shift(fn, closes, fee_bps):
    leak = [1 if closes[i + 1] > closes[i] else 0 for i in range(len(closes) - 1)] + [0]
    edge = _total(fn, closes, leak, fee_bps)
    flags = []
    if edge > SHIFT_EDGE_PCT:
        flags.append(f"LOOK-AHEAD: next-day-return signal earned {edge:+.1f}% "
                     f"(> {SHIFT_EDGE_PCT}%); no 1-day lag enforced")
    return f"edge={edge:+.1f}%", flags


def _closed(trades, upto):
    return [(t["entry_i"], t["exit_i"], t["pnl_pct"]) for t in trades
            if not t.get("open") and t["exit_i"] <= upto]


def probe_truncation(fn, closes, fee_bps, seed):
    n = len(closes)
    rng = random.Random(seed)
    signal = [rng.randint(0, 1) for _ in range(n)]
    cuts = sorted({max(MIN_OBS, n * k // 4) for k in (2, 3)} | {n - 10})
    cuts = [t for t in cuts if MIN_OBS <= t < n - 1]
    flags = []
    for T in cuts:
        _, full = _call(fn, closes, signal, fee_bps)
        _, trunc = _call(fn, closes[:T + 1], signal[:T + 1], fee_bps)
        a, b = _closed(full, T - 1), _closed(trunc, T - 1)
        if a != b:
            flags.append(f"FUTURE DATA: results up to T={T} differ with/without later data "
                         f"({len(a)} vs {len(b)} closed trades)")
    return f"{len(cuts)} cut(s) checked", flags


def probe_placebo(fn, n, seed, fee_bps):
    edges = []
    for k in range(PLACEBO_SEEDS):
        closes = synth_closes(n, seed + 1000 + k, mu=0.0)
        rng = random.Random(seed + 2000 + k)
        sig = [rng.randint(0, 1) for _ in range(n)]
        bh = (closes[-1] / closes[0] - 1) * 100
        edges.append(_total(fn, closes, sig, fee_bps) - bh)
    mean = sum(edges) / len(edges)
    flags = []
    if abs(mean) > PLACEBO_TOL_PCT:
        flags.append(f"PLACEBO EDGE: random signal averages {mean:+.1f}% vs buy&hold "
                     f"(|x| > {PLACEBO_TOL_PCT}%)")
    return f"mean edge={mean:+.2f}% over {PLACEBO_SEEDS} seeds", flags


def run_probes(name, fn, workdir, n_obs=500, seed=42, fee_bps=0.0):
    """-> list of (probe, result, flags). Never skips: errors become flags."""
    if n_obs < MIN_OBS:
        return [(p, "not computed", [f"insufficient data: {n_obs} < {MIN_OBS} observations"])
                for p in PROBES]
    closes = write_and_load(synth_closes(n_obs, seed), Path(workdir) / "synthetic_prices.csv")
    runners = {
        "shift": lambda: probe_shift(fn, closes, fee_bps),
        "truncation": lambda: probe_truncation(fn, closes, fee_bps, seed),
        "placebo": lambda: probe_placebo(fn, n_obs, seed, fee_bps),
    }
    rows = []
    for p in PROBES:
        try:
            res, flags = runners[p]()
        except Exception as e:  # noqa: BLE001 - surfaced, never swallowed
            res, flags = "ERROR", [f"ERROR: {type(e).__name__}: {e}"]
        rows.append((p, res, flags))
    return rows


def load_target(spec):
    mod, _, func = spec.partition(":")
    if not func:
        raise ValueError(f"target '{spec}' must be module:function")
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    fn = getattr(importlib.import_module(mod), func)
    if not callable(fn):
        raise TypeError(f"{spec} is not callable")
    return fn


def check_target(spec, workdir, **kw):
    try:
        fn = load_target(spec)
    except Exception as e:  # noqa: BLE001
        msg = f"ENTRY POINT UNAVAILABLE: {type(e).__name__}: {e}"
        return [(p, "ERROR", [msg]) for p in PROBES]
    return run_probes(spec, fn, workdir, **kw)


def format_table(results):
    lines = [f"{'backtester':<28} {'probe':<11} {'result':<36} flags"]
    for spec, rows in results:
        for probe, res, flags in rows:
            lines.append(f"{spec:<28} {probe:<11} {res:<36} {'; '.join(flags) or 'none'}")
    return "\n".join(lines)


def main(argv=None, workdir=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", action="append", help="module:function (default backtest:evaluate_pos)")
    ap.add_argument("--n-obs", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fee-bps", type=float, default=0.0)
    a = ap.parse_args(argv)
    targets = a.target or list(DEFAULT_TARGETS)
    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory(prefix="lookahead_check_")
        workdir = tmp.name
    try:
        results = [(t, check_target(t, workdir, n_obs=a.n_obs, seed=a.seed, fee_bps=a.fee_bps))
                   for t in targets]
    finally:
        if tmp:
            tmp.cleanup()
    print(format_table(results))
    failed = [(t, p, f) for t, rows in results for p, _, fl in rows for f in fl]
    for t, p, f in failed:
        print(f"FAIL {t} probe={p}: {f}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
