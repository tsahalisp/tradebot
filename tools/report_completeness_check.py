#!/usr/bin/env python3
"""Read-only completeness check for the weekly paper-portfolio report output.

NOTE: this checkout has no weekly_report module and no tools/params_snapshot.json,
so the formats below are the ASSUMED contract (documented, not discovered).

Report (JSON or CSV):
  JSON: {"as_of": "YYYY-MM-DD", "period_start": "YYYY-MM-DD",
         "portfolios": {name: {nav, spy_return_pct, drawdown_pct, turnover_pct[, return_pct]}}}
        ("portfolios" may also be a list of objects carrying a "portfolio" key)
  CSV : header portfolio,as_of,period_start,nav,spy_return_pct,drawdown_pct,turnover_pct[,return_pct]
Snapshot: JSON with "portfolios" as a dict (keys) or list (names / {"name": ..}).
Prices  : CSV with columns date,close (SPY).

Checks: (a) every snapshot portfolio present with all required fields, (b) no field
NaN/null/inf/empty, (c) as-of within --max-bdays business days of today, (d) report
SPY return matches the SPY price series within --tol percentage points.
Exit: 0 all pass; 1 check failure(s); 2 unreadable/malformed input. Never writes anything.
"""
import argparse
import csv
import datetime as dt
import json
import math
import sys

REQUIRED = ("nav", "spy_return_pct", "drawdown_pct", "turnover_pct")


class InputError(Exception):
    pass


def _date(s, what):
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except Exception:
        raise InputError(f"{what}: bad or missing date {s!r}")


def load_snapshot(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise InputError(f"params snapshot {path}: {e}")
    p = d.get("portfolios") if isinstance(d, dict) else None
    if isinstance(p, dict):
        names = list(p)
    elif isinstance(p, list):
        names = [x if isinstance(x, str) else (x.get("name") if isinstance(x, dict) else None) for x in p]
    else:
        raise InputError(f"params snapshot {path}: no 'portfolios' dict/list")
    if not names or any(not n for n in names):
        raise InputError(f"params snapshot {path}: empty or unnamed portfolio entries")
    return names


def load_report(path):
    """-> (as_of, period_start, rows) with rows = [(name, {field: raw})]. Raises InputError."""
    try:
        text = open(path).read()
    except OSError as e:
        raise InputError(f"report {path}: {e}")
    if not text.strip():
        raise InputError(f"report {path}: empty file")
    if path.lower().endswith(".csv"):
        recs = list(csv.DictReader(text.splitlines()))
        if not recs or "portfolio" not in recs[0]:
            raise InputError(f"report {path}: CSV needs a 'portfolio' column and >=1 row")
        as_of, start = recs[0].get("as_of"), recs[0].get("period_start")
        rows = [(r.get("portfolio") or "", r) for r in recs]
    else:
        try:
            d = json.loads(text)
        except ValueError as e:
            raise InputError(f"report {path}: invalid JSON ({e})")
        if not isinstance(d, dict):
            raise InputError(f"report {path}: top level must be an object")
        as_of, start = d.get("as_of"), d.get("period_start")
        p = d.get("portfolios")
        if isinstance(p, dict):
            rows = list(p.items())
        elif isinstance(p, list):
            rows = [(x.get("portfolio", "") if isinstance(x, dict) else "", x) for x in p]
        else:
            raise InputError(f"report {path}: no 'portfolios' dict/list")
    for name, rec in rows:
        if not isinstance(rec, dict):
            raise InputError(f"report {path}: portfolio {name!r} entry is not an object")
    return _date(as_of, "report as_of"), _date(start, "report period_start"), rows


def load_prices(path):
    try:
        with open(path) as f:
            recs = list(csv.DictReader(f))
        px = {_date(r["date"], "price file"): float(r["close"]) for r in recs}
    except InputError:
        raise
    except Exception as e:
        raise InputError(f"price file {path}: {type(e).__name__}: {e}")
    if len(px) < 2 or any(not math.isfinite(v) or v <= 0 for v in px.values()):
        raise InputError(f"price file {path}: need >=2 finite positive closes")
    return px


def bdays_between(a, b):
    """Weekdays in (a, b]; negative if a is after b."""
    if a > b:
        return -bdays_between(b, a)
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        n += d.weekday() < 5
    return n


def _num(v):
    """float if finite number, else None (None/''/NaN/inf/non-numeric all -> None)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        if isinstance(v, str) and not v.strip():
            return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _px_on(px, day):
    """Close on `day` or the last prior one (weekends/holidays)."""
    ds = [d for d in px if d <= day]
    return px[max(ds)] if ds else None


def check(report, snapshot, prices, today, max_bdays=3, tol=0.25):
    """-> (table_rows, failures). failures = [(portfolio, field, message)]."""
    as_of, start, rows = report
    fails, table = [], []
    age = bdays_between(as_of, today)
    if age > max_bdays or age < 0:
        fails.append(("*report*", "as_of", f"as_of {as_of} is {age} business days from {today} (limit {max_bdays}, no future dates)"))
    s0, s1 = _px_on(prices, start), _px_on(prices, as_of)
    spy_ref = None
    if s0 is None or s1 is None or max(prices) < as_of:
        fails.append(("*report*", "spy_series", f"SPY prices do not cover {start}..{as_of}"))
    else:
        spy_ref = (s1 / s0 - 1) * 100
    seen = {}
    for name, rec in rows:
        if not name:
            fails.append(("*unnamed*", "portfolio", "report entry has no portfolio name"))
            continue
        if name in seen:
            fails.append((name, "portfolio", "duplicate entry in report"))
        seen[name] = rec
    for name in snapshot:
        rec = seen.get(name)
        if rec is None:
            fails.append((name, "*portfolio*", "listed in params snapshot but missing from report"))
            table.append([name, "MISSING", "", "", "", "", "FAIL"])
            continue
        bad = False
        vals = {}
        for fld in REQUIRED:
            if fld not in rec:
                fails.append((name, fld, "field missing")); bad = True
                continue
            v = _num(rec[fld])
            if v is None:
                fails.append((name, fld, f"invalid value {rec[fld]!r} (null/NaN/inf/empty/non-numeric)")); bad = True
            vals[fld] = v
        sr = vals.get("spy_return_pct")
        if sr is not None and spy_ref is not None and abs(sr - spy_ref) > tol:
            fails.append((name, "spy_return_pct", f"report {sr:.4f}% vs SPY series {spy_ref:.4f}% (tol {tol})")); bad = True
        ret = _num(rec.get("return_pct"))
        alpha = f"{ret - sr:+.2f}" if ret is not None and sr is not None else "n/a"
        f = lambda k: "BAD" if vals.get(k) is None else f"{vals[k]:,.2f}"
        table.append([name, f("nav"), f("spy_return_pct"), f("drawdown_pct"), f("turnover_pct"), alpha, "FAIL" if bad else "ok"])
    extra = sorted(set(seen) - set(snapshot))
    for name in extra:
        fails.append((name, "*portfolio*", "in report but not in params snapshot (unexpected)"))
    return table, fails


def render(table, fails, spy_note=""):
    hdr = ["portfolio", "nav", "spy_ret%", "drawdown%", "turnover%", "alpha%", "status"]
    t = [hdr] + table
    w = [max(len(str(r[i])) for r in t) for i in range(len(hdr))]
    out = ["  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)) for r in t]
    for p, fld, msg in fails:
        out.append(f"FAIL portfolio={p} field={fld}: {msg}")
    out.append("RESULT: " + (f"FAIL ({len(fails)} problem(s))" if fails else "PASS"))
    return "\n".join(out)


def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report")
    ap.add_argument("--snapshot", default="tools/params_snapshot.json")
    ap.add_argument("--spy-prices", required=True, help="CSV date,close")
    ap.add_argument("--today", help="YYYY-MM-DD (default: system date)")
    ap.add_argument("--max-bdays", type=int, default=3)
    ap.add_argument("--tol", type=float, default=0.25, help="SPY return tolerance, percentage points")
    a = ap.parse_args(argv)
    try:
        t = today or (_date(a.today, "--today") if a.today else dt.date.today())
        names = load_snapshot(a.snapshot)
        report = load_report(a.report)
        prices = load_prices(a.spy_prices)
    except InputError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    table, fails = check(report, names, prices, t, a.max_bdays, a.tol)
    print(render(table, fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
