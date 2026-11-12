"""Read-only completeness/freshness check for the weekly paper-portfolio report.

Finding (see task report): the repo has NO weekly report writer. autoportfolio.py only
marks NAV daily into SQLite and rebalances every REBAL_DAYS=7; nothing emits a weekly
NAV-vs-SPY / drawdown / turnover report file, so nothing validates one. This tool
therefore defines the JSON shape it expects and fails loudly when it is not met:

  {"as_of": "YYYY-MM-DD",
   "benchmark": {"name": "SPY", "return_pct": <num>},
   "portfolios": [{"name": "core", "as_of": "YYYY-MM-DD" (optional, defaults to report),
                   "nav": <num>, "excess_return_pct": <num>,   # return vs SPY
                   "drawdown_pct": <num>, "turnover_pct": <num>,
                   "n_obs": <int>, "placebo": <bool, optional>}]}

A portfolio with n_obs < MIN_OBS must carry null figures (not computed) and is flagged
INSUFFICIENT_DATA. Any flag => exit 1. Never writes anything, never touches the network.

Usage: weekly_report_completeness_check.py REPORT.json --expect core,congress [--today YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import math
import sys

REQUIRED = ("nav", "excess_return_pct", "drawdown_pct", "turnover_pct")
MIN_OBS = 20
MAX_AGE_DAYS = 7
PLACEBO_TOL = 1e-6


def _bad(v):
    if v is None or isinstance(v, bool):
        return True
    if isinstance(v, str):
        if not v.strip():
            return True
        try:
            v = float(v)
        except ValueError:
            return True
    if not isinstance(v, (int, float)):
        return True
    return not math.isfinite(v)


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def check_report(report, expected, today):
    """Return list of rows {portfolio, fields, as_of, flags}. Pure; raises nothing on bad data."""
    rows = []
    if not isinstance(report, dict):
        return [{"portfolio": "<report>", "fields": "-", "as_of": "-", "flags": ["MALFORMED: top level is not an object"]}]
    plist = report.get("portfolios")
    if not isinstance(plist, list):
        return [{"portfolio": "<report>", "fields": "-", "as_of": "-", "flags": ["MALFORMED: 'portfolios' missing or not a list"]}]
    byname = {p.get("name"): p for p in plist if isinstance(p, dict)}
    bench = report.get("benchmark")
    bflags = []
    if not isinstance(bench, dict) or str(bench.get("name", "")).upper() != "SPY":
        bflags.append("SPY_MISSING: no SPY benchmark row")
    elif _bad(bench.get("return_pct")):
        bflags.append("SPY_MISSING: SPY benchmark value missing/NaN/inf")
    rows.append({"portfolio": "SPY (benchmark)", "fields": "-", "as_of": str(report.get("as_of", "-")), "flags": bflags})

    rdate = _date(report.get("as_of"))
    for name in expected:
        p = byname.get(name)
        if p is None:
            rows.append({"portfolio": name, "fields": "0/%d" % len(REQUIRED), "as_of": "-",
                         "flags": ["MISSING_PORTFOLIO: not in report"]})
            continue
        flags = []
        present = [f for f in REQUIRED if not _bad(p.get(f))]
        raw_asof = p.get("as_of", report.get("as_of"))
        d = _date(raw_asof)
        if d is None:
            flags.append("STALE_OR_BAD_DATE: as-of date missing/unparseable")
        elif (today - d).days > MAX_AGE_DAYS:
            flags.append("STALE: as-of %s is %d days before %s (max %d)" % (d, (today - d).days, today, MAX_AGE_DAYS))
        n = p.get("n_obs")
        if _bad(n):
            flags.append("MISSING_FIELD: n_obs")
        elif n < MIN_OBS:
            flags.append("INSUFFICIENT_DATA: %d observations < %d" % (n, MIN_OBS))
            computed = [f for f in REQUIRED if f != "nav" and not (p.get(f) is None)]
            if computed:
                flags.append("COMPUTED_ON_SHORT_SERIES: %s must be null when insufficient data" % ",".join(computed))
        if not (isinstance(n, (int, float)) and not _bad(n) and n < MIN_OBS):
            for f in REQUIRED:
                if f not in present:
                    flags.append("MISSING_FIELD: %s missing/empty/NaN/inf" % f)
        if p.get("placebo") and "excess_return_pct" in present and abs(p["excess_return_pct"]) > PLACEBO_TOL:
            flags.append("PLACEBO_NONZERO_EXCESS: SPY placebo shows %r vs SPY" % p["excess_return_pct"])
        rows.append({"portfolio": name, "fields": "%d/%d" % (len(present), len(REQUIRED)),
                     "as_of": str(d) if d else "-", "flags": flags})
    return rows


def load(path):
    """Return (report, error). Missing/unreadable/malformed files are errors, never skipped."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh, parse_constant=float), None
    except FileNotFoundError:
        return None, "report file not found: %s" % path
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return None, "report file unreadable/malformed (%s): %s" % (type(e).__name__, path)


def render(rows):
    out = ["%-18s %-8s %-11s %s" % ("portfolio", "fields", "as_of", "flags")]
    for r in rows:
        out.append("%-18s %-8s %-11s %s" % (r["portfolio"], r["fields"], r["as_of"], "; ".join(r["flags"]) or "OK"))
    return "\n".join(out)


def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report")
    ap.add_argument("--expect", required=True, help="comma-separated expected portfolio names")
    ap.add_argument("--today", help="YYYY-MM-DD (default: system date)")
    a = ap.parse_args(argv)
    expected = [x.strip() for x in a.expect.split(",") if x.strip()]
    if not expected:
        print("FAIL [<args>] no expected portfolios given", file=sys.stderr)
        return 2
    if a.today:
        today = _date(a.today)
        if today is None:
            print("FAIL [<args>] bad --today", file=sys.stderr)
            return 2
    today = today or dt.date.today()
    report, err = load(a.report)
    if err:
        print("FAIL [<report>] MISSING_OR_MALFORMED_FILE: " + err, file=sys.stderr)
        return 1
    rows = check_report(report, expected, today)
    print(render(rows))
    failed = [(r["portfolio"], f) for r in rows for f in r["flags"]]
    for pf, f in failed:
        print("FAIL [%s] %s" % (pf, f), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
