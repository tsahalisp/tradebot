"""Tests for tools/nav_vs_spy_check.py. Synthetic SQLite ledgers in tmp_path only.
Placebo/benchmark: a SPY-only portfolio must raise no flags and match SPY returns."""
import datetime as dt
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import nav_vs_spy_check as chk  # noqa: E402

START = dt.datetime(2026, 1, 5, 21, tzinfo=dt.timezone.utc)  # a Monday


def bdays(n):
    d, out = START, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.timestamp())
        d += dt.timedelta(days=1)
    return out


def spy_path(n):
    return [100 * (1 + 0.004 * ((i % 5) - 2)) * (1.001 ** i) for i in range(n)]


def make_db(path, navs, spys, name="core", created=None, spy0="auto", trades=()):
    ts = bdays(len(navs))
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE books(name TEXT PRIMARY KEY, cash REAL, created REAL, universe TEXT, last_rebal REAL, spy0 REAL)")
    con.execute("CREATE TABLE nav_history(book TEXT, ts REAL, nav REAL, cash REAL, invested REAL, spy REAL)")
    con.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, ts REAL, ticker TEXT, side TEXT, shares REAL, price REAL, fee REAL, reason TEXT)")
    con.execute("INSERT INTO books VALUES(?,?,?,?,?,?)",
                (name, 0, ts[0] if created is None else created, "u", None, spys[0] if spy0 == "auto" else spy0))
    for t, n, s in zip(ts, navs, spys):
        con.execute("INSERT INTO nav_history VALUES(?,?,?,?,?,?)", (name, t, n, 0, n, s))
    for i in trades:
        con.execute("INSERT INTO trades(book,ts,ticker,side) VALUES(?,?,?,?)", (name, ts[i] - 60, "X", "BUY"))
    con.commit(); con.close()
    return str(path)


def run(path, capsys, *extra):
    rc = chk.main([str(path), *extra])
    out = capsys.readouterr()
    return rc, out.out, out.err


def noisy_nav(n):
    return [100000 * (1 + 0.003 * ((i * 7) % 5 - 2)) * (1.0005 ** i) for i in range(n)]


def test_clean_series_no_flags(tmp_path, capsys):
    p = make_db(tmp_path / "ok.db", noisy_nav(30), spy_path(30))
    rc, out, err = run(p, capsys)
    assert rc == 0 and "OK: no flags" in out and err == ""
    assert "insufficient" not in out


def test_spy_only_placebo_matches_spy(tmp_path, capsys):
    spy = spy_path(40)
    p = make_db(tmp_path / "spy.db", [1000 * s for s in spy], spy, name="spy_only")
    flags, sums = chk.check_db(p, chk.argparse.Namespace(max_jump=.25, max_flat=5, min_obs=20, base_tol_days=1))
    assert flags == []
    s = sums[0]
    assert s["track"] < 1e-12 and abs(s["alpha"]) < 1e-12
    assert run(p, capsys)[0] == 0


def test_jump_flagged_unless_trade(tmp_path, capsys):
    navs = noisy_nav(30); navs[15:] = [x * 1.4 for x in navs[15:]]
    p = make_db(tmp_path / "jump.db", navs, spy_path(30))
    rc, _, err = run(p, capsys)
    assert rc == 1 and "[JUMP]" in err and "jump.db" in err
    p2 = make_db(tmp_path / "jt.db", navs, spy_path(30), trades=[15])
    assert run(p2, capsys)[0] == 0


def test_flat_stretch_flagged(tmp_path, capsys):
    navs = noisy_nav(30); navs[10:18] = [navs[10]] * 8
    p = make_db(tmp_path / "flat.db", navs, spy_path(30))
    rc, _, err = run(p, capsys)
    assert rc == 1 and "[FLAT]" in err and "flat.db" in err
    # flat while SPY is also flat is not flagged
    spy = spy_path(30); spy[10:18] = [spy[10]] * 8
    p2 = make_db(tmp_path / "flat2.db", navs, spy)
    assert run(p2, capsys)[0] == 0


def test_short_flat_not_flagged(tmp_path, capsys):
    navs = noisy_nav(30); navs[10:15] = [navs[10]] * 5  # 4 business days
    assert run(make_db(tmp_path / "f.db", navs, spy_path(30)), capsys)[0] == 0


def test_base_date_mismatch(tmp_path, capsys):
    spy = spy_path(30)
    p = make_db(tmp_path / "bd.db", noisy_nav(30), spy, created=bdays(1)[0] - 10 * 86400)
    rc, _, err = run(p, capsys)
    assert rc == 1 and "[BASE_DATE]" in err and "bd.db" in err
    p2 = make_db(tmp_path / "bs.db", noisy_nav(30), spy, spy0=spy[0] * 1.05)
    rc, _, err = run(p2, capsys)
    assert rc == 1 and "[BASE_DATE]" in err and "spy0" in err
    p3 = make_db(tmp_path / "bn.db", noisy_nav(30), spy, spy0=None)
    assert "[BASE_DATE]" in run(p3, capsys)[2]


def test_short_series_insufficient(tmp_path, capsys):
    p = make_db(tmp_path / "short.db", noisy_nav(10), spy_path(10))
    rc, out, err = run(p, capsys)
    assert rc == 1 and "[INSUFFICIENT]" in err and "short.db" in err
    assert out.count("insufficient data") >= 2  # sharpe and alpha, not computed


def test_missing_file_fails_loudly(tmp_path, capsys):
    rc, _, err = run(tmp_path / "nope.db", capsys)
    assert rc == 1 and "[MISSING_FILE]" in err and "nope.db" in err


def test_not_a_database_and_empty_db_not_skipped(tmp_path, capsys):
    bad = tmp_path / "bad.db"; bad.write_text("garbage")
    rc, _, err = run(bad, capsys)
    assert rc == 1 and "[UNREADABLE]" in err
    empty = make_db(tmp_path / "e.db", [1.0], [1.0])
    con = sqlite3.connect(empty); con.execute("DELETE FROM nav_history"); con.execute("DELETE FROM books"); con.commit(); con.close()
    rc, _, err = run(empty, capsys)
    assert rc == 1 and "[NO_BOOKS]" in err


def test_read_only(tmp_path, capsys):
    p = make_db(tmp_path / "ro.db", noisy_nav(30), spy_path(30))
    before = pathlib.Path(p).read_bytes()
    run(p, capsys)
    assert pathlib.Path(p).read_bytes() == before
