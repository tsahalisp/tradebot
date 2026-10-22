"""Stale-data / data-gap alarm. Read-only: opens SQLite with mode=ro, never writes.

Data lives in (see backend/database.py, backend/autoportfolio.py):
  ~/.tradebot/portfolio.db  nav_history(book,ts,nav,..,spy), trades(book,ts,price,..)
  ~/.tradebot/tradebot.db   paper_marks(book_id,date,price,equity), paper_book(book_id,product)
  optional price CSVs (--csv, columns: date + close)

Checks per series: (a) stale: last date > MAX_STALE_BDAYS business days old,
(b) internal gap > MAX_GAP_BDAYS business days, (c) NaN / non-positive fraction,
(d) every paper-portfolio NAV date has a valid SPY price. A missing, unreadable or
empty file/table is itself a FAIL -- nothing is skipped silently.
Business days are Mon-Fri only (exchange holidays are NOT modelled; limits have slack).
Exit 0 = all pass, 1 = at least one FAIL.
"""
import argparse
import csv
import datetime as dt
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field

MAX_STALE_BDAYS = 3
MAX_GAP_BDAYS = 4
MAX_BAD_FRAC = 0.05

HOME = os.path.expanduser("~/.tradebot")
DEFAULT_PORTFOLIO_DB = os.path.join(HOME, "portfolio.db")
DEFAULT_MARKS_DB = os.path.join(HOME, "tradebot.db")


@dataclass
class Series:
    file: str
    name: str
    dates: list = field(default_factory=list)   # datetime.date per row (may repeat)
    values: list = field(default_factory=list)  # float | None per row
    event: bool = False  # sparse event data (trades): only the bad-value check applies


def bdays_between(a, b):
    """Business days (Mon-Fri) after a up to and including b; negative if b < a."""
    if b < a:
        return -bdays_between(b, a)
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def is_bad(v):
    return v is None or (isinstance(v, float) and math.isnan(v)) or v <= 0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dedupe(s):
    """Last value per calendar date (intraday re-marks collapse to one daily point)."""
    out = {}
    for d, v in zip(s.dates, s.values):
        out[d] = v
    return out


def check_series(s, today, max_stale=MAX_STALE_BDAYS, max_gap=MAX_GAP_BDAYS, max_bad=MAX_BAD_FRAC):
    """Return [(check, ok, detail)] for one series."""
    if not s.dates:
        return [("empty", False, "no rows")]
    res = []
    days = sorted(set(s.dates))
    if s.event:  # trades are not daily: no stale/gap semantics
        nbad = sum(is_bad(v) for v in s.values)
        frac = nbad / len(s.values)
        return [("bad_values", frac <= max_bad, f"{nbad}/{len(s.values)} NaN/non-positive = {frac:.1%}")]
    age = bdays_between(days[-1], today)
    res.append(("stale", age <= max_stale, f"last {days[-1]} ({age} bdays old, max {max_stale})"))
    worst, at = 0, None
    for p, n in zip(days, days[1:]):
        g = bdays_between(p, n)
        if g > worst:
            worst, at = g, (p, n)
    res.append(("gap", worst <= max_gap,
                f"largest {worst} bdays" + (f" {at[0]}->{at[1]}" if at else "") + f" (max {max_gap})"))
    nbad = sum(is_bad(v) for v in s.values)
    frac = nbad / len(s.values)
    res.append(("bad_values", frac <= max_bad,
                f"{nbad}/{len(s.values)} NaN/non-positive = {frac:.1%} (max {max_bad:.0%})"))
    return res


def check_spy_coverage(nav, spy):
    """(d) every NAV date must have a valid (finite, >0) SPY price. spy: Series | None."""
    if spy is None:
        return ("spy_match", False, "no SPY series available to compare against")
    sp = _dedupe(spy)
    missing = sorted(d for d in _dedupe(nav) if is_bad(sp.get(d)))
    if missing:
        shown = ", ".join(map(str, missing[:5])) + (" ..." if len(missing) > 5 else "")
        return ("spy_match", False, f"{len(missing)} NAV dates lack SPY price: {shown}")
    return ("spy_match", True, "all NAV dates have SPY")


def _open_ro(path):
    if not os.path.isfile(path):
        raise FileNotFoundError("file missing")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def _ts_date(ts):
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).date()


def _iso_date(x):
    return dt.date.fromisoformat(str(x)[:10])


def load_portfolio_db(path):
    """-> (results, problems). results: list of (series, spy_series|None, is_nav)."""
    c = _open_ro(path)
    out = []
    try:
        books = [r[0] for r in c.execute("SELECT DISTINCT book FROM nav_history ORDER BY book")]
        if not books:
            raise ValueError("nav_history has no rows")
        for b in books:
            rows = c.execute("SELECT ts,nav,spy FROM nav_history WHERE book=? ORDER BY ts", (b,)).fetchall()
            ds = [_ts_date(r[0]) for r in rows]
            nav = Series(path, f"{b}.nav", ds, [_num(r[1]) for r in rows])
            spy = Series(path, f"{b}.spy", ds, [_num(r[2]) for r in rows])
            out.append((nav, spy, True))
            out.append((spy, None, False))
        tr = c.execute("SELECT ts,price FROM trades ORDER BY ts").fetchall()
        if not tr:
            raise ValueError("trades has no rows")
        out.append((Series(path, "trades.price", [_ts_date(r[0]) for r in tr], [_num(r[1]) for r in tr], event=True),
                    None, None))
    finally:
        c.close()
    return out


def load_marks_db(path):
    c = _open_ro(path)
    try:
        rows = c.execute("""SELECT b.book_id,b.product,m.date,m.price,m.equity FROM paper_marks m
                            JOIN paper_book b ON b.book_id=m.book_id ORDER BY m.date""").fetchall()
        if not rows:
            raise ValueError("paper_marks has no rows")
    finally:
        c.close()
    by = {}
    for bid, prod, d, px, eq in rows:
        by.setdefault((bid, prod), []).append((_iso_date(d), _num(px), _num(eq)))
    spy_dates, spy_vals = [], []
    for (bid, prod), r in by.items():
        if str(prod).upper() == "SPY":
            spy_dates += [x[0] for x in r]
            spy_vals += [x[1] for x in r]
    spy = Series(path, "SPY.price", spy_dates, spy_vals) if spy_dates else None
    out = []
    for (bid, prod), r in sorted(by.items(), key=lambda kv: kv[0][0]):
        ds = [x[0] for x in r]
        out.append((Series(path, f"book{bid}[{prod}].price", ds, [x[1] for x in r]), None, False))
        out.append((Series(path, f"book{bid}[{prod}].equity", ds, [x[2] for x in r]), spy, True))
    return out


def load_csv(path):
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        cols = {k.lower(): k for k in (rd.fieldnames or [])}
        dcol = cols.get("date") or cols.get("t")
        vcol = cols.get("close") or cols.get("c") or cols.get("adj close")
        if not dcol or not vcol:
            raise ValueError(f"needs date and close columns, got {rd.fieldnames}")
        rows = list(rd)
    if not rows:
        raise ValueError("no data rows")
    return [(Series(path, os.path.basename(path), [_iso_date(r[dcol]) for r in rows],
                    [_num(r[vcol]) for r in rows]), None, False)]


def run(portfolio_db, marks_db, csvs, today, fmt=print):
    """Run all checks. Returns (rows, failures); row = (file, series, check, status, detail)."""
    rows = []

    def add(f, name, check, ok, detail):
        rows.append((f, name, check, "ok" if ok else "FAIL", detail))

    sources = [(portfolio_db, load_portfolio_db), (marks_db, load_marks_db)]
    sources += [(c, load_csv) for c in csvs]
    for path, loader in sources:
        if path is None:
            continue
        try:
            items = loader(path)
        except Exception as e:  # missing / corrupt / empty: always an alarm, never a skip
            add(path, "*", "load", False, f"{type(e).__name__}: {e}")
            continue
        for s, spy, is_nav in items:
            for check, ok, detail in check_series(s, today):
                add(path, s.name, check, ok, detail)
            if is_nav:
                add(path, s.name, *check_spy_coverage(s, spy))
    return rows


def render(rows):
    hdr = ("FILE", "SERIES", "CHECK", "STATUS", "DETAIL")
    w = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(4)]
    lines = [" | ".join(str(c).ljust(w[i]) for i, c in enumerate(r[:4])) + " | " + r[4] for r in [hdr] + rows]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--portfolio-db", default=DEFAULT_PORTFOLIO_DB)
    ap.add_argument("--marks-db", default=DEFAULT_MARKS_DB)
    ap.add_argument("--csv", action="append", default=[], help="price CSV (date,close); repeatable")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: today UTC)")
    a = ap.parse_args(argv)
    today = _iso_date(a.today) if a.today else dt.datetime.now(dt.timezone.utc).date()
    rows = run(a.portfolio_db, a.marks_db, a.csv, today)
    print(render(rows))
    fails = [r for r in rows if r[3] == "FAIL"]
    if fails:
        print(f"\nDATA ALARM: {len(fails)} failing check(s):", file=sys.stderr)
        for f, name, check, _, detail in fails:
            print(f"  {f} [{name}] {check}: {detail}", file=sys.stderr)
        return 1
    print("\nall data checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
