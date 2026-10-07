#!/usr/bin/env python3
"""Stale-data / data-gap alarm for the paper-portfolio price series (core universe + SPY).

For each ticker: last bar date, and missing NYSE trading days in the last 30 days.
Exits 1 (with a per-ticker message) if any series is empty, older than 3 business
days, or has gaps. Exits 2 on usage errors. Read-only: touches no strategy or DB.

  tools/check_data_freshness.py --dir DIR     # DIR/<TICKER>.csv with a date column
  tools/check_data_freshness.py --live        # fetch via backend/datasource (yfinance)
"""
import argparse, csv, os, sys
from datetime import date, datetime, timedelta

MAX_STALE_BDAYS = 3
WINDOW_DAYS = 30
DATE_COLS = ("date", "Date", "t", "timestamp")


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, da = divmod(h + l - 7 * m + 114, 31)
    return date(y, mo, da + 1)


def _nth_weekday(y, m, wd, n):
    d = date(y, m, 1)
    d += timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    return d


def _observed(d):
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def nyse_holidays(y):
    last_mon_may = max(d for d in (date(y, 5, 1) + timedelta(days=i) for i in range(31))
                       if d.month == 5 and d.weekday() == 0)
    h = {_observed(date(y, 1, 1)), _nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3),
         _easter(y) - timedelta(days=2), last_mon_may, _observed(date(y, 7, 4)),
         _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4), _observed(date(y, 12, 25))}
    if y >= 2022:
        h.add(_observed(date(y, 6, 19)))
    return h


def trading_days(start, end):
    hol = set().union(*(nyse_holidays(y) for y in range(start.year, end.year + 1)))
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def bdays_between(last, asof):
    """Weekdays strictly after `last` up to and including `asof`."""
    n, d = 0, last
    while d < asof:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def read_csv_dates(path):
    """Bar dates from a CSV. Raises ValueError on unreadable/undated files."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    col = next((c for c in DATE_COLS if c in rows[0]), None)
    if col is None:
        raise ValueError(f"no date column (looked for {DATE_COLS})")
    return [datetime.fromisoformat(r[col].strip()[:10]).date() for r in rows if r[col].strip()]


def check_series(dates, asof):
    """-> (last_bar|None, stale_bdays|None, missing_days, problems[])"""
    if not dates:
        return None, None, [], ["EMPTY: no bars at all"]
    have = set(dates)
    last = max(have)
    problems = []
    stale = bdays_between(last, asof)
    if last > asof:
        problems.append(f"FUTURE: last bar {last} is after as-of {asof}")
    if stale > MAX_STALE_BDAYS:
        problems.append(f"STALE: last bar {last} is {stale} business days old (max {MAX_STALE_BDAYS})")
    # gaps: expected trading days in the window up to the last bar (later ones count as stale)
    missing = [d for d in trading_days(asof - timedelta(days=WINDOW_DAYS), min(last, asof)) if d not in have]
    if missing:
        problems.append(f"GAPS: {len(missing)} missing trading day(s): " + ", ".join(map(str, missing)))
    return last, stale, missing, problems


def universe():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    from autoportfolio import MEGACAP_CORE
    return sorted(set(MEGACAP_CORE) | {"SPY"})


def load_dir(directory, tickers):
    out = {}
    for t in tickers:
        p = os.path.join(directory, f"{t}.csv")
        if not os.path.exists(p):
            out[t] = None
            continue
        try:
            out[t] = read_csv_dates(p)
        except Exception as e:
            out[t] = e
    return out


def load_live(tickers):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
    import datasource
    out = {}
    for t in tickers:
        try:
            out[t] = [datetime.fromisoformat(str(b["t"])[:10]).date() for b in datasource.get_bars(t, "1d", 60)]
        except Exception as e:
            out[t] = e
    return out


def run(series, asof, out=sys.stdout):
    """series: {ticker: [dates] | None (missing file) | Exception}. Returns exit code."""
    bad = 0
    for t, s in sorted(series.items()):
        if s is None:
            last, problems = None, ["MISSING: no data file/series"]
        elif isinstance(s, Exception):
            last, problems = None, [f"UNREADABLE: {s}"]
        else:
            last, _, _, problems = check_series(s, asof)
        print(f"{'FAIL' if problems else 'ok  '} {t:6s} last={last or '-'}", file=out)
        for p in problems:
            print(f"       {p}", file=out)
        bad += bool(problems)
    if bad:
        print(f"DATA CHECK FAILED: {bad}/{len(series)} series bad (as-of {asof})", file=out)
        return 1
    print(f"DATA CHECK OK: {len(series)} series fresh and gap-free (as-of {asof})", file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dir", help="directory of <TICKER>.csv files")
    g.add_argument("--live", action="store_true", help="fetch via backend/datasource")
    ap.add_argument("--asof", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--tickers", help="comma list (default: core universe + SPY)")
    a = ap.parse_args(argv)
    asof = date.fromisoformat(a.asof) if a.asof else date.today()
    tickers = a.tickers.split(",") if a.tickers else universe()
    series = load_live(tickers) if a.live else load_dir(a.dir, tickers)
    return run(series, asof)


if __name__ == "__main__":
    sys.exit(main())
