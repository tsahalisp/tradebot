#!/usr/bin/env python3
"""Read-only data-gap / stale-data checker for price files (CSV, or parquet if pandas+pyarrow exist).

Usage: python tools/data_gap_check.py [--today YYYY-MM-DD] PATH [PATH ...]
PATH is a file or a directory (scanned for *.csv / *.parquet). Never writes, never touches network.

Checks per file (flag names):
  malformed          unreadable file / missing date or price column / unparseable date or price text
  insufficient_data  fewer than MIN_OBS observations -> price checks NOT computed (still a failure)
  gap                > MAX_GAP_BDAYS missing business days between consecutive dates (dates listed)
  dup_or_unordered   duplicate or non-monotonic dates
  bad_value          price zero, negative, NaN or inf
  suspect_split      single-day move > MAX_JUMP with no split flag (possible unadjusted split)
  stale              last date more than MAX_STALE_BDAYS business days before `today`
Exit code: 0 all files clean, 1 any flag (file + check named on stderr), 2 usage error.
"""
import argparse
import csv
import datetime as dt
import math
import sys
from pathlib import Path

MIN_OBS = 20
MAX_GAP_BDAYS = 3
MAX_STALE_BDAYS = 3
MAX_JUMP = 0.5

DATE_COLS = ("date", "datetime", "timestamp", "time")
PRICE_COLS = ("adj_close", "adj close", "adjclose", "close", "price")
SPLIT_COLS = ("split", "split_flag", "splits", "stock splits", "is_split")


class Malformed(Exception):
    pass


def bdays_between(a, b):
    """Business days strictly between dates a < b (Mon-Fri; holidays not modelled)."""
    n, d = 0, a + dt.timedelta(days=1)
    while d < b:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def missing_bdays(a, b):
    out, d = [], a + dt.timedelta(days=1)
    while d < b:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _pick(cols, names):
    low = {c.strip().lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def _truthy(v):
    if v is None:
        return False
    s = str(v).strip().lower()
    if s in ("", "0", "0.0", "false", "no", "nan", "none"):
        return False
    try:
        return float(s) != 0.0
    except ValueError:
        return True


def _parse_date(s):
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        raise Malformed(f"unparseable date {s!r}")


def _parse_price(s):
    t = str(s).strip()
    if t == "" or t.lower() in ("nan", "none", "null"):
        return float("nan")
    try:
        return float(t)  # accepts inf / -inf
    except ValueError:
        raise Malformed(f"unparseable price {s!r}")


def _rows_from_dicts(records, cols):
    dcol, pcol = _pick(cols, DATE_COLS), _pick(cols, PRICE_COLS)
    if dcol is None or pcol is None:
        raise Malformed(f"need a date column {DATE_COLS} and a price column {PRICE_COLS}; got {list(cols)}")
    scol = _pick(cols, SPLIT_COLS)
    rows = [(_parse_date(r[dcol]), _parse_price(r[pcol]), _truthy(r[scol]) if scol else False)
            for r in records]
    return rows


def load_rows(path):
    """-> list of (date, price, split_flag) in file order. Raises Malformed."""
    path = Path(path)
    if not path.is_file():
        raise Malformed("file not found or not a regular file")
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with open(path, newline="") as f:
                rd = csv.DictReader(f)
                if not rd.fieldnames:
                    raise Malformed("empty file / no header")
                records = list(rd)
                if any(None in r or None in r.values() for r in records):
                    raise Malformed("ragged rows (column count differs from header)")
                return _rows_from_dicts(records, rd.fieldnames)
        if suffix == ".parquet":
            try:
                import pandas as pd
            except ImportError:
                raise Malformed("parquet needs pandas+pyarrow, not installed (file NOT checked)")
            df = pd.read_parquet(path)
            if df.index.name and df.index.name.lower() in DATE_COLS:
                df = df.reset_index()
            df = df.astype(object).where(df.notna(), "nan")
            return _rows_from_dicts(df.to_dict("records"), list(df.columns))
    except Malformed:
        raise
    except Exception as e:  # corrupt parquet, bad encoding, ...
        raise Malformed(f"cannot read: {type(e).__name__}: {e}")
    raise Malformed(f"unsupported extension {suffix!r}")


def check_rows(rows, today):
    """-> list of (check, detail) flags for one series."""
    flags = []
    if len(rows) < MIN_OBS:
        return [("insufficient_data",
                 f"{len(rows)} observations < {MIN_OBS}; remaining checks not computed")]
    dates = [r[0] for r in rows]
    dups = sorted({d for d in dates if dates.count(d) > 1}) if len(set(dates)) != len(dates) else []
    unordered = [dates[i] for i in range(1, len(dates)) if dates[i] < dates[i - 1]]
    if dups or unordered:
        parts = []
        if dups:
            parts.append("duplicate dates " + ", ".join(map(str, dups[:10])))
        if unordered:
            parts.append("out-of-order at " + ", ".join(map(str, unordered[:10])))
        flags.append(("dup_or_unordered", "; ".join(parts)))

    bad = [(d, p) for d, p, _ in rows if not math.isfinite(p) or p <= 0]
    if bad:
        flags.append(("bad_value", f"{len(bad)} bad prices, e.g. " +
                      ", ".join(f"{d}={p}" for d, p in bad[:5])))

    # remaining checks on the sorted, de-duplicated, valid series
    seen = {}
    for d, p, s in rows:
        if math.isfinite(p) and p > 0:
            seen[d] = (p, s)
    series = sorted(seen.items())
    gaps = []
    for (a, _), (b, _) in zip(series, series[1:]):
        if bdays_between(a, b) > MAX_GAP_BDAYS:
            m = missing_bdays(a, b)
            gaps.append(f"{a}->{b} missing {len(m)} bdays [{', '.join(map(str, m[:8]))}{' ...' if len(m) > 8 else ''}]")
    if gaps:
        flags.append(("gap", "; ".join(gaps[:5]) + (f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else "")))

    jumps = []
    for (a, (pa, _)), (b, (pb, sb)) in zip(series, series[1:]):
        if abs(pb / pa - 1) > MAX_JUMP and not sb:
            jumps.append(f"{b} {pa:g}->{pb:g} ({pb / pa - 1:+.0%})")
    if jumps:
        flags.append(("suspect_split", "no split flag: " + "; ".join(jumps[:5])))

    last = max(dates)
    if last <= today:
        lag = bdays_between(last, today) + (1 if last < today and today.weekday() < 5 else 0)
        if lag > MAX_STALE_BDAYS:
            flags.append(("stale", f"last date {last} is {lag} business days before {today}"))
    return flags


def check_file(path, today):
    try:
        rows = load_rows(path)
    except Malformed as e:
        return 0, None, [("malformed", str(e))]
    return len(rows), max((r[0] for r in rows), default=None), check_rows(rows, today)


def expand(paths):
    out, errs = [], []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            found = sorted(list(p.glob("*.csv")) + list(p.glob("*.parquet")))
            if not found:
                errs.append((str(p), "malformed", "directory contains no .csv/.parquet files"))
            out += found
        else:
            out.append(p)  # nonexistent paths are reported by check_file, not skipped
    return out, errs


def run(paths, today, out=sys.stdout, err=sys.stderr):
    files, failures = expand(paths)
    if not files and not failures:
        failures.append(("(none)", "malformed", "no input files given"))
    table = []
    for f, c, d in failures:
        table.append((f, "-", "-", "FAIL", c))
    for f in files:
        n, last, flags = check_file(f, today)
        table.append((str(f), str(n), str(last or "-"),
                      "FAIL" if flags else "ok", ",".join(c for c, _ in flags) or "-"))
        failures += [(str(f), c, d) for c, d in flags]
    hdr = ("file", "obs", "last_date", "status", "flags")
    w = [max(len(str(r[i])) for r in table + [hdr]) for i in range(5)]
    for r in [hdr] + table:
        print("  ".join(str(x).ljust(w[i]) for i, x in enumerate(r)), file=out)
    for f, c, d in failures:
        print(f"FAIL {f}: {c}: {d}", file=err)
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--today", help="YYYY-MM-DD (default: system date)")
    a = ap.parse_args(argv)
    try:
        today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    except ValueError:
        print(f"bad --today {a.today!r}", file=sys.stderr)
        return 2
    return run(a.paths, today)


if __name__ == "__main__":
    sys.exit(main())
