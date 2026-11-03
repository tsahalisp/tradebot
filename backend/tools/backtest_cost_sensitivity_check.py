"""Read-only transaction-cost / execution-delay sensitivity check.

Takes a strategy's daily returns + turnover (JSON or CSV, or a synthetic fixture),
recomputes net performance on a grid of per-unit-turnover costs, a 1-day execution
delay, the break-even cost (alpha vs SPY -> 0) and a fixed-seed random placebo with
the same trade timing. Imports no repo module, touches no network/DB; never modifies
strategy logic. Exit 1 (naming strategy + check) on any failure; nothing is skipped
silently.

JSON: {"spy": [...], "strategies": {"name": {"returns": [...], "turnover": [...],
       "delayed_returns": [...]?, "spy": [...]?}}}
CSV : columns returns,turnover,spy[,delayed_returns][,strategy]  (no strategy column
      -> one strategy named after the file stem)

delayed_returns = the same strategy re-run with signals executed 1 day late (from the
backtester). Returns+turnover alone cannot reconstruct a delay, so without it the delay
column is reported "n/a (no delayed_returns)" - visibly, not as a pass.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

DAYS = 252
MIN_OBS = 20
COSTS_BPS = (0, 5, 10, 25)
ALPHA_FLAG_BPS = 10
DELAY_MAX_LOSS = 0.5
PLACEBO_SEED = 12345
PLACEBO_DRAWS = 50   # alpha averaged over fixed seeds SEED..SEED+49 (one draw is too noisy)


class InputError(Exception):
    pass


# ---------------- loading ----------------
def _series(v, what):
    try:
        a = np.array(v, dtype=float)
    except (TypeError, ValueError):
        raise ValueError(f"{what} is not a numeric list")
    if a.ndim != 1:
        raise ValueError(f"{what} is not a 1-d list")
    return a


def _build(name, returns, turnover, spy, delayed):
    """-> dict(name, returns, turnover, spy, delayed, errors). Never raises."""
    s = {"name": name, "returns": None, "turnover": None, "spy": None,
         "delayed": None, "errors": []}
    for key, raw, req in (("returns", returns, True), ("turnover", turnover, False),
                          ("spy", spy, True), ("delayed", delayed, False)):
        if raw is None:
            if req:
                s["errors"].append(("missing_" + key, f"{key} series missing"))
            continue
        try:
            s[key] = _series(raw, key)
        except ValueError as e:
            s["errors"].append(("malformed_" + key, str(e)))
    for key in ("returns", "spy", "delayed"):
        a = s[key]
        if a is not None and not np.all(np.isfinite(a)):
            s["errors"].append(("malformed_" + key, f"{key} has NaN/inf"))
    n = len(s["returns"]) if s["returns"] is not None else None
    for key in ("turnover", "spy", "delayed"):
        a = s[key]
        if n is not None and a is not None and len(a) != n:
            s["errors"].append(("length_mismatch", f"{key} has {len(a)} rows, returns {n}"))
    return s


def load(path):
    p = Path(path)
    if not p.is_file():
        raise InputError(f"file not found: {path}")
    try:
        text = p.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"unreadable: {e}")
    if p.suffix.lower() == ".json":
        try:
            d = json.loads(text)
        except json.JSONDecodeError as e:
            raise InputError(f"invalid JSON: {e}")
        if not isinstance(d, dict) or not isinstance(d.get("strategies"), dict) or not d["strategies"]:
            raise InputError('JSON needs a non-empty "strategies" object')
        out = []
        for name, v in d["strategies"].items():
            if not isinstance(v, dict):
                raise InputError(f"strategy {name!r} is not an object")
            out.append(_build(name, v.get("returns"), v.get("turnover"),
                              v.get("spy", d.get("spy")), v.get("delayed_returns")))
        return out
    if p.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
        if not rows or "returns" not in rows[0]:
            raise InputError("CSV needs a header with a 'returns' column and >=1 row")
        groups = {}
        for r in rows:
            groups.setdefault(r.get("strategy") or p.stem, []).append(r)
        out = []
        for name, rs in groups.items():
            def col(c):
                if c not in rs[0]:
                    return None
                return [x[c] if x[c] not in ("", None) else "nan" for x in rs]
            out.append(_build(name, col("returns"), col("turnover"), col("spy"),
                              col("delayed_returns")))
        return out
    raise InputError(f"unsupported extension {p.suffix!r} (use .json or .csv)")


# ---------------- maths ----------------
def ann_return(r):
    g = np.prod(1.0 + r)
    return float(g ** (DAYS / len(r)) - 1.0) if g > 0 else -1.0


def sharpe(r):
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(DAYS)) if sd > 1e-12 else float("nan")


def beta_of(r, spy):
    v = spy.var(ddof=1)
    return float(np.cov(r, spy, ddof=1)[0, 1] / v) if v > 1e-18 else 0.0


def alpha(r, spy, beta):
    """Annualised arithmetic alpha vs SPY at a fixed beta."""
    return float((r.mean() - beta * spy.mean()) * DAYS)


def net(r, turnover, bps):
    return r - bps / 1e4 * turnover


def placebo_returns(spy, turnover, seed=PLACEBO_SEED):
    """Random long/flat exposure that flips (p=0.5) only on the strategy's trade days."""
    rng = np.random.default_rng(seed)
    exp, e = np.empty(len(spy)), 1.0
    for t in range(len(spy)):
        if t > 0 and turnover[t] > 0 and rng.random() < 0.5:
            e = 1.0 - e
        exp[t] = e
    return exp * spy


def analyse(s, costs=COSTS_BPS):
    """-> dict(name, rows, breakeven_bps, delay, placebo, flags[(check, detail)])."""
    res = {"name": s["name"], "rows": [], "breakeven_bps": None, "delay": None,
           "placebo": [], "flags": list(s["errors"])}
    r, spy, tn = s["returns"], s["spy"], s["turnover"]
    if r is not None and len(r) < MIN_OBS:
        res["flags"].append(("insufficient_data", f"{len(r)} observations < {MIN_OBS}; not computed"))
        res["insufficient"] = True
        return res
    if tn is None:
        res["flags"].append(("turnover", "turnover series missing"))
    elif not np.all(np.isfinite(tn)) or (tn < 0).any():
        res["flags"].append(("turnover", "turnover has negative or non-finite values"))
    if res["flags"]:
        return res
    beta = beta_of(r, spy)
    a_gross = alpha(r, spy, beta)
    for c in costs:
        rn = net(r, tn, c)
        res["rows"].append({"bps": c, "ann": ann_return(rn), "sharpe": sharpe(rn),
                            "alpha": alpha(rn, spy, beta)})
    mt = float(tn.mean())
    if a_gross <= 0:
        res["breakeven_bps"] = 0.0
    else:
        res["breakeven_bps"] = float("inf") if mt <= 0 else a_gross / (DAYS * mt) * 1e4
    if a_gross <= 0 or alpha(net(r, tn, ALPHA_FLAG_BPS), spy, beta) < 0:
        res["flags"].append(("negative_alpha_at_10bps",
                             f"alpha vs SPY <0 at <={ALPHA_FLAG_BPS} bps (gross {a_gross:+.2%}, "
                             f"break-even {res['breakeven_bps']:.1f} bps)"))
    if s["delayed"] is not None:
        ad = alpha(s["delayed"], spy, beta_of(s["delayed"], spy))
        loss = (a_gross - ad) / a_gross if a_gross > 0 else float("nan")
        res["delay"] = {"alpha": ad, "loss": loss}
        if a_gross > 0 and loss > DELAY_MAX_LOSS:
            res["flags"].append(("delay_sensitive",
                                 f"1-day delay removes {loss:.0%} of gross alpha (>{DELAY_MAX_LOSS:.0%})"))
    draws = [placebo_returns(spy, tn, PLACEBO_SEED + k) for k in range(PLACEBO_DRAWS)]
    betas = [beta_of(pr, spy) for pr in draws]
    res["placebo"] = [{"bps": c, "alpha": float(np.mean(
        [alpha(net(pr, tn, c), spy, b) for pr, b in zip(draws, betas)]))} for c in costs]
    return res


# ---------------- reporting ----------------
def _f(x, pct=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.2%}" if pct else f"{x:+.2f}"


def render(results):
    lines = [f"{'strategy':<18}{'cost bps':>9}{'ann ret':>10}{'sharpe':>8}{'alpha':>9}{'placebo a':>11}"]
    for res in results:
        if not res["rows"]:
            lines.append(f"{res['name']:<18}{'-- not computed: ' + ', '.join(c for c, _ in res['flags'])}")
            continue
        for row, pb in zip(res["rows"], res["placebo"]):
            lines.append(f"{res['name']:<18}{row['bps']:>9}{_f(row['ann']):>10}{_f(row['sharpe'], False):>8}"
                         f"{_f(row['alpha']):>9}{_f(pb['alpha']):>11}")
        be = res["breakeven_bps"]
        d = res["delay"]
        dtxt = "n/a (no delayed_returns)" if d is None else f"alpha {_f(d['alpha'])}, loss {_f(d['loss'])}"
        lines.append(f"{'':<18}break-even cost: {'inf' if be == float('inf') else f'{be:.1f}'} bps | 1-day delay: {dtxt}")
    return "\n".join(lines)


def run(path, costs=COSTS_BPS, out=print):
    """-> (results, failures[(strategy, check, detail)])."""
    try:
        strategies = load(path)
    except InputError as e:
        out(f"FAIL {Path(str(path)).name}: input_file: {e}")
        return [], [(str(path), "input_file", str(e))]
    return run_strategies(strategies, costs, out)


def run_strategies(strategies, costs=COSTS_BPS, out=print):
    results = [analyse(s, costs) for s in strategies]
    out(render(results))
    fails = [(r["name"], c, d) for r in results for c, d in r["flags"]]
    for n, c, d in fails:
        out(f"FAIL {n}: {c}: {d}")
    return results, fails


# ---------------- synthetic fixtures ----------------
def synthetic_spy(n=500, seed=1):
    return np.random.default_rng(seed).normal(0.0004, 0.01, n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help=".json or .csv input")
    ap.add_argument("--costs", default=",".join(map(str, COSTS_BPS)), help="bps grid")
    a = ap.parse_args(argv)
    try:
        costs = tuple(float(x) for x in a.costs.split(","))
    except ValueError:
        print(f"FAIL args: costs: bad --costs {a.costs!r}")
        return 1
    _, fails = run(a.file, costs)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
