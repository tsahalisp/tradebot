"""Tests for tools/data_gap_check.py. Synthetic data in tmp_path only, fixed seeds."""
import csv
import datetime as dt
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import data_gap_check as dgc  # noqa: E402

TODAY = dt.date(2024, 6, 14)  # Friday


def bdays(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out[::-1]


def walk(n=250, seed=42, end=TODAY):
    """SPY-like placebo: seeded gaussian random walk, ~1% daily vol, no jumps."""
    rng, p, out = random.Random(seed), 100.0, []
    for d in bdays(end, n):
        p *= 1 + rng.gauss(0.0004, 0.01)
        out.append([d, p, ""])
    return out


def write(path, rows, split=True):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"] + (["split"] if split else []))
        for d, p, s in rows:
            w.writerow([d, p] + ([s] if split else []))
    return path


def flags_of(path, today=TODAY):
    _, _, fl = dgc.check_file(path, today)
    return {c for c, _ in fl}


def test_clean_series_passes(tmp_path):
    f = write(tmp_path / "clean.csv", walk(60, seed=1))
    assert flags_of(f) == set()
    assert dgc.run([f], TODAY) == 0


def test_spy_placebo_random_walk_no_flags(tmp_path):
    for seed in (7, 42, 2024):
        f = write(tmp_path / f"spy{seed}.csv", walk(500, seed=seed), split=False)
        assert flags_of(f) == set(), seed


def test_gap_detected_with_dates(tmp_path):
    rows = walk(60, seed=2)
    gone = [r[0] for r in rows[30:36]]  # 6 consecutive bdays
    f = write(tmp_path / "gap.csv", rows[:30] + rows[36:])
    _, _, fl = dgc.check_file(f, TODAY)
    d = dict(fl)
    assert "gap" in d and str(gone[0]) in d["gap"] and "missing 6" in d["gap"]


def test_short_gap_tolerated(tmp_path):
    rows = walk(60, seed=2)
    f = write(tmp_path / "hol.csv", rows[:30] + rows[33:])  # 3 bdays missing
    assert "gap" not in flags_of(f)


def test_duplicate_dates(tmp_path):
    rows = walk(60, seed=3)
    f = write(tmp_path / "dup.csv", rows[:30] + [rows[29]] + rows[30:])
    assert "dup_or_unordered" in flags_of(f)


def test_out_of_order_dates(tmp_path):
    rows = walk(60, seed=3)
    rows[10], rows[11] = rows[11], rows[10]
    f = write(tmp_path / "ooo.csv", rows)
    assert "dup_or_unordered" in flags_of(f)


@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf", "-inf", ""])
def test_bad_values(tmp_path, bad):
    rows = walk(60, seed=4)
    rows[20][1] = bad
    f = tmp_path / "bad.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "close"])
        for d, p, _ in rows:
            w.writerow([d, p])
    assert "bad_value" in flags_of(f)


def test_suspected_unadjusted_split(tmp_path):
    rows = walk(60, seed=5)
    for r in rows[30:]:
        r[1] /= 4  # 4:1 split, prices not back-adjusted
    f = write(tmp_path / "split.csv", rows)
    _, _, fl = dgc.check_file(f, TODAY)
    assert "suspect_split" in dict(fl)


def test_flagged_split_is_accepted(tmp_path):
    rows = walk(60, seed=5)
    for r in rows[30:]:
        r[1] /= 4
    rows[30][2] = "4:1"
    f = write(tmp_path / "split_ok.csv", rows)
    assert "suspect_split" not in flags_of(f)


def test_stale_last_date(tmp_path):
    f = write(tmp_path / "stale.csv", walk(60, seed=6, end=dt.date(2024, 6, 3)))
    assert "stale" in flags_of(f)
    # same file is fresh relative to an earlier injected 'today'
    assert "stale" not in flags_of(f, today=dt.date(2024, 6, 5))


def test_fresh_edge(tmp_path):
    f = write(tmp_path / "e.csv", walk(60, seed=6, end=dt.date(2024, 6, 11)))  # Tue, 3 bdays before Fri
    assert "stale" not in flags_of(f)


def test_short_series_insufficient(tmp_path):
    f = write(tmp_path / "short.csv", walk(19, seed=8))
    _, _, fl = dgc.check_file(f, TODAY)
    assert [c for c, _ in fl] == ["insufficient_data"]
    assert dgc.run([f], TODAY, out=open(tmp_path / "o", "w"), err=open(tmp_path / "e", "w")) == 1
    assert "insufficient_data" in (tmp_path / "e").read_text()


def test_twenty_obs_is_enough(tmp_path):
    assert flags_of(write(tmp_path / "t.csv", walk(20, seed=8))) == set()


def test_missing_file_fails(tmp_path):
    assert flags_of(tmp_path / "nope.csv") == {"malformed"}


@pytest.mark.parametrize("content", [
    "", "a,b\n1,2\n", "date,close\nnot-a-date,5\n", "date,close\n2024-01-02,abc\n",
    "date,close\n2024-01-02\n"])
def test_malformed_file(tmp_path, content):
    f = tmp_path / "m.csv"
    f.write_text(content)
    assert flags_of(f) == {"malformed"}


def test_empty_dir_fails(tmp_path):
    assert dgc.run([tmp_path], TODAY, out=open(tmp_path / ".o", "w"), err=open(tmp_path / ".e", "w")) == 1


def test_cli_exit_code_and_message(tmp_path):
    write(tmp_path / "good.csv", walk(60, seed=1))
    write(tmp_path / "short.csv", walk(5, seed=1))
    cmd = [sys.executable, "-I", str(ROOT / "tools" / "data_gap_check.py"), "--today", str(TODAY)]
    ok = subprocess.run(cmd + [str(tmp_path / "good.csv")], capture_output=True, text=True)
    assert ok.returncode == 0 and "good.csv" in ok.stdout
    bad = subprocess.run(cmd + [str(tmp_path)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert "short.csv" in bad.stderr and "insufficient_data" in bad.stderr
    assert "good.csv" not in bad.stderr
