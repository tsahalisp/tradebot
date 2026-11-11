#!/usr/bin/env python3
"""Read-only walk-forward fold leakage check (stdlib only; no network, no strategy code).

Input: a fold spec (JSON list / {"folds": [...]} or CSV) with columns
train_start, train_end, test_start, test_end (ISO dates, inclusive windows).
Observations are counted as business days (Mon-Fri) in the inclusive window.

Checks (per fold unless noted):
  overlap      (a) test_start <= train_end
  test_overlap (b) test window overlaps another fold's test window (double-counted OOS)
  lookahead    (c) train_end > test_start of the same fold, or folds not in chronological
               test order. NB: a later fold training on an EARLIER fold's test period is
               normal rolling walk-forward and is deliberately not flagged.
  embargo      (d) when --label-horizon > 0, business days between train_end and
               test_start (exclusive) < --min-embargo
  dates        (e) unparseable/missing dates, train_end < train_start, test_end < test_start
  insufficient (f) < 3 folds (global, fold 0) or < 20 obs in a test window; such folds are
               marked 'insufficient data' and no performance is computed for them.
Exit 0 only if no flag; 1 on any flag; 2 on missing/malformed file. Nothing is skipped silently.
"""
import argparse
import csv
import json
import random
import sys
from datetime import date, timedelta

FIELDS = ("train_start", "train_end", "test_start", "test_end")
MIN_FOLDS = 3
MIN_TEST_OBS = 20


class SpecError(Exception):
    pass


def load_folds(path):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        raise SpecError(f"cannot read file: {e}")
    if not text.strip():
        raise SpecError("file is empty")
    try:
        if str(path).lower().endswith(".csv"):
            rows = list(csv.DictReader(text.splitlines()))
        else:
            data = json.loads(text)
            rows = data.get("folds") if isinstance(data, dict) else data
    except (ValueError, csv.Error) as e:
        raise SpecError(f"malformed file: {e}")
    if not isinstance(rows, list) or not rows:
        raise SpecError("no folds found (expected a non-empty list of folds)")
    if not all(isinstance(r, dict) for r in rows):
        raise SpecError("malformed file: every fold must be an object/row")
    return rows


def parse_day(v):
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        return None


def bdays_inclusive(a, b):
    """Business days (Mon-Fri) in [a, b]; 0 if b < a."""
    n, d = 0, a
    while d <= b:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def check_folds(rows, label_horizon=0, min_embargo=0, min_folds=MIN_FOLDS, min_obs=MIN_TEST_OBS):
    """Return (infos, flags). infos[i] = dict(parsed dates, test_obs, train_obs, valid);
    flags = list of (fold_index, check, message)."""
    flags, infos = [], []
    for i, r in enumerate(rows):
        p = {k: parse_day(r.get(k)) for k in FIELDS}
        valid = True
        for k in FIELDS:
            if p[k] is None:
                flags.append((i, "dates", f"{k} missing or unparseable ({r.get(k)!r})"))
                valid = False
        if valid:
            if p["train_end"] < p["train_start"]:
                flags.append((i, "dates", "train_end < train_start")); valid = False
            if p["test_end"] < p["test_start"]:
                flags.append((i, "dates", "test_end < test_start")); valid = False
        info = dict(p, valid=valid, train_obs=None, test_obs=None)
        if valid:
            info["train_obs"] = bdays_inclusive(p["train_start"], p["train_end"])
            info["test_obs"] = bdays_inclusive(p["test_start"], p["test_end"])
        infos.append(info)

    ok = [i for i, x in enumerate(infos) if x["valid"]]
    for i in ok:
        x = infos[i]
        if x["test_start"] <= x["train_end"]:
            flags.append((i, "overlap", f"test_start {x['test_start']} <= train_end {x['train_end']}"))
        if x["train_end"] > x["test_start"]:
            flags.append((i, "lookahead", f"train_end {x['train_end']} is after test_start {x['test_start']}"))
        if label_horizon > 0 and x["test_start"] > x["train_end"]:
            gap = bdays_inclusive(x["train_end"] + timedelta(days=1), x["test_start"] - timedelta(days=1))
            if gap < min_embargo:
                flags.append((i, "embargo", f"embargo {gap} business days < required {min_embargo} "
                                            f"(label horizon {label_horizon})"))
        elif label_horizon > 0 and min_embargo > 0:
            flags.append((i, "embargo", f"no embargo (train/test overlap) < required {min_embargo}"))
        if x["test_obs"] < min_obs:
            flags.append((i, "insufficient", f"insufficient data: {x['test_obs']} test obs < {min_obs}"))
    for a in ok:
        for b in ok:
            if b > a and infos[a]["test_start"] <= infos[b]["test_end"] and infos[b]["test_start"] <= infos[a]["test_end"]:
                flags.append((b, "test_overlap", f"test window overlaps fold {a}'s test window"))
    for a, b in zip(ok, ok[1:]):
        if infos[b]["test_start"] <= infos[a]["test_start"]:
            flags.append((b, "lookahead", f"folds not in chronological order (test_start <= fold {a}'s)"))
    if len(rows) < min_folds:
        flags.append((0, "insufficient", f"insufficient data: {len(rows)} folds < {min_folds}"))
    return infos, flags


def placebo_edge(folds, seed=0, n_candidates=30, daily_vol=0.01, drift=0.0003, fit_on="train"):
    """Benchmark: pick the best of n random +/-1 signals on the fit window, then measure its mean
    daily return minus a SPY-like baseline over the test windows. Synthetic data, fixed seed.
    folds: list of dicts (parsed date objects). fit_on='train' (honest) or 'test' (leaky)."""
    rng = random.Random(seed)
    start = min(f["train_start"] for f in folds)
    end = max(f["test_end"] for f in folds)
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    idx = {d: i for i, d in enumerate(days)}
    spy = [rng.gauss(drift, daily_vol) for _ in days]
    sigs = [[rng.choice((-1, 1)) for _ in days] for _ in range(n_candidates)]

    def span(a, b):
        return [idx[x] for x in days if a <= x <= b]

    edges = []
    for f in folds:
        fit = span(f["train_start"], f["train_end"]) if fit_on == "train" else span(f["test_start"], f["test_end"])
        best = max(sigs, key=lambda s: sum(s[j] * spy[j] for j in fit))
        test = span(f["test_start"], f["test_end"])
        edges.append(sum(best[j] * spy[j] - spy[j] for j in test) / len(test))
    return sum(edges) / len(edges)


def render(path, rows, infos, flags):
    by = {}
    for i, c, _ in flags:
        by.setdefault(i, []).append(c)
    out = [f"{'fold':>4}  {'train':<23} {'test':<23} {'trn_obs':>7} {'tst_obs':>7}  flags"]
    for i, (r, x) in enumerate(zip(rows, infos)):
        w = lambda a, b: f"{r.get(a)}..{r.get(b)}"
        fl = ",".join(sorted(set(by.get(i, [])))) or "ok"
        if "insufficient" in by.get(i, []):
            fl += " (insufficient data - not computed)"
        n = lambda v: "-" if v is None else str(v)
        out.append(f"{i:>4}  {w('train_start','train_end'):<23} {w('test_start','test_end'):<23} "
                   f"{n(x['train_obs']):>7} {n(x['test_obs']):>7}  {fl}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", help="fold spec (.json or .csv)")
    ap.add_argument("--label-horizon", type=int, default=0, help="signal lookback/label horizon in days (enables embargo check)")
    ap.add_argument("--min-embargo", type=int, default=0, help="minimum embargo in business days")
    a = ap.parse_args(argv)
    try:
        rows = load_folds(a.file)
    except SpecError as e:
        print(f"FAIL {a.file}: file: {e}", file=sys.stderr)
        return 2
    infos, flags = check_folds(rows, a.label_horizon, a.min_embargo)
    print(render(a.file, rows, infos, flags))
    for i, c, m in flags:
        print(f"FAIL {a.file}: fold {i}: {c}: {m}", file=sys.stderr)
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
