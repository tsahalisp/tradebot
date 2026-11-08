#!/usr/bin/env python3
"""Read-only per-portfolio NAV staleness / alignment check against SPY.

Inputs (CSV, read only; no network, no DB writes):
  --nav-dir DIR   one file per paper portfolio, header `date,nav`  (name = file stem)
  --spy FILE      header `date,close`
Checks per portfolio (failures name portfolio + check, exit code 1):
  stale        last NAV date more than 3 business days before --today
  misaligned   start/end differs from SPY, or dates present in only one series (listed)
  bad_value    NAV zero / negative / NaN / inf, or a one-day move above 25%
  flat         identical NAV for 5+ consecutive observations (stalled runner)
  insufficient fewer than 20 observations: reported, return not computed, exit non-zero
  malformed    missing/unreadable/unparseable file, duplicate or unsorted dates
Exit codes: 0 all clean, 1 any check failed, 2 usage/no inputs.
"""
import argparse
import csv
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_STALE_BDAYS = 3
MAX_JUMP = 0.25
FLAT_RUN = 5
MIN_OBS = 20


def business_days_between(a, b):
    """Weekdays in (a, b]; 0 if b <= a."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def read_series(path, col):
    """Return list of (date, float) or raise ValueError describing the problem."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"file missing: {p}")
    try:
        with p.open(newline="") as f:
            rd = csv.DictReader(f)
            if not rd.fieldnames or "date" not in rd.fieldnames or col not in rd.fieldnames:
                raise ValueError(f"header must contain 'date' and '{col}', got {rd.fieldnames}")
            rows = []
            for i, r in enumerate(rd, start=2):
                try:
                    d = datetime.strptime((r["date"] or "").strip(), "%Y-%m-%d").date()
                    v = float((r[col] or "").strip())
                except (ValueError, TypeError, AttributeError) as e:
                    raise ValueError(f"line {i}: unparseable row {dict(r)}: {e}")
                rows.append((d, v))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise ValueError(f"unreadable: {e}")
    if not rows:
        raise ValueError("no data rows")
    dates = [d for d, _ in rows]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate dates")
    if dates != sorted(dates):
        raise ValueError("dates not sorted ascending")
    return rows


def check_portfolio(nav, spy, today):
    """Return (failures: list[(check, detail)], cum_ret, spy_ret, insufficient)."""
    fails = []
    dates = [d for d, _ in nav]
    vals = [v for _, v in nav]

    lag = business_days_between(dates[-1], today)
    if lag > MAX_STALE_BDAYS:
        fails.append(("stale", f"last NAV {dates[-1]} is {lag} business days before {today}"))

    bad = [(d, v) for d, v in nav if not math.isfinite(v) or v <= 0]
    if bad:
        fails.append(("bad_value", "zero/negative/NaN/inf NAV on " + ", ".join(f"{d}={v}" for d, v in bad)))
    for (d0, v0), (d1, v1) in zip(nav, nav[1:]):
        if math.isfinite(v0) and math.isfinite(v1) and v0 > 0 and v1 > 0 and abs(v1 / v0 - 1) > MAX_JUMP:
            fails.append(("bad_value", f"one-day move {v1 / v0 - 1:+.1%} on {d1} (possible bookkeeping error)"))

    if len(nav) < MIN_OBS:
        fails.append(("insufficient", f"insufficient data: {len(nav)} observations < {MIN_OBS}; not computed"))
        return fails, None, None, True

    spy_d = {d for d, _ in spy}
    nav_d = set(dates)
    if dates[0] != spy[0][0] or dates[-1] != spy[-1][0]:
        fails.append(("misaligned", f"range NAV {dates[0]}..{dates[-1]} vs SPY {spy[0][0]}..{spy[-1][0]}"))
    only_nav, only_spy = sorted(nav_d - spy_d), sorted(spy_d - nav_d)
    if only_nav:
        fails.append(("misaligned", "dates in NAV but not SPY: " + ", ".join(map(str, only_nav))))
    if only_spy:
        fails.append(("misaligned", "dates in SPY but not NAV: " + ", ".join(map(str, only_spy))))

    run = best = 1
    end = dates[0]
    for i in range(1, len(vals)):
        run = run + 1 if vals[i] == vals[i - 1] else 1
        if run > best:
            best, end = run, dates[i]
    if best >= FLAT_RUN:
        fails.append(("flat", f"NAV identical for {best} consecutive observations ending {end} (possible stalled runner)"))

    cum = spy_ret = None
    if not bad:
        spy_map = dict(spy)
        common = sorted(nav_d & spy_d)
        if len(common) >= 2:
            nav_map = dict(nav)
            a, b = common[0], common[-1]
            if spy_map[a] > 0:
                cum = nav_map[b] / nav_map[a] - 1
                spy_ret = spy_map[b] / spy_map[a] - 1
    return fails, cum, spy_ret, False


def run(nav_dir, spy_path, today, out=print):
    """Return (exit_code, failures list of (portfolio, check, detail))."""
    failures = []
    files = sorted(Path(nav_dir).glob("*.csv")) if Path(nav_dir).is_dir() else []
    if not files:
        out(f"ERROR: no NAV files found in {nav_dir}")
        return 2, [("*", "malformed", f"no NAV files in {nav_dir}")]
    try:
        spy = read_series(spy_path, "close")
    except ValueError as e:
        out(f"ERROR: SPY series: {e}")
        return 2, [("SPY", "malformed", str(e))]
    if any(not math.isfinite(v) or v <= 0 for _, v in spy):
        out("ERROR: SPY series contains zero/negative/NaN/inf prices")
        return 2, [("SPY", "bad_value", "invalid SPY prices")]

    rows = []
    for f in files:
        name = f.stem
        try:
            nav = read_series(f, "nav")
        except ValueError as e:
            failures.append((name, "malformed", str(e)))
            rows.append((name, "-", "-", "-", "-", "FAIL: malformed"))
            continue
        fl, cum, sr, insuff = check_portfolio(nav, spy, today)
        failures += [(name, c, d) for c, d in fl]
        pct = lambda x: "n/a" if x is None else f"{x:+.2%}"
        excess = "n/a" if cum is None or sr is None else f"{cum - sr:+.2%}"
        status = "OK" if not fl else "FAIL: " + ",".join(sorted({c for c, _ in fl}))
        rows.append((name, str(len(nav)), pct(cum), pct(sr), excess, status))

    hdr = ("portfolio", "obs", "cum_ret", "spy_ret", "excess", "status")
    w = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(6)]
    for r in [hdr] + rows:
        out("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))
    for name, c, d in failures:
        out(f"FAIL {name} [{c}]: {d}")
    return (1 if failures else 0), failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nav-dir", required=True)
    ap.add_argument("--spy", required=True)
    ap.add_argument("--today", help="YYYY-MM-DD (default: system date)")
    a = ap.parse_args(argv)
    today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()
    code, _ = run(a.nav_dir, a.spy, today)
    return code


if __name__ == "__main__":
    sys.exit(main())
