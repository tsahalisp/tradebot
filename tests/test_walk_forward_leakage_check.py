"""Tests for tools/walk_forward_leakage_check.py. Synthetic fold files in tmp_path only; fixed seeds.

Existing coverage found: backend/validate.py does a single 70/30 train/test split (no folds, no
embargo); the only existing tests are backend/tests/test_data_loading_quality.py. No walk-forward
tests existed. Benchmark: random signal vs SPY-like baseline, clean vs leaky folds.
"""
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import walk_forward_leakage_check as w  # noqa: E402

SCRIPT = ROOT / "tools" / "walk_forward_leakage_check.py"


def rolling(n=4, train=250, test=60, gap=5, start=date(2018, 1, 1)):
    """Rolling folds in calendar days (approx business-day counts via *7/5)."""
    folds, ts = [], start
    for _ in range(n):
        te = ts + timedelta(days=train * 7 // 5)
        s = te + timedelta(days=1 + gap * 7 // 5)
        e = s + timedelta(days=test * 7 // 5 - 1)
        folds.append({"train_start": ts.isoformat(), "train_end": te.isoformat(),
                      "test_start": s.isoformat(), "test_end": e.isoformat()})
        ts += timedelta(days=test * 7 // 5)
    return folds


def write(tmp_path, folds, name="f.json"):
    p = tmp_path / name
    p.write_text(json.dumps(folds))
    return p


def cli(path, *args):
    return subprocess.run([sys.executable, str(SCRIPT), str(path), *args], capture_output=True, text=True)


def checks(folds, **kw):
    return {(i, c) for i, c, _ in w.check_folds(folds, **kw)[1]}


def parsed(folds):
    return [{k: date.fromisoformat(v) for k, v in f.items()} for f in folds]


# (1) clean + placebo benchmark
def test_clean_rolling_passes(tmp_path):
    p = write(tmp_path, rolling())
    r = cli(p, "--label-horizon", "5", "--min-embargo", "3")
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout and "tst_obs" in r.stdout


def test_placebo_clean_near_zero_vs_leaky_inflated():
    folds = parsed(rolling())
    clean = w.placebo_edge(folds, seed=42, fit_on="train")
    leaky_folds = [dict(f, train_end=f["test_end"]) for f in folds]   # train overlaps test
    assert all(f["train_end"] >= f["test_start"] for f in leaky_folds)
    leaky = w.placebo_edge(leaky_folds, seed=42, fit_on="test")
    assert abs(clean) < 0.002          # ~0 daily edge vs SPY-like baseline
    assert leaky > 0.002 and leaky > 3 * abs(clean)
    # the checker flags the leaky set
    assert any(c == "overlap" for _, c in checks([dict(f, train_end=f["test_end"]) for f in rolling()]))


# (2)
def test_train_test_overlap(tmp_path):
    f = rolling(); f[1]["test_start"] = f[1]["train_end"]
    assert (1, "overlap") in checks(f)
    r = cli(write(tmp_path, f))
    assert r.returncode == 1 and "f.json" in r.stderr and "fold 1" in r.stderr and "overlap" in r.stderr


def test_lookahead_train_after_test_start():
    f = rolling(); f[2]["train_end"] = (date.fromisoformat(f[2]["test_start"]) + timedelta(days=10)).isoformat()
    assert (2, "lookahead") in checks(f)


def test_out_of_order_folds_flagged():
    f = rolling(); f[1], f[2] = f[2], f[1]
    assert any(c == "lookahead" for _, c in checks(f))


# (3)
def test_overlapping_test_windows(tmp_path):
    f = rolling(); f[2]["test_start"] = f[1]["test_end"]
    assert (2, "test_overlap") in checks(f)
    assert "test_overlap" in cli(write(tmp_path, f)).stderr


# (4)
def test_insufficient_embargo(tmp_path):
    f = rolling(gap=0)
    assert not any(c == "embargo" for _, c in checks(f))          # horizon 0 -> not applicable
    assert (0, "embargo") in checks(f, label_horizon=10, min_embargo=5)
    r = cli(write(tmp_path, f), "--label-horizon", "10", "--min-embargo", "5")
    assert r.returncode == 1 and "embargo" in r.stderr
    assert not any(c == "embargo" for _, c in checks(rolling(gap=6), label_horizon=10, min_embargo=5))


# (5)
@pytest.mark.parametrize("key,val", [("train_start", "2018-13-45"), ("test_end", ""), ("train_end", None),
                                     ("test_start", "yesterday")])
def test_bad_or_missing_dates(tmp_path, key, val):
    f = rolling(); f[1][key] = val
    assert (1, "dates") in checks(f)
    r = cli(write(tmp_path, f))
    assert r.returncode == 1 and "fold 1" in r.stderr and "dates" in r.stderr
    assert "fold" in r.stdout        # bad fold still listed in the table, not skipped


def test_missing_key_and_inverted_windows():
    f = rolling(); del f[0]["test_end"]
    assert (0, "dates") in checks(f)
    f = rolling(); f[1]["train_end"] = "2000-01-01"
    assert (1, "dates") in checks(f)
    f = rolling(); f[2]["test_end"] = f[2]["test_start"].replace("20", "19", 1)
    assert (2, "dates") in checks(f)


# (6)
def test_too_few_folds(tmp_path):
    r = cli(write(tmp_path, rolling(2)))
    assert r.returncode == 1 and "insufficient" in r.stderr and "2 folds" in r.stderr


def test_short_test_window(tmp_path):
    f = rolling(); f[1]["test_end"] = (date.fromisoformat(f[1]["test_start"]) + timedelta(days=7)).isoformat()
    assert (1, "insufficient") in checks(f)
    r = cli(write(tmp_path, f))
    assert r.returncode == 1 and "fold 1" in r.stderr and "not computed" in r.stdout


# (7)
def test_missing_and_malformed_file(tmp_path):
    r = cli(tmp_path / "nope.json")
    assert r.returncode == 2 and "nope.json" in r.stderr
    for name, body in [("bad.json", "{not json"), ("empty.json", ""), ("obj.json", '{"x": 1}'),
                       ("rows.json", "[1, 2]"), ("none.json", "[]")]:
        p = tmp_path / name; p.write_text(body)
        r = cli(p)
        assert r.returncode == 2 and name in r.stderr, name


def test_csv_input(tmp_path):
    p = tmp_path / "f.csv"
    f = rolling()
    p.write_text("train_start,train_end,test_start,test_end\n" +
                 "\n".join(",".join(x[k] for k in w.FIELDS) for x in f))
    assert cli(p).returncode == 0


# Real spec: backend/validate.py's single 70/30 split, expressed as a fold spec.
@pytest.mark.xfail(strict=True, reason=(
    "backend/validate.py uses ONE split (closes[:k] / closes[k:], split=0.7) with no embargo gap and "
    "params tuned on indicators with lookbacks up to 252 bars: 1 fold (<3), no embargo. Not fixed. "
    "Proposed fix: add a multi-fold rolling walk-forward with an embargo >= max lookback horizon "
    "(needs Peter's sign-off; changes validation results)."))
def test_real_validate_split_passes_checks():
    n = 600; k = int(n * 0.7)
    days = [date(2022, 1, 3) + timedelta(days=i) for i in range(n * 2) if (date(2022, 1, 3) + timedelta(days=i)).weekday() < 5][:n]
    f = [{"train_start": days[0].isoformat(), "train_end": days[k - 1].isoformat(),
          "test_start": days[k].isoformat(), "test_end": days[-1].isoformat()}]
    assert not w.check_folds(f, label_horizon=252, min_embargo=5)[1]
