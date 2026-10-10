"""
Weekly paper-portfolio report: NAV vs SPY, max drawdown, turnover. READ-ONLY.

Reads the autoportfolio ledger (~/.tradebot/portfolio.db: books / nav_history / trades),
prints a table and writes markdown to reports/. Fails LOUDLY (exit 1, every problem listed,
no report written) if the DB, any book, its NAV series, its trades or any SPY value is
missing/empty/stale. A book is never silently skipped.

Definitions (all on one NAV point per UTC day = the last mark of that day):
  7d return        last NAV / NAV at last point on-or-before (last_date - 7d) - 1
  inception return last NAV / first NAV - 1        (SPY over the same two dates)
  max drawdown     worst peak-to-trough NAV decline over the whole series
  turnover         (sum of buy+sell notional / 2) / mean NAV, over the same window
Usage: python tools/weekly_report.py [--db PATH] [--out-dir reports] [--today YYYY-MM-DD]
"""
import argparse, datetime as dt, os, sqlite3, sys

DEFAULT_DB = os.path.expanduser("~/.tradebot/portfolio.db")
MAX_STALE_BDAYS = 3
WINDOW_DAYS = 7


class ReportError(Exception):
    pass


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def business_days_between(d0, d1):
    """Weekdays in (d0, d1]. 0 if d1 <= d0."""
    n, d = 0, d0
    while d < d1:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _open_ro(path):
    if not os.path.isfile(path):
        raise ReportError(f"database not found: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)


def load_book_names(c):
    try:
        names = [n for (n,) in c.execute("SELECT name FROM books ORDER BY name")]
    except sqlite3.Error as e:
        raise ReportError(f"cannot read 'books' table: {e}")
    if not names:
        raise ReportError("'books' table is empty: no paper portfolios to report on")
    return names


def daily_nav(c, book):
    """-> sorted list of (date, nav, spy), last mark per UTC day. Raises ReportError on bad data."""
    try:
        rows = c.execute("SELECT ts,nav,spy FROM nav_history WHERE book=? ORDER BY ts", (book,)).fetchall()
    except sqlite3.Error as e:
        raise ReportError(f"cannot read nav_history: {e}")
    if not rows:
        raise ReportError("nav_history is empty")
    by_day = {}
    for ts, nav, spy in rows:
        by_day[_day(ts)] = (nav, spy)   # later mark of the same day overwrites
    out, bad_nav, bad_spy = [], [], []
    for d in sorted(by_day):
        nav, spy = by_day[d]
        if nav is None or nav <= 0:
            bad_nav.append(d.isoformat())
        if spy is None or spy <= 0:
            bad_spy.append(d.isoformat())
        out.append((d, nav, spy))
    if bad_nav:
        raise ReportError(f"missing/invalid NAV on {len(bad_nav)} date(s): {', '.join(bad_nav)}")
    if bad_spy:
        raise ReportError(f"SPY missing/invalid for NAV date(s): {', '.join(bad_spy)}")
    return out


def load_trades(c, book):
    try:
        rows = c.execute("SELECT ts,shares,price FROM trades WHERE book=? ORDER BY ts", (book,)).fetchall()
    except sqlite3.Error as e:
        raise ReportError(f"cannot read trades: {e}")
    if not rows:
        raise ReportError("trades is empty (cannot compute turnover)")
    if any(s is None or p is None for _, s, p in rows):
        raise ReportError("trades contain NULL shares/price")
    return [(_day(ts), abs(s) * p) for ts, s, p in rows]


def max_drawdown(navs):
    peak, worst = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def turnover(trades, navs, after=None):
    notional = sum(n for d, n in trades if after is None or d > after)
    return (notional / 2) / (sum(navs) / len(navs))


def compute_book(name, series, trades, today, max_stale=MAX_STALE_BDAYS):
    last_d = series[-1][0]
    stale = business_days_between(last_d, today)
    if stale > max_stale:
        raise ReportError(f"stale: last NAV {last_d} is {stale} business days before {today} (max {max_stale})")
    first, last = series[0], series[-1]
    r = {"book": name, "first_date": first[0], "last_date": last_d, "points": len(series),
         "last_nav": last[1],
         "inc_nav": last[1] / first[1] - 1, "inc_spy": last[2] / first[2] - 1,
         "max_dd": max_drawdown([p[1] for p in series]),
         "inc_turnover": turnover(trades, [p[1] for p in series])}
    cutoff = last_d - dt.timedelta(days=WINDOW_DAYS)
    base_i = max((i for i, p in enumerate(series) if p[0] <= cutoff), default=None)
    if base_i is None:
        r.update(w_nav=None, w_spy=None, w_turnover=None)   # book younger than 7d: shown as n/a + warned
    else:
        b, win = series[base_i], series[base_i:]
        r.update(w_nav=last[1] / b[1] - 1, w_spy=last[2] / b[2] - 1,
                 w_turnover=turnover(trades, [p[1] for p in win], after=b[0]))
    return r


def build_report(db, today):
    """-> (results, errors, warnings). errors non-empty means the report must not be trusted."""
    results, errors, warnings = [], [], []
    try:
        c = _open_ro(db)
        names = load_book_names(c)
    except (ReportError, sqlite3.Error) as e:
        return [], [f"[input] {e}"], []
    try:
        for name in names:
            try:
                res = compute_book(name, daily_nav(c, name), load_trades(c, name), today)
                results.append(res)
                if res["w_nav"] is None:
                    warnings.append(f"[{name}] history shorter than {WINDOW_DAYS}d: 7d columns are n/a")
            except ReportError as e:
                errors.append(f"[{name}] {e}")
    finally:
        c.close()
    return results, errors, warnings


def _pct(x):
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def render(results, today):
    hdr = ["Book", "Last NAV", "7d NAV", "7d SPY", "7d alpha", "Inc NAV", "Inc SPY", "Inc alpha",
           "Max DD", "Turnover 7d", "Turnover inc"]
    rows = []
    for r in results:
        wa = None if r["w_nav"] is None else r["w_nav"] - r["w_spy"]
        rows.append([r["book"], f"{r['last_nav']:,.2f}", _pct(r["w_nav"]), _pct(r["w_spy"]), _pct(wa),
                     _pct(r["inc_nav"]), _pct(r["inc_spy"]), _pct(r["inc_nav"] - r["inc_spy"]),
                     _pct(r["max_dd"]),
                     "n/a" if r["w_turnover"] is None else f"{r['w_turnover'] * 100:.2f}%",
                     f"{r['inc_turnover'] * 100:.2f}%"])
    widths = [max(len(x[i]) for x in [hdr] + rows) for i in range(len(hdr))]
    line = lambda cells: "  ".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths)))
    text = "\n".join([f"Weekly paper-portfolio report as of {today}", line(hdr),
                      "  ".join("-" * w for w in widths)] + [line(x) for x in rows])
    md = [f"# Weekly paper-portfolio report — {today}", "",
          "| " + " | ".join(hdr) + " |", "|" + "|".join(["---"] + ["---:"] * (len(hdr) - 1)) + "|"]
    md += ["| " + " | ".join(x) + " |" for x in rows]
    md += ["", "Returns are NAV vs SPY over the same dates (one point per UTC day). "
           "Turnover = (buy+sell notional / 2) / mean NAV. Paper money, real prices, not advice."]
    return text, "\n".join(md) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports"))
    ap.add_argument("--today", type=dt.date.fromisoformat, default=None, help="override today (UTC), YYYY-MM-DD")
    a = ap.parse_args(argv)
    today = a.today or dt.datetime.now(dt.timezone.utc).date()
    results, errors, warnings = build_report(a.db, today)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if errors:
        print("WEEKLY REPORT FAILED — no report written:", file=sys.stderr)
        for e in errors:
            print(f"  ERROR {e}", file=sys.stderr)
        return 1
    text, md = render(results, today)
    print(text)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"weekly_{today.isoformat()}.md")
    with open(path, "w") as f:
        f.write(md)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
