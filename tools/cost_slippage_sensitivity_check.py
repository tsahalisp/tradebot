"""Read-only transaction-cost / slippage sensitivity check (numpy only).

Input per strategy (CSV with header, or JSON {col: [..]}): daily columns
  gross      daily gross return (before any cost)            [required]
  turnover   daily one-way turnover (fraction of NAV traded) [required]
  benchmark  SPY-like daily return (or pass --benchmark FILE) [required]
  net        returns the strategy actually reports            [optional]
  cost       daily cost the strategy actually charged         [optional]
Net return at cost level c bps = gross - c/1e4 * turnover. For each level we report
net Sharpe and annualised excess return vs the benchmark. Exit 1 on any failure.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ANN = 252
MIN_OBS = 20
DEFAULT_GRID = (0, 5, 10, 25, 50)
DEFAULT_REALISTIC_BPS = 10.0


class InputError(Exception):
    pass


def load_columns(path):
    """Load a CSV/JSON file into {column: list}. Raises InputError (never returns partial)."""
    p = Path(path)
    if not p.is_file():
        raise InputError(f"MISSING_FILE: {p} not found")
    try:
        text = p.read_text()
        if p.suffix.lower() == ".json":
            data = json.loads(text)
            if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
                raise ValueError("JSON must be an object of column -> list")
            return data
        rows = list(csv.DictReader(text.splitlines()))
        if not rows:
            raise ValueError("CSV has no data rows")
        cols = {k.strip(): [] for k in rows[0]}
        for r in rows:
            if None in r or any(v is None for v in r.values()):
                raise ValueError("ragged CSV row")
            for k, v in r.items():
                cols[k.strip()].append(float(v) if v.strip() != "" else float("nan"))
        return cols
    except (ValueError, OSError, UnicodeDecodeError) as e:  # JSONDecodeError is a ValueError
        raise InputError(f"MALFORMED_FILE: {p}: {e}")


def _arr(cols, key):
    try:
        return np.asarray(cols[key], dtype=float)
    except (TypeError, ValueError) as e:
        raise InputError(f"MALFORMED_FILE: column '{key}' not numeric: {e}")


def sharpe(r):
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(ANN)) if sd > 0 else 0.0


def analyze(name, gross, turnover, benchmark, net=None, cost=None,
            grid=DEFAULT_GRID, realistic_bps=DEFAULT_REALISTIC_BPS):
    """Return {name, rows, breakeven_bps, flags}. Flags are strings 'CODE: detail'."""
    res = {"name": name, "rows": [], "breakeven_bps": None, "flags": []}
    series = {"gross": gross, "turnover": turnover, "benchmark": benchmark}
    if net is not None:
        series["net"] = net
    if cost is not None:
        series["cost"] = cost
    arrs = {k: np.asarray(v, dtype=float) for k, v in series.items()}
    lens = {k: len(v) for k, v in arrs.items()}
    if len(set(lens.values())) > 1:
        res["flags"].append(f"INVALID_INPUT: length mismatch {lens}")
        return res
    if lens["gross"] == 0:
        res["flags"].append("INVALID_INPUT: empty series")
        return res
    for k, v in arrs.items():
        if not np.isfinite(v).all():
            res["flags"].append(f"INVALID_INPUT: NaN/inf in {k}")
    if (arrs["turnover"] < 0).any():
        res["flags"].append("INVALID_INPUT: negative turnover")
    if res["flags"]:
        return res
    n = lens["gross"]
    if n < MIN_OBS:
        res["flags"].append(f"INSUFFICIENT_DATA: {n} observations < {MIN_OBS}; not computed")
        return res
    g, t, b = arrs["gross"], arrs["turnover"], arrs["benchmark"]
    if t.sum() > 0:
        if "cost" in arrs and not (arrs["cost"] > 0).any():
            res["flags"].append("NO_COST_MODELED: cost column is all zero but turnover > 0")
        elif "cost" not in arrs and "net" in arrs and np.allclose(arrs["net"], g):
            res["flags"].append("NO_COST_MODELED: reported returns equal gross with turnover > 0 and no cost column")
    mt = t.mean()
    gap = float((g - b).mean())
    if mt > 0:
        res["breakeven_bps"] = max(gap / mt * 1e4, 0.0)
    elif gap <= 0:
        res["breakeven_bps"] = 0.0  # no cost needed to erase a non-positive edge
    # else: zero turnover and positive edge -> costs never erase it (None = infinite)
    for c in grid:
        r = g - c / 1e4 * t
        res["rows"].append({"cost_bps": float(c), "sharpe": sharpe(r),
                            "excess": float((r - b).mean() * ANN), "flags": []})
    for row in res["rows"]:
        if row["excess"] <= 0:
            row["flags"].append("VANISHED")
    be = res["breakeven_bps"]
    if be is not None and be <= realistic_bps:
        res["flags"].append(f"EDGE_VANISHES: net excess <= 0 at {be:.2f} bps "
                            f"<= realistic {realistic_bps:g} bps (break-even {be:.2f} bps)")
    return res


def analyze_file(path, benchmark_path=None, **kw):
    name = Path(path).stem
    try:
        cols = load_columns(path)
        for k in ("gross", "turnover"):
            if k not in cols:
                raise InputError(f"MALFORMED_FILE: {path}: missing column '{k}'")
        if benchmark_path:
            bcols = load_columns(benchmark_path)
            if "benchmark" not in bcols:
                raise InputError(f"MALFORMED_FILE: {benchmark_path}: missing column 'benchmark'")
            bench = _arr(bcols, "benchmark")
        elif "benchmark" in cols:
            bench = _arr(cols, "benchmark")
        else:
            raise InputError(f"MALFORMED_FILE: {path}: missing column 'benchmark' (and no --benchmark)")
        return analyze(name, _arr(cols, "gross"), _arr(cols, "turnover"), bench,
                       net=_arr(cols, "net") if "net" in cols else None,
                       cost=_arr(cols, "cost") if "cost" in cols else None, **kw)
    except InputError as e:
        return {"name": name, "rows": [], "breakeven_bps": None, "flags": [str(e)]}


def format_result(res):
    out = [f"== {res['name']} =="]
    if res["rows"]:
        out.append(f"{'cost_bps':>9} {'net_sharpe':>11} {'excess_ann':>11}  flags")
        for r in res["rows"]:
            out.append(f"{r['cost_bps']:9.1f} {r['sharpe']:11.3f} {r['excess']:11.4f}  {','.join(r['flags'])}")
        be = res["breakeven_bps"]
        out.append("break-even cost: " + ("none (zero turnover, positive edge)" if be is None else f"{be:.2f} bps"))
    for f in res["flags"]:
        out.append(f"FLAG {f}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", help="strategy CSV/JSON files")
    ap.add_argument("--benchmark", help="CSV/JSON with a 'benchmark' column (else read from each file)")
    ap.add_argument("--grid", default=",".join(map(str, DEFAULT_GRID)), help="cost levels in bps")
    ap.add_argument("--realistic-bps", type=float, default=DEFAULT_REALISTIC_BPS)
    a = ap.parse_args(argv)
    try:
        grid = [float(x) for x in a.grid.split(",")]
    except ValueError:
        print("FAIL: --grid must be comma-separated numbers", file=sys.stderr)
        return 2
    failed = []
    for f in a.files:
        res = analyze_file(f, a.benchmark, grid=grid, realistic_bps=a.realistic_bps)
        print(format_result(res))
        for flag in res["flags"]:
            failed.append(f"{res['name']}: {flag.split(':')[0]}")
    if failed:
        print("FAIL: " + "; ".join(failed), file=sys.stderr)
        return 1
    print("OK: all strategies passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
