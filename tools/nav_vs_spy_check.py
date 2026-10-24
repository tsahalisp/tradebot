#!/usr/bin/env python3
"""Read-only integrity check of each paper portfolio's NAV series against SPY.

Source: the autoportfolio SQLite ledger (default ~/.tradebot/portfolio.db), tables
books(name, created, spy0), nav_history(book, ts, nav, spy) and trades(book, ts).
The DB is opened read-only; nothing is written, no network is used.

Checks (per book; every failure names the file and the check):
  JUMP          one-day NAV change > --max-jump (25%) with no trade between the two marks
                (the ledger has no separate cash-flow table, so trades are the only
                 legitimate explanation)
  FLAT          NAV unchanged for > --max-flat (5) business days while SPY moved
  BASE_DATE     first NAV mark date != books.created date, or books.spy0 != SPY at the
                first mark (i.e. the base used for SPY comparisons differs from the NAV base)
  INSUFFICIENT  fewer than --min-obs (20) daily observations: Sharpe/alpha are reported as
                'insufficient data' and never computed
Plus structural failures: MISSING_FILE, UNREADABLE, NO_BOOKS, NO_NAV (never silently skipped).

Exit code 0 only if there are no flags. Usage: nav_vs_spy_check.py [DB ...]
"""
import argparse
import datetime as dt
import math
import os
import sqlite3
import sys

DEFAULT_DB = os.path.expanduser("~/.tradebot/portfolio.db")
INSUFFICIENT = "insufficient data"


def _d(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


def busdays_between(a, b):
    """Weekdays in (a, b]."""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def daily_series(rows):
    """rows: (ts, nav, spy) sorted by ts -> last mark per UTC date: [(date, ts, nav, spy)]."""
    out = {}
    for ts, nav, spy in rows:
        out[_d(ts)] = (ts, nav, spy)
    return [(d, *out[d]) for d in sorted(out)]


def sharpe_alpha(series):
    """Annualised Sharpe (rf=0, daily returns) and total-return alpha vs SPY, or None."""
    navs = [s[2] for s in series]
    spys = [s[3] for s in series]
    rets = [b / a - 1 for a, b in zip(navs, navs[1:]) if a]
    sharpe = None
    if len(rets) >= 2:
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = m / sd * math.sqrt(252) if sd > 0 else None
    alpha = None
    if navs[0] and spys[0] and spys[-1]:
        alpha = (navs[-1] / navs[0] - 1) - (spys[-1] / spys[0] - 1)
    return sharpe, alpha


def tracking_error(series):
    """Max |daily NAV return - daily SPY return| over days with both prices."""
    worst = 0.0
    for p, c in zip(series, series[1:]):
        if p[2] and p[3] and c[3] is not None:
            worst = max(worst, abs((c[2] / p[2] - 1) - (c[3] / p[3] - 1)))
    return worst


def check_book(name, created, spy0, rows, trade_ts, a):
    """Returns (flags, summary dict). flags: list of (check, message)."""
    flags = []
    series = daily_series(rows)
    if not series:
        return [("NO_NAV", f"book '{name}' has no nav_history rows")], None
    n = len(series)

    # (a) one-day jump with no trade between the two marks
    for p, c in zip(series, series[1:]):
        if p[2] and abs(c[2] / p[2] - 1) > a.max_jump:
            if not any(p[1] < t <= c[1] for t in trade_ts):
                flags.append(("JUMP", f"book '{name}': NAV {p[2]:.2f}->{c[2]:.2f} "
                              f"({(c[2] / p[2] - 1) * 100:+.1f}%) {p[0]}->{c[0]} with no trade/cash flow"))

    # (b) flat NAV for > max_flat business days while SPY moves
    i = 0
    while i < n:
        j = i
        while j + 1 < n and series[j + 1][2] == series[i][2]:
            j += 1
        if j > i:
            gap = busdays_between(series[i][0], series[j][0])
            si, sj = series[i][3], series[j][3]
            if gap > a.max_flat and si and sj and si != sj:
                flags.append(("FLAT", f"book '{name}': NAV flat at {series[i][2]:.2f} for {gap} business days "
                              f"{series[i][0]}->{series[j][0]} while SPY moved {(sj / si - 1) * 100:+.2f}%"))
        i = j + 1

    # (c) base date / SPY base consistency
    if created is not None and abs((_d(series[0][1]) - _d(created)).days) > a.base_tol_days:
        flags.append(("BASE_DATE", f"book '{name}': NAV starts {series[0][0]} but book created {_d(created)}"))
    first_spy = next((s[3] for s in series if s[3]), None)
    if first_spy is not None:
        if spy0 is None:
            flags.append(("BASE_DATE", f"book '{name}': books.spy0 is NULL but nav_history has SPY"))
        elif series[0][3] is None or abs(spy0 / first_spy - 1) > 1e-6:
            flags.append(("BASE_DATE", f"book '{name}': SPY base spy0={spy0:.4f} != SPY at first NAV mark "
                          f"{first_spy:.4f} (base date differs)"))
    elif spy0 is None:
        flags.append(("BASE_DATE", f"book '{name}': no SPY price anywhere in nav_history"))

    # (d) sample size gates Sharpe/alpha
    if n < a.min_obs:
        flags.append(("INSUFFICIENT", f"book '{name}': {n} observations < {a.min_obs}; "
                      f"Sharpe/alpha = '{INSUFFICIENT}'"))
        sharpe = alpha = INSUFFICIENT
    else:
        sharpe, alpha = sharpe_alpha(series)
    return flags, {"book": name, "n": n, "start": series[0][0], "end": series[-1][0],
                   "sharpe": sharpe, "alpha": alpha, "track": tracking_error(series)}


def check_db(path, a):
    """Returns (flags, summaries); flags are (file, check, message)."""
    if not os.path.isfile(path):
        return [(path, "MISSING_FILE", "file does not exist")], []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        books = {r[0]: r[1:] for r in con.execute("SELECT name,created,spy0 FROM books")}
        navbooks = [r[0] for r in con.execute("SELECT DISTINCT book FROM nav_history")]
        flags, sums = [], []
        names = sorted(set(books) | set(navbooks))
        if not names:
            return [(path, "NO_BOOKS", "no portfolios found")], []
        for nm in names:
            created, spy0 = books.get(nm, (None, None))
            if nm not in books:
                flags.append((path, "BASE_DATE", f"book '{nm}' has NAV rows but no books entry"))
            rows = con.execute("SELECT ts,nav,spy FROM nav_history WHERE book=? AND nav IS NOT NULL ORDER BY ts",
                               (nm,)).fetchall()
            tts = [r[0] for r in con.execute("SELECT ts FROM trades WHERE book=?", (nm,))]
            f, s = check_book(nm, created, spy0, rows, tts, a)
            flags += [(path, c, m) for c, m in f]
            if s:
                s["file"] = path
                sums.append(s)
        con.close()
        return flags, sums
    except sqlite3.Error as e:
        return [(path, "UNREADABLE", f"{type(e).__name__}: {e}")], []


def _fmt(x, pct=False):
    if x is None:
        return "n/a"
    if isinstance(x, str):
        return x
    return f"{x * 100:+.2f}%" if pct else f"{x:.2f}"


def render(sums):
    hdr = ["file", "book", "obs", "start", "end", "sharpe", "alpha_vs_spy", "max|r-rSPY|"]
    rows = [[os.path.basename(s["file"]), s["book"], str(s["n"]), str(s["start"]), str(s["end"]),
             _fmt(s["sharpe"]), _fmt(s["alpha"], True), _fmt(s["track"], True)] for s in sums]
    w = [max(len(r[i]) for r in [hdr] + rows) for i in range(len(hdr))]
    return "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in [hdr] + rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dbs", nargs="*", default=[DEFAULT_DB])
    ap.add_argument("--max-jump", type=float, default=0.25)
    ap.add_argument("--max-flat", type=int, default=5)
    ap.add_argument("--min-obs", type=int, default=20)
    ap.add_argument("--base-tol-days", type=int, default=1,
                    help="allowed calendar-day gap between books.created and first NAV mark")
    a = ap.parse_args(argv)
    flags, sums = [], []
    for p in a.dbs:
        f, s = check_db(p, a)
        flags += f
        sums += s
    if sums:
        print(render(sums))
    if flags:
        print(f"\nFAIL: {len(flags)} flag(s)", file=sys.stderr)
        for p, c, m in flags:
            print(f"  FAIL [{c}] {p}: {m}", file=sys.stderr)
        return 1
    print("\nOK: no flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
