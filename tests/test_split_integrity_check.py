"""Tests for tools/split_integrity_check.py. Synthetic windows only (tmp_path).

Existing code reviewed: backend/validate.py and backend/model.py use a single
contiguous int(n*split) in-sample/out-of-sample cut, no embargo, nothing checks
the schedule. The xfail test documents that real config (not fixed here).
Benchmark: random-signal strategy on a clean split shows ~0 edge; on a leaky split ~inflated.
"""
import json
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import split_integrity_check as sic  # noqa: E402

D0 = date(2020, 1, 1)


def iso(n):
    return (D0 + timedelta(days=n)).isoformat()


def win(ts, te, vs, ve, **kw):
    return {"train_start": iso(ts), "train_end": iso(te), "test_start": iso(vs), "test_end": iso(ve), **kw}


def rolling(n=5, train=250, test=60, gap=2):
    out, s = [], 0
    for _ in range(n):
        out.append(win(s, s + train - 1, s + train + gap, s + train + gap + test - 1))
        s += test
    return out


def checks(flags):
    return {(i, c) for i, c, _ in flags}


def run_cli(path, *args):
    return subprocess.run([sys.executable, str(ROOT / "tools/split_integrity_check.py"), str(path), *args],
                          capture_output=True, text=True)


def write(tmp_path, obj, name="s.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def test_clean_rolling_schedule_no_flags(tmp_path):
    p = write(tmp_path, {"lookback_days": 20, "windows": rolling(gap=2)})
    r = run_cli(p)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout and "FAIL" not in r.stdout


def test_train_test_overlap(tmp_path):
    ws = rolling()
    ws[2] = win(0, 300, 250, 320)
    _, flags = sic.check_windows(ws)
    assert (2, "overlap") in checks(flags) and (2, "test_before_train_end") in checks(flags)
    r = run_cli(write(tmp_path, ws))
    assert r.returncode == 1 and "window 2: overlap" in r.stderr


def test_missing_embargo_only_when_lookback_declared(tmp_path):
    ws = [win(0, 249, 250, 320)]  # adjacent, 0 days gap
    assert sic.check_windows(ws)[1] == []
    _, flags = sic.check_windows(ws, lookback_days=20)
    assert checks(flags) == {(0, "embargo")}
    r = run_cli(write(tmp_path, {"lookback_days": 20, "windows": ws}))
    assert r.returncode == 1 and "window 0: embargo" in r.stderr
    assert run_cli(write(tmp_path, {"lookback_days": 20, "windows": ws}, "b.json"), "--embargo-days", "0").returncode == 0


def test_overlapping_test_windows(tmp_path):
    ws = [win(0, 249, 251, 320), win(30, 279, 281, 350)]
    _, flags = sic.check_windows(ws)
    assert checks(flags) == {(0, "test_overlap"), (1, "test_overlap")}
    assert sic.check_windows(ws, disjoint_tests=False)[1] == []
    assert run_cli(write(tmp_path, ws)).returncode == 1


def test_short_test_window_insufficient_data(tmp_path):
    ws = rolling()
    ws[1] = win(0, 249, 700, 709)
    _, flags = sic.check_windows(ws)
    assert checks(flags) == {(1, "insufficient_data")}
    r = run_cli(write(tmp_path, ws))
    assert r.returncode == 1 and "insufficient data" in r.stderr
    # explicit observation count is honoured
    assert sic.check_windows([win(0, 249, 251, 400, n_test_obs=5)])[1][0][1] == "insufficient_data"


def test_selection_date_after_test_start():
    _, flags = sic.check_windows([win(0, 249, 252, 320, select_date=iso(260))])
    assert checks(flags) == {(0, "select_after_test_start")}
    assert sic.check_windows([win(0, 249, 252, 320, select_date=iso(249))])[1] == []


@pytest.mark.parametrize("content", [None, "{not json", "{}", "[]", '"x"'])
def test_bad_or_missing_file(tmp_path, content):
    p = tmp_path / "x.json"
    if content is not None:
        p.write_text(content)
    r = run_cli(p)
    assert r.returncode == 2 and "ERROR" in r.stderr


def test_malformed_entries_never_skipped(tmp_path):
    ws = rolling(2) + [[iso(0), "nope", iso(5), iso(9)], [1, 2, 3], "junk",
                       win(10, 5, 20, 30), {"train_start": iso(0)}]
    rows, flags = sic.check_windows(ws)
    assert len(rows) == len(ws)
    assert {i for i, c, _ in flags if c == "malformed"} == {2, 3, 4, 5, 6}
    r = run_cli(write(tmp_path, ws))
    assert r.returncode == 1 and "window 6: malformed" in r.stderr


# ---- real config: backend/validate.py single split, no embargo ----
@pytest.mark.xfail(strict=True, reason=(
    "backend/validate.py (and model.validate) cut closes at int(n*0.7) with zero gap while "
    "strategies use lookbacks up to 252 bars (GRIDS). No embargo. Proposed fix (not applied): "
    "drop/skip `embargo` bars between closes[:k] and closes[k:] (>=1, ideally the longest lookback "
    "if indicators were ever warmed on train data)."))
def test_real_validate_split_has_embargo():
    n, split = 600, 0.7
    k = int(n * split)
    w = [win(0, k - 1, k, n - 1)]  # contiguous slices, as in validate.validate
    _, flags = sic.check_windows(w, lookback_days=252)
    assert flags == []


# ---- benchmark: random signal, clean vs leaky split ----
def _edge(sig, rets, idx):
    return sum(sig[t] * rets[t] for t in idx) / len(idx)


def _experiment(seed, leaky, n_sig=200, n=1200, train=600):
    rng = random.Random(seed)
    rets = [rng.gauss(0, 0.01) for _ in range(n)]
    sigs = [[rng.choice((-1, 1)) for _ in range(n)] for _ in range(n_sig)]
    tr = range(0, train)
    te = range(0, n) if leaky else range(train, n)  # leaky: test contains the train rows
    best = max(sigs, key=lambda s: _edge(s, rets, tr))
    return _edge(best, rets, te)


def test_benchmark_random_signal_clean_vs_leaky():
    seeds = range(40)
    clean = sum(_experiment(s, False) for s in seeds) / len(seeds)
    leaky = sum(_experiment(s, True) for s in seeds) / len(seeds)
    assert abs(clean) < 0.0003, clean          # placebo: ~zero edge (daily ret units)
    assert leaky > 0.0004 and leaky > 4 * abs(clean), (clean, leaky)
    # and the tool distinguishes the two schedules
    assert sic.check_windows([win(0, 599, 602, 1199)], lookback_days=5)[1] == []
    assert "overlap" in {c for _, c, _ in sic.check_windows([win(0, 599, 0, 1199)])[1]}
