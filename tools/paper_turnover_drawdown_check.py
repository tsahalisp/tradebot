#!/usr/bin/env python3
"""Read-only check of paper-portfolio max drawdown and turnover (numpy only).

Independently recomputes max drawdown and annualised one-way turnover from each
portfolio's NAV series and daily position weights, and compares them with the
latest weekly report. Nothing is written; no network, DB or trading code is used.

Layout under ROOT (the harness stores only the *current* positions and trades in
SQLite, so daily weights must be exported to this layout):
  ROOT/report.json            {"portfolios": {"<name>": {"max_drawdown": -0.12, "turnover": 1.5}}}
  ROOT/<name>/nav.csv         date,nav,spy
  ROOT/<name>/weights.csv     date,<ticker>...[,cash]    (one row per day)
Max drawdown is a fraction (-0.12 = -12%, sign ignored on compare); turnover is
annualised one-way (0.5*sum|dw| per day * 252).

Exit code 0 only if every portfolio has no flags; otherwise 1, naming
"<portfolio>: <check>" on stderr. 'insufficient data' counts as a failure.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

MIN_OBS = 20
ANN = 252


class Bad(Exception):
    """Missing/malformed input; message becomes the flag detail."""


def max_drawdown(nav):
    nav = np.asarray(nav, float)
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def daily_turnover(w):
    """One-way turnover per day: 0.5*sum|w_t - w_{t-1}| (length len(w)-1)."""
    return 0.5 * np.abs(np.diff(np.asarray(w, float), axis=0)).sum(axis=1)


def annualised_turnover(w):
    return float(daily_turnover(w).mean() * ANN)


def _read(path, what):
    if not path.is_file():
        raise Bad(f"missing {what} file {path}")
    try:
        with path.open(newline="") as f:
            rows = list(csv.reader(f))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise Bad(f"unreadable {what} file {path}: {e}")
    if len(rows) < 2:
        raise Bad(f"{what} file {path} has no data rows")
    head = [h.strip().lower() for h in rows[0]]
    body = rows[1:]
    if any(len(r) != len(head) for r in body):
        raise Bad(f"{what} file {path} has ragged rows")
    if len(set(head)) != len(head) or head[0] != "date":
        raise Bad(f"{what} file {path} needs unique header starting with 'date'")
    dates = [r[0].strip() for r in body]
    try:
        vals = np.array([[float(x) for x in r[1:]] for r in body], float)
    except ValueError as e:
        raise Bad(f"{what} file {path} has non-numeric cell: {e}")
    return head[1:], dates, vals


def load_nav(d):
    cols, dates, vals = _read(d / "nav.csv", "nav")
    if "nav" not in cols or "spy" not in cols:
        raise Bad("nav.csv needs columns date,nav,spy")
    return dates, vals[:, cols.index("nav")], vals[:, cols.index("spy")]


def load_weights(d):
    cols, dates, vals = _read(d / "weights.csv", "positions")
    return cols, dates, vals


def check_portfolio(name, d, reported=None, *, ceiling=10.0, stall_days=20,
                    tol=0.01, long_only=True, min_obs=MIN_OBS):
    """Return dict(name, mdd, turnover, vs_spy, flags). Never raises on bad data."""
    flags, mdd, turn, vs_spy = [], None, None, None

    def flag(check, detail):
        flags.append(f"{check}: {detail}")

    if not d.is_dir():
        flag("missing-portfolio", f"no directory {d}")
        return dict(name=name, mdd=None, turnover=None, vs_spy=None, flags=flags)
    nav = spy = ndates = wdates = w = cols = None
    try:
        ndates, nav, spy = load_nav(d)
    except Bad as e:
        flag("malformed-file" if "missing" not in str(e) else "missing-file", e)
    try:
        cols, wdates, w = load_weights(d)
    except Bad as e:
        flag("malformed-file" if "missing" not in str(e) else "missing-file", e)

    if nav is not None:
        if not (np.isfinite(nav).all() and np.isfinite(spy).all() and (nav > 0).all() and (spy > 0).all()):
            flag("malformed-file", "nav/spy contain non-finite or non-positive values")
            nav = spy = None
        elif len(set(ndates)) != len(ndates) or ndates != sorted(ndates):
            flag("dates", "nav dates duplicated or not ascending")
    if w is not None and (len(set(wdates)) != len(wdates) or wdates != sorted(wdates)):
        flag("dates", "position dates duplicated or not ascending")

    if nav is not None and w is not None and ndates != wdates:
        miss_w = sorted(set(ndates) - set(wdates))
        miss_n = sorted(set(wdates) - set(ndates))
        flag("date-alignment", f"{len(miss_w)} nav dates without positions (first {miss_w[:1]}), "
                               f"{len(miss_n)} position dates without nav (first {miss_n[:1]})")

    n_nav = len(nav) if nav is not None else None
    n_w = len(w) if w is not None else None
    short = [n for n in (n_nav, n_w) if n is not None and n < min_obs]
    if short:
        flag("insufficient data", f"{min(short)} observations < {min_obs}; not computed")
        return dict(name=name, mdd=None, turnover=None, vs_spy=None, flags=flags)

    if nav is not None:
        mdd = max_drawdown(nav)
        vs_spy = mdd - max_drawdown(spy)

    if w is not None:
        cash_cols = [i for i, c in enumerate(cols) if c == "cash"]
        bad_fin = ~np.isfinite(w)
        if bad_fin.any():
            flag("weights", f"{int(bad_fin.sum())} NaN/inf weights")
        elif long_only and (w < -1e-9).any():
            flag("weights", f"{int((w < -1e-9).sum())} negative weights (long-only expected)")
        if not bad_fin.any():
            dev = np.abs(w.sum(axis=1) - 1.0)
            if (dev > tol).any():
                flag("weights", f"{int((dev > tol).sum())} days where weights"
                                f"{'+cash' if cash_cols else ''} do not sum to 1 (max dev {dev.max():.3f})")
            else:
                dt = daily_turnover(w)
                turn = float(dt.mean() * ANN)
                if ceiling is not None and turn > ceiling:
                    flag("turnover-ceiling", f"{turn:.2f} > ceiling {ceiling:.2f}")
                if stall_days:
                    run = best = 0
                    for z in dt <= 1e-12:
                        run = run + 1 if z else 0
                        best = max(best, run)
                    if best >= stall_days:
                        flag("turnover-stalled", f"zero turnover for {best} consecutive days")

    if reported is not None and not isinstance(reported, dict):
        flag("report-mismatch", "report entry is not an object")
    elif reported is not None:
        for key, got in (("max_drawdown", mdd), ("turnover", turn)):
            if got is None:
                continue
            rep = reported.get(key)
            try:
                rep = float(rep)
            except (TypeError, ValueError):
                rep = float("nan")
            if not np.isfinite(rep):
                flag("report-mismatch", f"report has no valid {key}")
            elif key == "max_drawdown" and abs(abs(rep) - abs(got)) > tol or \
                    key == "turnover" and abs(rep - got) > tol:
                flag("report-mismatch", f"{key} report {rep:.4f} vs recomputed {got:.4f}")
    return dict(name=name, mdd=mdd, turnover=turn, vs_spy=vs_spy, flags=flags)


def load_report(root):
    p = root / "report.json"
    if not p.is_file():
        raise Bad(f"missing weekly report {p}")
    try:
        ports = json.loads(p.read_text())["portfolios"]
        if not isinstance(ports, dict):
            raise TypeError("portfolios is not an object")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Bad(f"malformed weekly report {p}: {e}")
    return ports


def run(root, portfolios=None, **kw):
    root = Path(root)
    results = []
    try:
        report = load_report(root)
    except Bad as e:
        report = {}
        results.append(dict(name="<report>", mdd=None, turnover=None, vs_spy=None,
                            flags=[f"missing-file: {e}"]))
    names = list(portfolios) if portfolios else sorted(
        set(report) | {p.name for p in root.iterdir() if p.is_dir()}) if root.is_dir() else []
    if not root.is_dir():
        results.append(dict(name="<root>", mdd=None, turnover=None, vs_spy=None,
                            flags=[f"missing-portfolio: root {root} not a directory"]))
    for n in names:
        if report and n not in report:
            kw_rep = None
            r = check_portfolio(n, root / n, kw_rep, **kw)
            r["flags"].append("report-mismatch: portfolio absent from weekly report")
        else:
            r = check_portfolio(n, root / n, report.get(n), **kw)
        results.append(r)
    return results


def format_table(results):
    f = lambda v, p=True: "n/a" if v is None else (f"{v * 100:.2f}%" if p else f"{v:.2f}")
    rows = [("portfolio", "max_dd", "turnover(ann)", "dd_vs_spy", "flags")]
    for r in results:
        rows.append((r["name"], f(r["mdd"]), f(r["turnover"], False), f(r["vs_spy"]),
                     "; ".join(r["flags"]) or "OK"))
    wd = [max(len(str(x[i])) for x in rows) for i in range(4)]
    return "\n".join("  ".join(str(x[i]).ljust(wd[i]) for i in range(4)) + "  " + x[4] for x in rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--portfolios", help="comma-separated names (default: report + directories)")
    ap.add_argument("--turnover-ceiling", type=float, default=10.0)
    ap.add_argument("--stall-days", type=int, default=20)
    ap.add_argument("--tol", type=float, default=0.01)
    a = ap.parse_args(argv)
    res = run(a.root, a.portfolios.split(",") if a.portfolios else None,
              ceiling=a.turnover_ceiling, stall_days=a.stall_days, tol=a.tol)
    print(format_table(res))
    bad = [(r["name"], fl) for r in res for fl in r["flags"]]
    for n, fl in bad:
        print(f"FAIL {n}: {fl}", file=sys.stderr)
    return 1 if bad or not res else 0


if __name__ == "__main__":
    sys.exit(main())
