#!/usr/bin/env python3
"""Read-only integrity check for walk-forward / train-test split schedules.

Input: windows of (train_start, train_end, test_start, test_end), ISO dates, inclusive.
JSON file is either a bare list of windows or an object:
  {"lookback_days": 200, "embargo_days": 1, "min_test_obs": 20,
   "disjoint_tests": true, "windows": [...]}
A window is [ts, te, vs, ve] or {"train_start":..,"train_end":..,"test_start":..,
"test_end":.., "select_date": optional, "n_test_obs": optional}.
Without n_test_obs, test observations are counted as weekdays in the test window.
CLI flags override file settings.

Checks (per window): overlap, test_before_train_end, embargo (only when a signal
lookback is declared), test_overlap (between test windows), insufficient_data,
select_after_test_start, malformed. Embargo = days strictly between train_end and
test_start. Exit 0 clean, 1 any flag, 2 unreadable/invalid input. Nothing is skipped.
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULTS = {"lookback_days": None, "embargo_days": 1, "min_test_obs": 20, "disjoint_tests": True}


def _d(v, name):
    if not isinstance(v, str):
        raise ValueError(f"{name}: expected ISO date string, got {v!r}")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{name}: bad date {v!r}")


def _weekdays(a, b):
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def parse_window(w):
    """-> dict with parsed dates; raises ValueError on any malformation."""
    if isinstance(w, (list, tuple)):
        if len(w) != 4:
            raise ValueError(f"expected 4 dates, got {len(w)}")
        w = dict(zip(("train_start", "train_end", "test_start", "test_end"), w))
    elif not isinstance(w, dict):
        raise ValueError(f"window must be list or object, got {type(w).__name__}")
    missing = [k for k in ("train_start", "train_end", "test_start", "test_end") if k not in w]
    if missing:
        raise ValueError(f"missing {missing}")
    p = {k: _d(w[k], k) for k in ("train_start", "train_end", "test_start", "test_end")}
    if w.get("select_date") is not None:
        p["select_date"] = _d(w["select_date"], "select_date")
    if w.get("n_test_obs") is not None:
        n = w["n_test_obs"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError(f"n_test_obs: bad value {n!r}")
        p["n_test_obs"] = n
    if p["train_end"] < p["train_start"]:
        raise ValueError("train_end before train_start")
    if p["test_end"] < p["test_start"]:
        raise ValueError("test_end before test_start")
    return p


def check_windows(windows, lookback_days=None, embargo_days=1, min_test_obs=20, disjoint_tests=True):
    """Return (rows, flags). rows: per-window dicts; flags: list of (index, check, detail)."""
    flags, rows, parsed = [], [], {}
    for i, w in enumerate(windows):
        try:
            parsed[i] = parse_window(w)
        except ValueError as e:
            flags.append((i, "malformed", str(e)))
            rows.append({"i": i, "train": "?", "test": "?", "bad": ["malformed"]})
            continue
        p = parsed[i]
        bad = []

        def flag(check, detail):
            bad.append(check)
            flags.append((i, check, detail))

        if p["train_start"] <= p["test_end"] and p["test_start"] <= p["train_end"]:
            flag("overlap", f"train {p['train_start']}..{p['train_end']} overlaps test {p['test_start']}..{p['test_end']}")
        if p["test_start"] < p["train_end"]:
            flag("test_before_train_end", f"test_start {p['test_start']} < train_end {p['train_end']}")
        if lookback_days is not None:
            gap = (p["test_start"] - p["train_end"]).days - 1
            if gap < embargo_days:
                flag("embargo", f"gap {gap}d < required {embargo_days}d (lookback {lookback_days}d declared)")
        n = p.get("n_test_obs", _weekdays(p["test_start"], p["test_end"]))
        if n < min_test_obs:
            flag("insufficient_data", f"insufficient data: {n} test obs < {min_test_obs}")
        if "select_date" in p and p["select_date"] > p["test_start"]:
            flag("select_after_test_start", f"select_date {p['select_date']} > test_start {p['test_start']}")
        rows.append({"i": i, "train": f"{p['train_start']}..{p['train_end']}",
                     "test": f"{p['test_start']}..{p['test_end']}", "bad": bad})

    if disjoint_tests:
        ids = sorted(parsed)
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, j = ids[a], ids[b]
                if parsed[i]["test_start"] <= parsed[j]["test_end"] and parsed[j]["test_start"] <= parsed[i]["test_end"]:
                    for k, other in ((j, i), (i, j)):
                        flags.append((k, "test_overlap", f"test window overlaps test window {other}"))
                        rows[k]["bad"].append("test_overlap")
    return rows, flags


def load_schedule(path):
    """-> (windows, settings). Raises ValueError with a clear message on any problem."""
    try:
        data = json.loads(Path(path).read_text())
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e}")
    except ValueError as e:
        raise ValueError(f"invalid JSON in {path}: {e}")
    settings = {}
    if isinstance(data, dict):
        if "windows" not in data:
            raise ValueError("JSON object has no 'windows' key")
        settings = {k: data[k] for k in DEFAULTS if k in data}
        data = data["windows"]
    if not isinstance(data, list):
        raise ValueError("windows must be a JSON list")
    if not data:
        raise ValueError("windows list is empty")
    return data, settings


def format_table(rows):
    out = [f"{'idx':>3}  {'train':<23}  {'test':<23}  status"]
    for r in rows:
        out.append(f"{r['i']:>3}  {r['train']:<23}  {r['test']:<23}  "
                   + ("OK" if not r["bad"] else "FAIL: " + ",".join(dict.fromkeys(r["bad"]))))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("schedule", help="JSON file of windows")
    ap.add_argument("--lookback-days", type=int)
    ap.add_argument("--embargo-days", type=int)
    ap.add_argument("--min-test-obs", type=int)
    ap.add_argument("--allow-overlapping-tests", action="store_true")
    a = ap.parse_args(argv)
    try:
        windows, st = load_schedule(a.schedule)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    cfg = {**DEFAULTS, **st}
    for k in ("lookback_days", "embargo_days", "min_test_obs"):
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    if a.allow_overlapping_tests:
        cfg["disjoint_tests"] = False
    rows, flags = check_windows(windows, **cfg)
    print(format_table(rows))
    if flags:
        print(f"\nFAILED: {len(flags)} flag(s)", file=sys.stderr)
        for i, check, detail in flags:
            print(f"  window {i}: {check}: {detail}", file=sys.stderr)
        return 1
    print(f"\nOK: {len(rows)} window(s), no flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
