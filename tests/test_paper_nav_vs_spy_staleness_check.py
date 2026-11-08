"""Tests for tools/paper_nav_vs_spy_staleness_check.py. Synthetic data in tmp_path only, fixed seed.
Benchmark: a SPY-only placebo portfolio must show zero excess return vs SPY and pass every check."""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import paper_nav_vs_spy_staleness_check as chk  # noqa: E402

N = 40
START = date(2026, 9, 1)  # Tuesday


def bdays(n, start=START):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DATES = bdays(N)
TODAY = DATES[-1] + timedelta(days=1)


def spy_prices(seed=7):
    rnd = random.Random(seed)
    p, out = 400.0, []
    for _ in DATES:
        p *= 1 + rnd.gauss(0.0004, 0.01)
        out.append(p)
    return out


def write(path, header, rows):
    path.write_text(header + "\n" + "".join(f"{d},{v}\n" for d, v in rows))


@pytest.fixture
def env(tmp_path):
    navdir = tmp_path / "navs"
    navdir.mkdir()
    spy = spy_prices()
    write(tmp_path / "spy.csv", "date,close", zip(DATES, spy))
    return navdir, tmp_path / "spy.csv", spy


def go(env, today=TODAY):
    navdir, spy, _ = env
    out = []
    code, fails = chk.run(navdir, spy, today, out=out.append)
    return code, fails, "\n".join(out)


def checks(fails, name):
    return {c for n, c, _ in fails if n == name}


def test_clean_passes_and_spy_placebo_zero_excess(env):
    navdir, _, spy = env
    write(navdir / "placebo.csv", "date,nav", zip(DATES, [10000 * p / spy[0] for p in spy]))
    rnd = random.Random(3)
    v, vals = 10000.0, []
    for _ in DATES:
        v *= 1 + rnd.gauss(0.0005, 0.008)
        vals.append(v)
    write(navdir / "clean.csv", "date,nav", zip(DATES, vals))
    code, fails, text = go(env)
    assert code == 0 and fails == []
    line = next(l for l in text.splitlines() if l.startswith("placebo"))
    assert "+0.00%" in line.split()[-2]  # excess column
    assert "cum_ret" in text and "spy_ret" in text


def test_stale_last_date(env):
    navdir, _, spy = env
    write(navdir / "a.csv", "date,nav", zip(DATES, [10000 * p / spy[0] for p in spy]))
    # 3 business days late is OK, 4 is stale
    ok_today = bdays(4, DATES[-1])[-1]
    assert chk.business_days_between(DATES[-1], ok_today) == 3
    assert go(env, ok_today)[0] == 0
    code, fails, _ = go(env, bdays(5, DATES[-1])[-1])
    assert code == 1 and "stale" in checks(fails, "a")


def test_misalignment_with_spy(env):
    navdir, _, spy = env
    rows = [(d, 10000 * p / spy[0]) for d, p in zip(DATES, spy)]
    write(navdir / "short_end.csv", "date,nav", rows[:-2])
    write(navdir / "gap.csv", "date,nav", rows[:10] + rows[11:])
    write(navdir / "late_start.csv", "date,nav", rows[3:])
    code, fails, text = go(env, DATES[-3] + timedelta(days=1))
    assert code == 1
    for n in ("short_end", "gap", "late_start"):
        assert "misaligned" in checks(fails, n), n
    assert str(DATES[10]) in next(d for n, c, d in fails if n == "gap")  # missing date listed
    assert str(DATES[-1]) in next(d for n, c, d in fails if n == "short_end")


@pytest.mark.parametrize("bad", [0.0, -5.0, "nan", "inf"])
def test_bad_values(env, bad):
    navdir, _, spy = env
    vals = [10000 * p / spy[0] for p in spy]
    vals[15] = bad
    write(navdir / "b.csv", "date,nav", zip(DATES, vals))
    code, fails, _ = go(env)
    assert code == 1 and "bad_value" in checks(fails, "b")


def test_jump_over_25pct(env):
    navdir, _, spy = env
    vals = [10000 * p / spy[0] for p in spy]
    vals = vals[:20] + [v * 1.3 for v in vals[20:]]
    write(navdir / "j.csv", "date,nav", zip(DATES, vals))
    code, fails, _ = go(env)
    assert code == 1 and "bad_value" in checks(fails, "j")
    assert str(DATES[20]) in next(d for n, c, d in fails if c == "bad_value")


def test_flat_stalled_nav(env):
    navdir, _, spy = env
    vals = [10000 * p / spy[0] for p in spy]
    vals[30:35] = [vals[30]] * 5
    write(navdir / "flat.csv", "date,nav", zip(DATES, vals))
    vals4 = [10000 * p / spy[0] for p in spy]
    vals4[30:34] = [vals4[30]] * 4  # 4 identical is tolerated
    write(navdir / "four.csv", "date,nav", zip(DATES, vals4))
    code, fails, _ = go(env)
    assert "flat" in checks(fails, "flat")
    assert "flat" not in checks(fails, "four")


def test_short_series_insufficient_not_computed(env):
    navdir, _, spy = env
    rows = [(d, 10000 * p / spy[0]) for d, p in zip(DATES, spy)][-10:]
    write(navdir / "new.csv", "date,nav", rows)
    code, fails, text = go(env)
    assert code == 1 and "insufficient" in checks(fails, "new")
    assert "insufficient data" in text and "n/a" in next(l for l in text.splitlines() if l.startswith("new"))
    assert "misaligned" not in checks(fails, "new")  # not computed


def test_malformed_and_missing(env, tmp_path):
    navdir, spy, _ = env
    (navdir / "garbage.csv").write_text("date,nav\n2026-09-01,abc\n")
    (navdir / "nohdr.csv").write_text("foo,bar\n1,2\n")
    (navdir / "empty.csv").write_text("")
    (navdir / "dup.csv").write_text("date,nav\n2026-09-01,1\n2026-09-01,2\n")
    (navdir / "bin.csv").write_bytes(b"\xff\xfe\x00\x01")
    code, fails, _ = go(env)
    assert code == 1
    for n in ("garbage", "nohdr", "empty", "dup", "bin"):
        assert checks(fails, n) == {"malformed"}, n
    # missing SPY file / no NAV files never pass silently
    assert chk.run(navdir, tmp_path / "nope.csv", TODAY, out=lambda s: None)[0] == 2
    empty = tmp_path / "e"; empty.mkdir()
    assert chk.run(empty, spy, TODAY, out=lambda s: None)[0] == 2
    assert chk.run(tmp_path / "missing_dir", spy, TODAY, out=lambda s: None)[0] == 2


def test_cli_exit_code_names_portfolio(env, capsys):
    navdir, spy, _ = env
    (navdir / "broken.csv").write_text("date,nav\nx,y\n")
    assert chk.main(["--nav-dir", str(navdir), "--spy", str(spy), "--today", str(TODAY)]) == 1
    assert "FAIL broken [malformed]" in capsys.readouterr().out
