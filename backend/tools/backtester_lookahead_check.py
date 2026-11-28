"""Read-only lookahead / placebo / input check for the harness's own backtester.

Runs backtest.evaluate_pos on a synthetic (seeded) price series, or on a closes CSV
(--csv, columns date,close), and flags:
  input        empty series, NaN/inf, non-positive prices, misaligned/unsorted dates
  lookahead_truncation  positions on a prefix differ from the same bars of the full run
  lookahead_shift       moving the signal 1 bar earlier/later changes Sharpe by far
                        more than expected (one-sided, > --max-shift-delta)
  placebo      pure-noise signals have mean Sharpe not near zero (|mean| > --placebo-tol)
  insufficient data     < 20 observations: reported, NOT computed
Exit codes: 0 clean, 1 flagged, 3 insufficient data. Nothing is ever skipped silently.
No network, no orders, no writes.
"""
import argparse
import csv
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest import evaluate_pos  # noqa: E402
from strategies import REGISTRY  # noqa: E402

MIN_OBS = 20


def synthetic_closes(n=500, seed=7, daily_vol=0.01, drift=0.0):
    """Seeded driftless (by default) geometric random walk, start 100."""
    rng = random.Random(seed)
    px, out = 100.0, []
    for _ in range(n):
        out.append(px)
        px *= math.exp(rng.gauss(drift, daily_vol))
    return out


def _sharpe(closes, pos, fee_bps):
    m, _ = evaluate_pos(closes, pos, fee_bps)
    return m["sharpe"]


def check_input(closes, dates=None):
    """-> list of (check, message). Empty list means clean."""
    flags = []
    if closes is None or len(closes) == 0:
        return [("input", "empty series")]
    for i, c in enumerate(closes):
        if not isinstance(c, (int, float)) or isinstance(c, bool) or not math.isfinite(c):
            flags.append(("input", f"non-finite/non-numeric close at index {i}: {c!r}"))
            break
    else:
        if any(c <= 0 for c in closes):
            flags.append(("input", "non-positive close"))
    if dates is not None:
        if len(dates) != len(closes):
            flags.append(("input", f"misaligned: {len(dates)} dates vs {len(closes)} closes"))
        elif any(b <= a for a, b in zip(dates, dates[1:])):
            flags.append(("input", "dates not strictly increasing (duplicate/unsorted)"))
    return flags


def check_truncation(signal_fn, closes, fracs=(0.5, 0.75)):
    flags = []
    full = list(signal_fn(closes))
    for f in fracs:
        k = int(len(closes) * f)
        part = list(signal_fn(closes[:k]))
        diff = [i for i in range(k) if full[i] != part[i]]
        if diff:
            flags.append(("lookahead_truncation",
                          f"position at {len(diff)} bar(s) (first idx {diff[0]}) changes when "
                          f"rows after idx {k - 1} are removed"))
    return flags


def check_shift(signal_fn, closes, fee_bps=10.0, max_delta=1.5):
    pos = list(signal_fn(closes))
    base = _sharpe(closes, pos, fee_bps)
    early = pos[1:] + [pos[-1]]   # signal known one bar sooner
    late = [0] + pos[:-1]         # signal one bar later
    flags = []
    # One-sided: moving the signal earlier should not GAIN much; delaying it should not
    # LOSE much. (A big loss from moving earlier is normal for e.g. mean reversion.)
    for name, p, sign in (("earlier", early, 1), ("later", late, -1)):
        d = _sharpe(closes, p, fee_bps) - base
        if sign * d > max_delta:
            flags.append(("lookahead_shift",
                          f"shifting signal 1 bar {name} moves Sharpe {base:+.2f} -> {base + d:+.2f} "
                          f"(delta {d:+.2f}, limit {max_delta})"))
    return flags


def noise_signal(rng, n, p_flip=0.02):
    """Sticky random 0/1 position (flips w.p. p_flip/bar) so fee drag stays realistic."""
    pos, cur = [], rng.randint(0, 1)
    for _ in range(n):
        if rng.random() < p_flip:
            cur = 1 - cur
        pos.append(cur)
    return pos


def check_placebo(closes, fee_bps=10.0, n_signals=40, seed=11, tol=0.5):
    """Sticky random 0/1 positions through the real backtester; mean Sharpe must be ~0."""
    rng = random.Random(seed)
    sh = [_sharpe(closes, noise_signal(rng, len(closes)), fee_bps) for _ in range(n_signals)]
    mean = sum(sh) / len(sh)
    if abs(mean) > tol:
        return [("placebo", f"noise-signal mean Sharpe {mean:+.2f} not near 0 (tol {tol}); "
                            f"backtester/fixture has a bias")], mean
    return [], mean


def run_checks(closes, signal_fn, dates=None, fee_bps=10.0, max_shift_delta=1.5, placebo_tol=0.5):
    """-> (status, flags, info). status: 'ok' | 'flagged' | 'insufficient data'."""
    flags = check_input(closes, dates)
    if flags:
        return "flagged", flags, {}
    if len(closes) < MIN_OBS:
        return "insufficient data", [("insufficient data",
                                      f"{len(closes)} observations < {MIN_OBS}; not computed")], {}
    pf, placebo = check_placebo(closes, fee_bps, tol=placebo_tol)
    flags = (check_truncation(signal_fn, closes)
             + check_shift(signal_fn, closes, fee_bps, max_shift_delta) + pf)
    return ("flagged" if flags else "ok"), flags, {"placebo_mean_sharpe": placebo}


def load_csv(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    dates = [r["date"] for r in rows]
    closes = []
    for r in rows:
        try:
            closes.append(float(r["close"]))
        except ValueError:
            closes.append(float("nan"))
    return dates, closes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategy", default="sma_cross", choices=sorted(REGISTRY))
    ap.add_argument("--csv", help="closes CSV (date,close); default: synthetic fixture")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--max-shift-delta", type=float, default=1.5)
    ap.add_argument("--placebo-tol", type=float, default=0.5)
    a = ap.parse_args(argv)

    if a.csv:
        src = a.csv
        dates, closes = load_csv(a.csv)
    else:
        src, dates, closes = f"synthetic(seed={a.seed},n={a.n})", None, synthetic_closes(a.n, a.seed)
    status, flags, info = run_checks(closes, REGISTRY[a.strategy], dates, a.fee_bps,
                                     a.max_shift_delta, a.placebo_tol)
    for check, msg in flags:
        print(f"FLAG file={src} check={check}: {msg}")
    print(f"{status.upper()} file={src} strategy={a.strategy} n={len(closes)} {info}")
    return {"ok": 0, "flagged": 1, "insufficient data": 3}[status]


if __name__ == "__main__":
    sys.exit(main())
