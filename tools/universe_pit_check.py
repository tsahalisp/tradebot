#!/usr/bin/env python3
"""Read-only point-in-time (PIT) universe checker.

Input CSV, one of two shapes (header required):
  intervals: symbol,added[,removed]       observation dates = month-ends, first add .. as-of
  snapshots: date,symbol[,added][,removed]  one row per member per observation date
Empty `removed` = still a member today. Removal date is exclusive (not a member ON it).

Checks (any failure => exit 1 naming file + check; unreadable/malformed file => exit 2):
  SURVIVORSHIP        dates >=1y before as-of exist but no member was ever removed/delisted
  FUTURE_ADD          added date after the date the universe is used (or after as-of)
  TRUNCATION          universe at a date differs when only data known at that date is used
  DUP_SYMBOL / REMOVED_BEFORE_ADDED / BAD_DATE   row-level data errors (never skipped silently)
  EMPTY_UNIVERSE      a date with no members
  INSUFFICIENT_DATA   no valid rows or < MIN_DATES observation dates; not computed
Nothing is written; no network.
"""
import argparse
import calendar
import csv
import sys
from datetime import date, timedelta

MIN_DATES = 20
SURV_LOOKBACK_DAYS = 365


class InputError(Exception):
    pass


class Report:
    def __init__(self, path, as_of):
        self.path, self.as_of = str(path), as_of
        self.rows = []        # (date, size, delisted, [flags])
        self.failures = []    # (check, detail)
        self.insufficient = False

    @property
    def ok(self):
        return not self.failures

    def fail(self, check, detail):
        self.failures.append((check, detail))


def _parse(s):
    s = (s or "").strip()
    if not s:
        return None
    return date.fromisoformat(s)


def month_ends(start, end):
    out, y, m = [], start.year, start.month
    while True:
        d = date(y, m, calendar.monthrange(y, m)[1])
        if d > end:
            return out
        if d >= start:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def load(path, rep):
    """Returns (mode, rows). rows: dict(symbol, added, removed, date, line). Bad rows -> rep.failures."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            hdr = [h.strip().lower() for h in (rd.fieldnames or [])]
            raw = [{(k or "").strip().lower(): (v or "") for k, v in r.items()} for r in rd]
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise InputError(f"cannot read: {e}")
    if "symbol" not in hdr or not ({"added", "date"} & set(hdr)):
        raise InputError(f"malformed header {hdr}: need symbol,added[,removed] or date,symbol[,added][,removed]")
    mode = "snapshots" if "date" in hdr else "intervals"
    rows = []
    for i, r in enumerate(raw, start=2):
        sym = r.get("symbol", "").strip().upper()
        try:
            row = {"symbol": sym, "line": i, "date": None,
                   "added": _parse(r.get("added")), "removed": _parse(r.get("removed"))}
            if mode == "snapshots":
                row["date"] = _parse(r.get("date"))
                if row["date"] is None:
                    raise ValueError("missing date")
            elif row["added"] is None:
                raise ValueError("missing added date")
        except ValueError as e:
            rep.fail("BAD_DATE", f"line {i} {sym or '?'}: {e}")
            continue
        if not sym:
            rep.fail("BAD_SYMBOL", f"line {i}: missing symbol")
            continue
        if row["added"] and row["removed"] and row["removed"] < row["added"]:
            rep.fail("REMOVED_BEFORE_ADDED", f"line {i} {sym}: {row['removed']} < {row['added']}")
            continue
        rows.append(row)
    return mode, rows


def _members(intervals, d):
    return {s for s, a, r in intervals if a is not None and a <= d and (r is None or d < r)}


def _truncated(intervals, d):
    """Only what was knowable at d: rows added by d; removals after d unknown."""
    return [(s, a, (r if r is not None and r <= d else None)) for s, a, r in intervals
            if a is not None and a <= d]


def check_file(path, as_of=None, min_dates=MIN_DATES):
    as_of = as_of or date.today()
    rep = Report(path, as_of)
    mode, rows = load(path, rep)

    if mode == "intervals":
        seen = {}
        for r in rows:
            for o in seen.setdefault(r["symbol"], []):
                lo = max(o["added"], r["added"])
                hi = min(o["removed"] or date.max, r["removed"] or date.max)
                if lo < hi:
                    rep.fail("DUP_SYMBOL", f"{r['symbol']} line {r['line']} overlaps line {o['line']}")
                    break
            seen[r["symbol"]].append(r)
        intervals = [(r["symbol"], r["added"], r["removed"]) for r in rows]
        if rows:
            dates = month_ends(min(r["added"] for r in rows), as_of)
        else:
            dates = []
        stored = None
    else:
        stored, per_sym = {}, {}
        for r in rows:
            if r["symbol"] in stored.setdefault(r["date"], set()):
                rep.fail("DUP_SYMBOL", f"{r['symbol']} twice on {r['date']} (line {r['line']})")
            stored[r["date"]].add(r["symbol"])
            per_sym.setdefault(r["symbol"], []).append(r)
        dates = sorted(stored)
        last = dates[-1] if dates else None
        intervals = []
        for s, rs in per_sym.items():
            ds = sorted(x["date"] for x in rs)
            adds = [x["added"] for x in rs if x["added"]]
            rems = [x["removed"] for x in rs if x["removed"]]
            if adds:
                a, rm = min(adds), (max(rems) if rems else None)
            else:  # derive from appearances
                a = ds[0]
                nxt = [d for d in dates if d > ds[-1]]
                rm = nxt[0] if nxt else None
            intervals.append((s, a, rm))
        for r in rows:
            if r["added"] and r["added"] > r["date"]:
                rep.fail("FUTURE_ADD", f"{r['symbol']} added {r['added']} but used on {r['date']} (line {r['line']})")

    future = sorted(s for s, a, _ in intervals if a and a > as_of)
    if future:
        rep.fail("FUTURE_ADD", f"added after as-of {as_of}: {','.join(future)}")
    if mode == "snapshots" and dates and dates[-1] > as_of:
        rep.fail("FUTURE_ADD", f"snapshot dated {dates[-1]} is after as-of {as_of}")

    if len(dates) < min_dates:
        rep.insufficient = True
        rep.fail("INSUFFICIENT_DATA", f"{len(dates)} observation dates < {min_dates}; not computed")
        return rep

    removed_ok = {s for s, a, r in intervals if r is not None and r <= as_of}
    eligible = delisted_seen = 0
    for d in dates:
        flags = []
        full = stored[d] if stored is not None else _members(intervals, d)
        trunc = _members(_truncated(intervals, d), d)
        delisted = len(full & removed_ok)
        if not full:
            flags.append("EMPTY")
            rep.fail("EMPTY_UNIVERSE", f"no members on {d}")
        if full != trunc:
            flags.append("TRUNC")
            rep.fail("TRUNCATION", f"{d}: full-only={sorted(full - trunc)} trunc-only={sorted(trunc - full)}")
        if stored is not None and any(a and a > d for s, a, _ in intervals if s in full):
            flags.append("FUTURE")
        if (as_of - d).days >= SURV_LOOKBACK_DAYS:
            eligible += 1
            delisted_seen += delisted > 0
            if delisted == 0:
                flags.append("SURV")
        rep.rows.append((d, len(full), delisted, flags))
    if eligible and delisted_seen == 0:
        rep.fail("SURVIVORSHIP", f"{eligible} dates >=1y old, none contains a name removed/delisted by {as_of}")
    return rep


def print_report(rep, out=None):
    out = out or sys.stdout
    print(f"== {rep.path} (as-of {rep.as_of}) ==", file=out)
    if rep.insufficient:
        print("insufficient data - not computed", file=out)
        return
    print(f"{'date':<12}{'size':>6}{'delisted':>10}  flags", file=out)
    for d, n, k, fl in rep.rows:
        print(f"{d!s:<12}{n:>6}{k:>10}  {','.join(fl) or '-'}", file=out)


def synthetic(path, seed=0, n_names=60, years=4, as_of=None, survivors_only=False):
    """Write a deterministic clean PIT fixture (churn, delistings) to `path`."""
    import random
    rng = random.Random(seed)
    as_of = as_of or date(2024, 12, 31)
    start = as_of - timedelta(days=365 * years)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "added", "removed"])
        for i in range(n_names):
            a = start - timedelta(days=rng.randint(0, 400)) if i < n_names // 2 else \
                start + timedelta(days=rng.randint(0, 365 * years - 200))
            rem = ""
            if not survivors_only and i % 3 == 0:
                rem = (max(a, start) + timedelta(days=rng.randint(100, 600))).isoformat()
                if rem >= as_of.isoformat():
                    rem = ""
            w.writerow([f"S{i:03d}", a.isoformat(), rem])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None, help="YYYY-MM-DD (default today)")
    a = ap.parse_args(argv)
    if not a.files:
        print("FAIL (no files given): INPUT: nothing to check", file=sys.stderr)
        return 2
    code = 0
    for p in a.files:
        try:
            rep = check_file(p, a.as_of)
        except InputError as e:
            print(f"FAIL {p}: INPUT: {e}", file=sys.stderr)
            code = max(code, 2)
            continue
        print_report(rep)
        for check, detail in rep.failures:
            print(f"FAIL {p}: {check}: {detail}", file=sys.stderr)
        if not rep.ok:
            code = max(code, 1)
    return code


if __name__ == "__main__":
    sys.exit(main())
