#!/usr/bin/env python3
"""Read-only cross-check of max drawdown and turnover in the weekly paper report.

Inputs (all files, nothing is written, no network, no DB):
  <data_dir>/<book>_nav.csv     columns: ts,nav,spy      (same as autoportfolio.nav_history)
  <data_dir>/<book>_trades.csv  columns: ts,ticker,side,shares,price[,fee]  (as autoportfolio.trades)
  <report.json>  {"portfolios": {"<book>": {"max_drawdown_pct": -12.3, "turnover_pct": 85.0}}}
ts is epoch seconds or an ISO date/datetime; multiple NAV rows per day collapse to the last.

Definitions (independent recompute, same sign convention as backend/metrics.py):
  max drawdown %  = min over days of nav/running_peak - 1, times 100 (negative or 0)
  daily turnover  = sum(shares*price) traded that day / that day's NAV (one-way, fraction)
  turnover %      = total traded notional / mean NAV * 100
Checks: drawdown_mismatch, turnover_mismatch, turnover_implausible (negative or >100% in a day),
base_date_inconsistent (reported DD shallower than SPY's while NAV trough is deeper),
insufficient_data (<20 observations; nothing computed), bad_input (missing/malformed file).
Exit 1 if any portfolio has any failure; 2 on usage errors.
"""
import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

MIN_OBS = 20
MAX_DAILY_TURNOVER = 1.0


class BadInput(Exception):
    pass


def _day(ts):
    s = str(ts).strip()
    try:
        v = float(s)
        if not math.isfinite(v):
            raise ValueError
        return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        raise BadInput(f"unparseable ts {s!r}")


def _num(row, col, path, line, positive=False):
    try:
        v = float(row[col])
    except (KeyError, TypeError, ValueError):
        raise BadInput(f"{path.name} line {line}: bad {col} {row.get(col)!r}")
    if not math.isfinite(v) or (positive and v <= 0):
        raise BadInput(f"{path.name} line {line}: invalid {col} {v}")
    return v


def _rows(path, required):
    if not path.is_file():
        raise BadInput(f"missing file {path}")
    try:
        with path.open(newline="") as f:
            rd = csv.DictReader(f)
            missing = [c for c in required if c not in (rd.fieldnames or [])]
            if missing:
                raise BadInput(f"{path.name}: missing columns {missing}")
            return list(enumerate(rd, start=2))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise BadInput(f"{path.name}: unreadable ({e})")


def load_nav(path):
    """-> sorted [(day, nav, spy)], last row per day wins."""
    by_day = {}
    for line, r in _rows(path, ["ts", "nav", "spy"]):
        by_day[_day(r["ts"])] = (_num(r, "nav", path, line, True), _num(r, "spy", path, line, True))
    return [(d, *by_day[d]) for d in sorted(by_day)]


def load_trades(path):
    """-> {day: signed traded notional shares*price}. Negative values are kept so they can be flagged."""
    out = {}
    for line, r in _rows(path, ["ts", "shares", "price"]):
        n = _num(r, "shares", path, line) * _num(r, "price", path, line)
        d = _day(r["ts"])
        out[d] = out.get(d, 0.0) + n
    return out


def max_drawdown_pct(series):
    peak, mdd = series[0], 0.0
    for v in series:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd * 100


def load_report(path):
    try:
        data = json.loads(Path(path).read_text())
        ports = data["portfolios"]
        if not isinstance(ports, dict):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BadInput(f"report {path}: missing or malformed ({type(e).__name__}: {e})")
    return ports


def check_portfolio(name, nav_path, trades_path, rep, tol):
    """Return (row dict, [failed check names]). row has computed values or None."""
    row = {"portfolio": name, "obs": 0, "dd": None, "rep_dd": None, "spy_dd": None,
           "turn": None, "rep_turn": None, "alpha": None}
    fails = []
    try:
        nav = load_nav(nav_path)
        trades = load_trades(trades_path)
    except BadInput as e:
        row["note"] = str(e)
        return row, ["bad_input"]
    row["obs"] = len(nav)
    if len(nav) < MIN_OBS:
        row["note"] = f"insufficient data ({len(nav)} < {MIN_OBS} obs), not computed"
        return row, ["insufficient_data"]
    try:
        rep_dd = float(rep["max_drawdown_pct"])
        rep_turn = float(rep["turnover_pct"])
    except (KeyError, TypeError, ValueError):
        row["note"] = "report entry missing/non-numeric max_drawdown_pct or turnover_pct"
        return row, ["bad_input"]

    days = {d: v for d, v, _ in nav}
    navs, spys = [v for _, v, _ in nav], [s for _, _, s in nav]
    dd, spy_dd = max_drawdown_pct(navs), max_drawdown_pct(spys)
    orphan = sorted(set(trades) - set(days))
    if orphan:
        row["note"] = f"trades on days without NAV: {orphan[:3]}"
        return row, ["bad_input"]
    daily = {d: n / days[d] for d, n in trades.items()}
    turn = sum(trades.values()) / (sum(navs) / len(navs)) * 100
    row.update(dd=dd, rep_dd=rep_dd, spy_dd=spy_dd, turn=turn, rep_turn=rep_turn,
               alpha=((navs[-1] / navs[0]) - (spys[-1] / spys[0])) * 100)

    notes = []
    if abs(dd - rep_dd) > tol:
        fails.append("drawdown_mismatch")
        notes.append(f"DD recomputed {dd:.2f} vs reported {rep_dd:.2f}")
    if abs(turn - rep_turn) > tol:
        fails.append("turnover_mismatch")
        notes.append(f"turnover recomputed {turn:.2f} vs reported {rep_turn:.2f}")
    bad = {d: t for d, t in daily.items() if t < 0 or t > MAX_DAILY_TURNOVER}
    if bad:
        d, t = min(bad.items())
        fails.append("turnover_implausible")
        notes.append(f"daily turnover {t * 100:.1f}% on {d}")
    if rep_dd > spy_dd + tol and dd < spy_dd - tol:
        fails.append("base_date_inconsistent")
        notes.append(f"reported DD {rep_dd:.2f} shallower than SPY {spy_dd:.2f} but NAV trough {dd:.2f}")
    row["note"] = "; ".join(notes)
    return row, fails


def run(data_dir, report_path, tol=0.1):
    """-> (rows, failures[(portfolio, check)]). Union of report entries and *_nav.csv / *_trades.csv files."""
    data_dir = Path(data_dir)
    failures, rows = [], []
    try:
        report = load_report(report_path)
    except BadInput as e:
        return [], [("<report>", "bad_input")], [str(e)]
    names = set(report)
    if data_dir.is_dir():
        for p in data_dir.iterdir():
            for suf in ("_nav.csv", "_trades.csv"):
                if p.name.endswith(suf):
                    names.add(p.name[: -len(suf)])
    else:
        return [], [("<data_dir>", "bad_input")], [f"data dir {data_dir} not found"]
    if not names:
        return [], [("<all>", "bad_input")], ["no portfolios found"]
    for name in sorted(names):
        if name not in report:
            row, fails = ({"portfolio": name, "obs": 0, "note": "present in data dir but absent from report"},
                          ["bad_input"])
        else:
            row, fails = check_portfolio(name, data_dir / f"{name}_nav.csv",
                                         data_dir / f"{name}_trades.csv", report[name], tol)
        row["status"] = "FAIL: " + ",".join(fails) if fails else "ok"
        rows.append(row)
        failures += [(name, f) for f in fails]
    return rows, failures, []


def _f(v):
    return "-" if v is None else f"{v:.2f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data_dir")
    ap.add_argument("report")
    ap.add_argument("--tol", type=float, default=0.1, help="tolerance in percentage points")
    a = ap.parse_args(argv)
    rows, failures, errors = run(a.data_dir, a.report, a.tol)
    cols = ["portfolio", "obs", "DD%", "repDD%", "spyDD%", "turn%", "repTurn%", "alpha%", "status"]
    table = [cols] + [[r["portfolio"], str(r["obs"]), _f(r.get("dd")), _f(r.get("rep_dd")), _f(r.get("spy_dd")),
                       _f(r.get("turn")), _f(r.get("rep_turn")), _f(r.get("alpha")), r["status"]] for r in rows]
    w = [max(len(x[i]) for x in table) for i in range(len(cols))]
    for x in table:
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(x)))
    for r in rows:
        if r.get("note"):
            print(f"  [{r['portfolio']}] {r['note']}")
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    for name, check in failures:
        print(f"FAIL {name}: {check}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
