#!/usr/bin/env python3
"""Read-only point-in-time check of a universe membership file.

Input: CSV (header symbol,added_date,removed_date) or JSON (list of such objects).
Blank removed_date = still a member. Dates are ISO (YYYY-MM-DD); membership is the
half-open interval [added_date, removed_date).
Optional usage file (CSV asof_date,symbol / JSON list): the symbols a backtest actually
used on each as-of date; flags look-ahead (added after the date), already-removed and
unknown symbols. Without it, look-ahead cannot be seen from membership alone.

Checks: lookahead, survivorship, interval (removed<added / overlap), bad_row
(missing/unparseable date, empty symbol), small_universe (<20 members: 'insufficient
data', not computed), bad_file (missing/empty/malformed file).
Nothing is skipped silently; any finding => exit 1 naming file, symbol and check.
Usage: universe_pit_check.py FILE [--asof YYYY-MM-DD ...] [--usage FILE] [--min-members N]
"""
import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

MIN_MEMBERS = 20
SURVIVORSHIP_YEARS = 3


class Finding:
    def __init__(self, file, symbol, check, detail):
        self.file, self.symbol, self.check, self.detail = str(file), symbol, check, detail

    def __str__(self):
        return f"FAIL file={self.file} symbol={self.symbol or '-'} check={self.check}: {self.detail}"


class BadFile(Exception):
    pass


def _read_records(path, kind="universe"):
    p = Path(path)
    if not p.is_file():
        raise BadFile(f"{kind} file not found")
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise BadFile(f"{kind} file unreadable: {e}")
    if not text.strip():
        raise BadFile(f"{kind} file is empty")
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise BadFile(f"malformed JSON: {e}")
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise BadFile("JSON must be a list of objects")
        recs = data
    else:
        try:
            recs = list(csv.DictReader(text.splitlines()))
        except csv.Error as e:
            raise BadFile(f"malformed CSV: {e}")
        if any(None in r for r in recs):
            raise BadFile("malformed CSV: row with more fields than header")
    if not recs:
        raise BadFile(f"{kind} file has a header but no rows")
    return recs


def _need_columns(recs, cols, kind):
    have = set().union(*(r.keys() for r in recs))
    missing = [c for c in cols if c not in have]
    if missing:
        raise BadFile(f"{kind} file missing column(s): {', '.join(missing)}")


def _parse_date(v):
    if v is None or str(v).strip() == "":
        return None, "missing"
    try:
        return date.fromisoformat(str(v).strip()), None
    except ValueError:
        return None, f"unparseable {v!r}"


def load_universe(path):
    """-> (valid rows [(symbol, added, removed|None, lineno)], findings)."""
    recs = _read_records(path)
    _need_columns(recs, ["symbol", "added_date", "removed_date"], "universe")
    rows, findings = [], []
    for i, r in enumerate(recs, start=1):
        sym = str(r.get("symbol") or "").strip()
        added, ea = _parse_date(r.get("added_date"))
        removed, er = _parse_date(r.get("removed_date"))
        errs = []
        if not sym:
            errs.append("empty symbol")
        if ea:
            errs.append(f"added_date {ea}")
        if er and er != "missing":
            errs.append(f"removed_date {er}")
        if errs:
            findings.append(Finding(path, sym, "bad_row", f"row {i}: " + "; ".join(errs)))
            continue
        rows.append((sym, added, removed, i))
    return rows, findings


def members_asof(rows, asof):
    return {s for s, a, r, _ in rows if a <= asof and (r is None or asof < r)}


def default_asofs(rows):
    if not rows:
        return []
    lo = min(a for _, a, _, _ in rows)
    hi = max([a for _, a, _, _ in rows] + [r for _, _, r, _ in rows if r])
    return [date(y, 12, 31) for y in range(lo.year, hi.year + 1)
            if lo <= date(y, 12, 31) <= hi]


def load_usage(path):
    recs = _read_records(path, "usage")
    _need_columns(recs, ["asof_date", "symbol"], "usage")
    usage, findings = {}, []
    for i, r in enumerate(recs, start=1):
        d, err = _parse_date(r.get("asof_date"))
        sym = str(r.get("symbol") or "").strip()
        if err or not sym:
            findings.append(Finding(path, sym, "bad_row", f"usage row {i}: "
                                    + (f"asof_date {err}" if err else "empty symbol")))
            continue
        usage.setdefault(d, set()).add(sym)
    return usage, findings


def check_universe(rows, path, asofs=None, usage=None, min_members=MIN_MEMBERS):
    """-> (table [(asof, count|None, [flags])], findings). rows must be valid rows."""
    findings = []
    # (c) inverted and overlapping intervals
    by_sym = {}
    for s, a, r, ln in rows:
        if r is not None and r < a:
            findings.append(Finding(path, s, "interval", f"row {ln}: removed_date {r} before added_date {a}"))
            continue
        by_sym.setdefault(s, []).append((a, r, ln))
    for s, ivs in by_sym.items():
        ivs.sort(key=lambda x: x[0])
        for (a1, r1, l1), (a2, r2, l2) in zip(ivs, ivs[1:]):
            if r1 is None or a2 < r1:
                findings.append(Finding(path, s, "interval",
                                        f"rows {l1} and {l2} overlap ({a1}..{r1 or 'open'} vs {a2}..{r2 or 'open'})"))
    # (b) survivorship: multi-year span, nobody ever removed
    if rows:
        lo = min(a for _, a, _, _ in rows)
        hi = max([a for _, a, _, _ in rows] + [r for _, _, r, _ in rows if r])
        years = (hi - lo).days / 365.25
        if not any(r for _, _, r, _ in rows) and years >= SURVIVORSHIP_YEARS:
            findings.append(Finding(path, "-", "survivorship",
                                    f"no removed/delisted symbols over {lo}..{hi} ({years:.1f}y): possible survivorship bias"))
    # (a) look-ahead use
    info = {}
    for s, a, r, _ in rows:
        info.setdefault(s, []).append((a, r))
    for d in sorted(usage or {}):
        for s in sorted(usage[d]):
            if s not in info:
                findings.append(Finding(path, s, "lookahead", f"used on {d} but not in universe file"))
            elif min(a for a, _ in info[s]) > d:
                findings.append(Finding(path, s, "lookahead",
                                        f"used on {d} but added_date is {min(a for a, _ in info[s])}"))
            elif not any(a <= d and (r is None or d < r) for a, r in info[s]):
                findings.append(Finding(path, s, "lookahead", f"used on {d} but not a member then (removed)"))
    # (e) table with size check
    table = []
    for d in (asofs if asofs is not None else default_asofs(rows)):
        n = len(members_asof(rows, d))
        flags = []
        if n < min_members:
            flags.append("insufficient data")
            findings.append(Finding(path, "-", "small_universe",
                                    f"as-of {d}: {n} members < {min_members}; insufficient data, not computed"))
        table.append((d, n, flags))
    if not table:
        findings.append(Finding(path, "-", "small_universe", "no as-of dates to evaluate; nothing computed"))
    return table, findings


def format_table(table, findings):
    per_date = {}
    for f in findings:
        for d, _, _ in table:
            if f"as-of {d}:" in f.detail or f" on {d} " in f.detail:
                per_date.setdefault(d, set()).add(f.check)
    lines = [f"{'as_of':<12}{'members':>8}  flags"]
    for d, n, flags in table:
        fl = sorted(set(flags) | per_date.get(d, set()))
        lines.append(f"{d!s:<12}{('n/a' if 'insufficient data' in flags else n)!s:>8}  {', '.join(fl) or 'ok'}")
    return "\n".join(lines)


def run(path, asofs=None, usage_path=None, min_members=MIN_MEMBERS, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    findings, usage = [], None
    try:
        rows, findings = load_universe(path)
        if usage_path:
            usage, uf = load_usage(usage_path)
            findings += uf
    except BadFile as e:
        findings.append(Finding(usage_path if usage_path and Path(path).is_file() else path, "-", "bad_file", str(e)))
        for f in findings:
            print(f, file=err)
        return 1
    table, more = check_universe(rows, path, asofs, usage, min_members)
    findings += more
    print(format_table(table, findings), file=out)
    for f in findings:
        print(f, file=err)
    print(f"{'FAILED' if findings else 'OK'}: {len(findings)} finding(s) in {path}", file=out)
    return 1 if findings else 0


def main(argv=None, asofs=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--asof", action="append", default=None, help="as-of date (repeatable); default year-ends")
    ap.add_argument("--usage", help="CSV/JSON of asof_date,symbol actually used by a backtest")
    ap.add_argument("--min-members", type=int, default=MIN_MEMBERS)
    a = ap.parse_args(argv)
    if a.asof:
        asofs = []
        for s in a.asof:
            d, e = _parse_date(s)
            if e:
                print(f"FAIL file={a.file} symbol=- check=bad_row: --asof {e}", file=sys.stderr)
                return 1
            asofs.append(d)
    return run(a.file, asofs, a.usage, a.min_members)


if __name__ == "__main__":
    sys.exit(main())
