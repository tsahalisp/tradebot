"""Tests for tools/data_gap_alarm_check.py. Synthetic files in tmp_path only, fixed seeds.
Placebo/benchmark: SPY itself as a symbol must show coverage 1.0 / zero gaps / no flags;
every defect test is compared against that clean baseline."""
import datetime as dt
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import data_gap_alarm_check as dg  # noqa: E402

START = dt.date(2026, 1, 5)  # Monday
N = 60
CAL = dg.bdays_range(START, START + dt.timedelta(days=200))[:N]
TODAY = CAL[-1] + dt.timedelta(days=1)  # one calendar day after last bar


def prices(seed, n=N):
    rng, p, out = random.Random(seed), 100.0, []
    for _ in range(n):
        p *= 1 + rng.uniform(-0.01, 0.01)
        out.append(round(p, 4))
    return out


def write(d, sym, dates, closes, split=None):
    lines = ["date,close" + (",split" if split is not None else "")]
    for i, (a, c) in enumerate(zip(dates, closes)):
        lines.append(f"{a},{c}" + (f",{split.get(i, 0)}" if split is not None else ""))
    (d / f"{sym}.csv").write_text("\n".join(lines) + "\n")


@pytest.fixture
def pdir(tmp_path):
    write(tmp_path, "SPY", CAL, prices(1))
    write(tmp_path, "AAA", CAL, prices(2))
    write(tmp_path, "SPYCOPY", CAL, prices(1))  # placebo: identical to SPY
    return tmp_path


def fails(pdir, today=TODAY, **kw):
    return dg.run(pdir, today, out=lambda s: None, **kw)


def has(failures, sym, check):
    return any(f"symbol {sym}" in f and check in f and f"{sym}.csv" in f for f in failures)


def test_clean_set_passes_and_spy_placebo_full_coverage(pdir):
    lines = []
    assert dg.run(pdir, TODAY, out=lines.append) == []
    assert dg.main([str(pdir), "--today", str(TODAY)]) == 0
    row = next(l for l in lines if l.startswith("SPYCOPY"))
    assert "1.000" in row and row.split()[2] == "0"
    res = dg.check_series(dg.load_prices(pdir / "SPYCOPY.csv"), CAL, TODAY)
    assert res["coverage"] == 1.0 and res["gaps"] == [] and res["flags"] == []


def test_stale_last_date(pdir):
    assert fails(pdir, today=CAL[-1] + dt.timedelta(days=14)) != []
    assert has(fails(pdir, today=CAL[-1] + dt.timedelta(days=14)), "AAA", "stale")
    # injectable N: same date passes with generous threshold; placebo clean at TODAY
    assert fails(pdir, today=CAL[-1] + dt.timedelta(days=14), max_stale=10) == []
    assert not has(fails(pdir), "AAA", "stale")


def test_interior_gaps_listed(pdir):
    keep = [i for i in range(N) if i not in (20, 21, 35)]
    write(pdir, "AAA", [CAL[i] for i in keep], [prices(2)[i] for i in keep])
    f = fails(pdir)
    assert has(f, "AAA", "gaps")
    msg = next(x for x in f if "symbol AAA" in x)
    assert all(str(CAL[i]) in msg for i in (20, 21, 35))
    assert not has(f, "SPYCOPY", "gaps")


def test_duplicate_and_unsorted_dates(pdir):
    d = list(CAL)
    d[30] = d[29]
    write(pdir, "AAA", d, prices(2))
    assert has(fails(pdir), "AAA", "duplicate dates")
    d = list(CAL)
    d[10], d[11] = d[11], d[10]
    write(pdir, "AAA", d, prices(2))
    f = fails(pdir)
    assert has(f, "AAA", "non-monotonic") and not has(f, "AAA", "duplicate dates")


@pytest.mark.parametrize("bad,tag", [("nan", "bad price"), ("inf", "bad price"),
                                      ("0", "bad price"), ("-3", "bad price")])
def test_bad_prices(pdir, bad, tag):
    c = [str(x) for x in prices(2)]
    c[25] = bad
    write(pdir, "AAA", CAL, c)
    f = fails(pdir)
    assert has(f, "AAA", tag) and not has(f, "SPY", tag)


def test_jump_needs_split_flag(pdir):
    c = prices(2)
    c[30:] = [x * 2 for x in c[30:]]  # +100% one-day move
    write(pdir, "AAA", CAL, c)
    assert has(fails(pdir), "AAA", "jump")
    write(pdir, "AAA", CAL, c, split={30: 1})
    assert not has(fails(pdir), "AAA", "jump")


def test_flat_stale_feed(pdir):
    c = prices(2)
    c[40:45] = [c[40]] * 5
    write(pdir, "AAA", CAL, c)
    assert has(fails(pdir), "AAA", "flat price")
    c = prices(2)
    c[40:44] = [c[40]] * 4  # 4 identical days is below threshold
    write(pdir, "AAA", CAL, c)
    assert not has(fails(pdir), "AAA", "flat price")


def test_short_series_insufficient_not_computed(pdir):
    write(pdir, "AAA", CAL[:19], prices(2, 19))
    f = fails(pdir)
    assert has(f, "AAA", "insufficient data")
    res = dg.check_series(dg.load_prices(pdir / "AAA.csv"), CAL, TODAY)
    assert res["gaps"] is None and res["coverage"] is None
    write(pdir, "AAA", CAL[:20], prices(2, 20))
    assert not has(fails(pdir), "AAA", "insufficient data")


def test_malformed_file(pdir):
    (pdir / "AAA.csv").write_text("date,close\n2026-01-05,abc\n")
    f = fails(pdir)
    assert has(f, "AAA", "malformed-file")
    (pdir / "AAA.csv").write_text("")
    assert has(fails(pdir), "AAA", "malformed-file")
    (pdir / "AAA.csv").write_text("foo,bar\n1,2\n")
    assert has(fails(pdir), "AAA", "malformed-file")
    assert dg.main([str(pdir), "--today", str(TODAY)]) == 1


def test_missing_spy_and_empty_dir_fail(pdir, tmp_path_factory):
    (pdir / "SPY.csv").unlink()
    f = fails(pdir)
    assert f and "SPY.csv" in f[0] and "spy-calendar" in f[0]
    empty = tmp_path_factory.mktemp("empty")
    assert "no price files" in fails(empty)[0]
    assert "no price files" in fails(empty / "nope")[0]
