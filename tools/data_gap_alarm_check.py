#!/usr/bin/env python3
"""Read-only data-gap / stale-data alarm for daily price files.

Usage: data_gap_alarm_check.py PRICE_DIR [--today YYYY-MM-DD] [--max-stale-bdays N]
PRICE_DIR holds one <SYMBOL>.csv per symbol (columns: date, close; optional split)
and must contain SPY.csv, which defines the reference calendar.
Business days = Mon-Fri (no holiday calendar). Exit 1 on any failure, naming
file, symbol and check; nothing is skipped silently. Never writes, never uses network.
"""
import argparse
import csv
import datetime as dt
import math
import sys
from pathlib import Path

MIN_OBS = 20
MAX_JUMP = 0.40
FLAT_RUN = 5
MAX_STALE_BDAYS = 3


class MalformedFile(Exception):
    pass


def bdays_between(a, b):
    """Weekdays in (a, b]; 0 if b <= a."""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def bdays_range(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def load_prices(path):
    """-> list of (date, close, split_flag) in file order. Raises MalformedFile."""
    path = Path(path)
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise MalformedFile(f"{path}: unreadable ({e})")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise MalformedFile(f"{path}: empty file")
    head = [h.strip().lower() for h in rows[0]]
    if "date" not in head or "close" not in head:
        raise MalformedFile(f"{path}: header must include date and close, got {head}")
    di, ci = head.index("date"), head.index("close")
    si = head.index("split") if "split" in head else None
    out = []
    for n, r in enumerate(rows[1:], start=2):
        if not r or not any(c.strip() for c in r):
            continue
        try:
            d = dt.date.fromisoformat(r[di].strip())
            raw = r[ci].strip()
            c = float(raw) if raw else float("nan")
            sp = bool(si is not None and len(r) > si and r[si].strip().lower() in ("1", "true", "yes", "y"))
        except (ValueError, IndexError) as e:
            raise MalformedFile(f"{path}: line {n} unparseable ({e})")
        out.append((d, c, sp))
    return out


def check_series(rows, spy_dates, today, max_stale=MAX_STALE_BDAYS):
    """-> dict(last, gaps[list], coverage, flags[list]). Pure; no I/O."""
    flags = []
    if len(rows) < MIN_OBS:
        return {"last": max((r[0] for r in rows), default=None), "gaps": None,
                "coverage": None, "flags": ["insufficient data"]}
    dates = [r[0] for r in rows]
    if len(set(dates)) != len(dates):
        flags.append("duplicate dates")
    if dates != sorted(dates):
        flags.append("non-monotonic dates")
    last = max(dates)
    if bdays_between(last, today) > max_stale:
        flags.append(f"stale: last {last} is {bdays_between(last, today)} bdays before {today}")
    ordered = sorted(rows, key=lambda r: r[0])
    for i, (d, c, sp) in enumerate(ordered):
        if math.isnan(c) or math.isinf(c) or c <= 0:
            flags.append(f"bad price {c} on {d}")
    good = [r for r in ordered if math.isfinite(r[1]) and r[1] > 0]
    for a, b in zip(good, good[1:]):
        if abs(b[1] / a[1] - 1) > MAX_JUMP and not b[2]:
            flags.append(f"jump {b[1] / a[1] - 1:+.0%} on {b[0]} without split flag")
    uniq = sorted(set(dates))
    spy_in_span = [d for d in spy_dates if uniq[0] <= d <= uniq[-1]]
    have = set(uniq)
    gaps = [d for d in spy_in_span if d not in have]
    if gaps:
        flags.append("gaps: " + ",".join(map(str, gaps)))
    coverage = (len(spy_in_span) - len(gaps)) / len(spy_in_span) if spy_in_span else 0.0
    run = 1
    for a, b in zip(ordered, ordered[1:]):
        run = run + 1 if b[1] == a[1] else 1
        if run == FLAT_RUN:
            flags.append(f"flat price {b[1]} for {FLAT_RUN}+ days ending {b[0]} (stale feed?)")
    return {"last": last, "gaps": gaps, "coverage": coverage, "flags": flags}


def run(price_dir, today, max_stale=MAX_STALE_BDAYS, out=print):
    """Returns list of failure strings (empty = pass)."""
    pdir = Path(price_dir)
    failures = []
    files = sorted(pdir.glob("*.csv")) if pdir.is_dir() else []
    if not files:
        return [f"{pdir}: no price files found (check: files-present)"]
    spy_path = pdir / "SPY.csv"
    try:
        spy_dates = sorted({r[0] for r in load_prices(spy_path)})
    except (MalformedFile, FileNotFoundError) as e:
        return [f"{spy_path}: SPY (symbol SPY) calendar unavailable (check: spy-calendar): {e}"]
    results = []
    for f in files:
        sym = f.stem
        try:
            res = check_series(load_prices(f), spy_dates, today, max_stale)
        except MalformedFile as e:
            failures.append(f"{f}: symbol {sym} check malformed-file: {e}")
            results.append((sym, "-", "-", "-", "MALFORMED"))
            continue
        for fl in res["flags"]:
            failures.append(f"{f}: symbol {sym} check {fl.split(':')[0].split(' on ')[0]}: {fl}")
        g = "n/a" if res["gaps"] is None else str(len(res["gaps"]))
        cov = "n/a" if res["coverage"] is None else f"{res['coverage']:.3f}"
        results.append((sym, str(res["last"]), g, cov,
                        "; ".join(res["flags"]) or "ok"))
    out(f"{'symbol':<10}{'last date':<12}{'gaps':<6}{'spy cov':<9}flags")
    for r in results:
        out(f"{r[0]:<10}{r[1]:<12}{r[2]:<6}{r[3]:<9}{r[4]}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("price_dir")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--max-stale-bdays", type=int, default=MAX_STALE_BDAYS)
    a = ap.parse_args(argv)
    failures = run(a.price_dir, a.today or dt.date.today(), a.max_stale_bdays)
    for f in failures:
        print("FAIL " + f, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
