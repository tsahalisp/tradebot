"""Tests for tools/data_alarm.py. Synthetic fixtures in tmp_path only.
Placebo: a clean SPY-like series must raise zero alarms."""
import datetime as dt
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import data_alarm as da  # noqa: E402

TODAY = dt.date(2026, 10, 21)  # Wednesday


def bdays(n, end=TODAY):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out[::-1]


def ts(d):
    return dt.datetime(d.year, d.month, d.day, 21, tzinfo=dt.timezone.utc).timestamp()


def make_db(path, days, spy=None, nav=None):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE nav_history(book TEXT, ts REAL, nav REAL, cash REAL, invested REAL, spy REAL)")
    c.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, book TEXT, ts REAL, ticker TEXT, side TEXT,"
              " shares REAL, price REAL, fee REAL, reason TEXT)")
    for i, d in enumerate(days):
        s = 400.0 + i if spy is None else spy(i, d)
        n = 100000.0 + i if nav is None else nav(i, d)
        c.execute("INSERT INTO nav_history VALUES('core',?,?,0,0,?)", (ts(d), n, s))
    c.execute("INSERT INTO trades(book,ts,ticker,side,shares,price,fee) VALUES('core',?,'AAPL','buy',1,150,0)",
              (ts(days[0]),))
    c.commit(); c.close()


def failed(rows):
    return {(r[1], r[2]) for r in rows if r[3] == "FAIL"}


def run(db, tmp_path, csvs=()):
    return da.run(str(db), None, list(csvs), TODAY)


def write_csv(path, days, vals):
    path.write_text("date,close\n" + "".join(f"{d},{v}\n" for d, v in zip(days, vals)))
    return path


def test_clean_series_no_alarms(tmp_path):
    db = tmp_path / "p.db"; make_db(db, bdays(60))
    spy = write_csv(tmp_path / "spy.csv", bdays(60), [400 + i for i in range(60)])
    rows = run(db, tmp_path, [spy])
    assert failed(rows) == set() and rows
    assert da.main(["--portfolio-db", str(db), "--marks-db", str(tmp_path / "x.db"),
                    "--csv", str(spy), "--today", "2026-10-21"]) == 1  # missing marks db still alarms


def test_trades_not_flagged_stale(tmp_path):
    db = tmp_path / "p.db"; make_db(db, bdays(60))
    rows = run(db, tmp_path)
    assert [r[3] for r in rows if r[1] == "trades.price"] == ["ok"]


def test_clean_exit_zero(tmp_path, capsys):
    db = tmp_path / "p.db"; make_db(db, bdays(60))
    marks = tmp_path / "m.db"
    c = sqlite3.connect(marks)
    c.execute("CREATE TABLE paper_book(book_id INTEGER PRIMARY KEY, product TEXT)")
    c.execute("CREATE TABLE paper_marks(book_id INT, date TEXT, price REAL, position INT, equity REAL)")
    c.execute("INSERT INTO paper_book VALUES(1,'SPY'),(2,'AAPL')")
    for i, d in enumerate(bdays(30)):
        c.execute("INSERT INTO paper_marks VALUES(1,?,?,1,?)", (str(d), 400 + i, 10000 + i))
        c.execute("INSERT INTO paper_marks VALUES(2,?,?,1,?)", (str(d), 150 + i, 10000 + i))
    c.commit(); c.close()
    assert da.main(["--portfolio-db", str(db), "--marks-db", str(marks), "--today", "2026-10-21"]) == 0
    assert "all data checks passed" in capsys.readouterr().out


def test_stale_series(tmp_path):
    db = tmp_path / "p.db"; make_db(db, bdays(60, end=TODAY - dt.timedelta(days=14)))
    assert ("core.nav", "stale") in failed(run(db, tmp_path))


def test_internal_gap(tmp_path):
    days = [d for d in bdays(60) if not (bdays(60)[20] <= d <= bdays(60)[30])]
    db = tmp_path / "p.db"; make_db(db, days)
    f = failed(run(db, tmp_path))
    assert ("core.nav", "gap") in f and ("core.nav", "stale") not in f


def test_nan_heavy(tmp_path):
    db = tmp_path / "p.db"
    make_db(db, bdays(60), nav=lambda i, d: None if i % 3 == 0 else 100000.0 + i)
    assert ("core.nav", "bad_values") in failed(run(db, tmp_path))


def test_nonpositive_values_counted(tmp_path):
    db = tmp_path / "p.db"
    make_db(db, bdays(60), nav=lambda i, d: -5.0 if i % 2 else 100000.0)
    assert ("core.nav", "bad_values") in failed(run(db, tmp_path))


def test_missing_spy_date(tmp_path):
    days = bdays(60)
    db = tmp_path / "p.db"
    make_db(db, days, spy=lambda i, d: None if i == 40 else 400.0 + i)
    rows = run(db, tmp_path)
    assert ("core.nav", "spy_match") in failed(rows)
    detail = [r for r in rows if r[2] == "spy_match" and r[3] == "FAIL"][0][4]
    assert str(days[40]) in detail


def test_spy_match_unit_no_series():
    nav = da.Series("f", "n", bdays(3), [1.0, 2.0, 3.0])
    assert da.check_spy_coverage(nav, None)[1] is False


def test_empty_and_missing_files(tmp_path, capsys):
    empty = tmp_path / "e.csv"; empty.write_text("date,close\n")
    zero = tmp_path / "z.csv"; zero.write_text("")
    rows = da.run(str(tmp_path / "nope.db"), None, [str(empty), str(zero), str(tmp_path / "no.csv")], TODAY)
    bad = [r for r in rows if r[3] == "FAIL" and r[2] == "load"]
    assert len(bad) == 4  # every input reported, none skipped
    assert da.main(["--portfolio-db", str(tmp_path / "nope.db"), "--marks-db", str(tmp_path / "n2.db"),
                    "--today", "2026-10-21"]) == 1
    err = capsys.readouterr().err
    assert "nope.db" in err and "load" in err


def test_empty_table_is_alarm(tmp_path):
    db = tmp_path / "p.db"; make_db(db, bdays(5))
    c = sqlite3.connect(db); c.execute("DELETE FROM nav_history"); c.commit(); c.close()
    assert ("*", "load") in failed(run(db, tmp_path))


def test_read_only(tmp_path):
    db = tmp_path / "p.db"; make_db(db, bdays(10))
    before = db.read_bytes()
    run(db, tmp_path)
    assert db.read_bytes() == before


def test_bdays_between():
    assert da.bdays_between(dt.date(2026, 10, 16), dt.date(2026, 10, 19)) == 1  # Fri->Mon
    assert da.bdays_between(dt.date(2026, 10, 19), dt.date(2026, 10, 19)) == 0
