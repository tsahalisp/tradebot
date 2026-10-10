"""Synthetic-fixture tests for tools/weekly_report.py (never touches the real ledger)."""
import datetime as dt, os, sqlite3, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import weekly_report as wr

TODAY = dt.date(2026, 10, 9)  # Friday


def ts(y, m, d, h=12):
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp()


def make_db(path, books=("alpha",), nav=(), trades=()):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE books(name TEXT PRIMARY KEY, cash REAL, created REAL, universe TEXT, last_rebal REAL, spy0 REAL)")
    c.execute("CREATE TABLE nav_history(book TEXT, ts REAL, nav REAL, cash REAL, invested REAL, spy REAL)")
    c.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, ts REAL, ticker TEXT, side TEXT, shares REAL, price REAL, fee REAL, reason TEXT)")
    for b in books:
        c.execute("INSERT INTO books(name) VALUES(?)", (b,))
    for b, t, n, s in nav:
        c.execute("INSERT INTO nav_history(book,ts,nav,spy) VALUES(?,?,?,?)", (b, t, n, s))
    for b, t, side, sh, px in trades:
        c.execute("INSERT INTO trades(book,ts,ticker,side,shares,price) VALUES(?,?,?,?,?,?)", (b, t, "X", side, sh, px))
    c.commit(); c.close()
    return str(path)


NAV = [("alpha", ts(2026, 9, 30), 100000, 400), ("alpha", ts(2026, 10, 2), 110000, 404),
       ("alpha", ts(2026, 10, 5), 99000, 408), ("alpha", ts(2026, 10, 7), 104500, 400),
       ("alpha", ts(2026, 10, 9), 108900, 412)]
TRADES = [("alpha", ts(2026, 9, 30), "BUY", 100, 100), ("alpha", ts(2026, 10, 5), "BUY", 50, 200),
          ("alpha", ts(2026, 10, 5), "SELL", 20, 250), ("alpha", ts(2026, 10, 8), "SELL", 10, 300)]


def run(db, tmp_path, capsys, today=TODAY):
    code = wr.main(["--db", db, "--out-dir", str(tmp_path / "reports"), "--today", today.isoformat()])
    return code, capsys.readouterr()


def test_normal_hand_computed(tmp_path):
    db = make_db(tmp_path / "p.db", nav=NAV, trades=TRADES)
    results, errors, warnings = wr.build_report(db, TODAY)
    assert errors == [] and warnings == []
    r = results[0]
    # 7d base = 2026-10-02 (nav 110000, spy 404)
    assert r["w_nav"] == pytest.approx(108900 / 110000 - 1) == pytest.approx(-0.01)
    assert r["w_spy"] == pytest.approx(412 / 404 - 1)
    assert r["inc_nav"] == pytest.approx(0.089)
    assert r["inc_spy"] == pytest.approx(0.03)
    assert r["max_dd"] == pytest.approx(-0.10)            # 110000 -> 99000
    # 7d: traded 10000+5000+3000=18000 -> 9000 / mean(110000,99000,104500,108900)=105600
    assert r["w_turnover"] == pytest.approx(9000 / 105600)
    # inception: 28000/2 = 14000 / mean of 5 navs = 104480
    assert r["inc_turnover"] == pytest.approx(14000 / 104480)


def test_normal_writes_table_and_markdown(tmp_path, capsys):
    db = make_db(tmp_path / "p.db", nav=NAV, trades=TRADES)
    code, out = run(db, tmp_path, capsys)
    assert code == 0 and "alpha" in out.out and "-1.00%" in out.out and "-10.00%" in out.out
    md = (tmp_path / "reports" / "weekly_2026-10-09.md").read_text()
    assert "| alpha |" in md


def test_intraday_marks_use_last_of_day(tmp_path):
    nav = NAV + [("alpha", ts(2026, 10, 9, 8), 1, 1)]   # earlier mark same day must be ignored
    db = make_db(tmp_path / "p.db", nav=nav, trades=TRADES)
    results, errors, _ = wr.build_report(db, TODAY)
    assert errors == [] and results[0]["last_nav"] == 108900


def test_missing_spy_date_fails(tmp_path, capsys):
    nav = [n if n[1] != ts(2026, 10, 5) else ("alpha", n[1], n[2], None) for n in NAV]
    db = make_db(tmp_path / "p.db", nav=nav, trades=TRADES)
    code, out = run(db, tmp_path, capsys)
    assert code == 1 and "SPY missing" in out.err and "2026-10-05" in out.err
    assert not (tmp_path / "reports").exists()


def test_stale_series_fails(tmp_path, capsys):
    db = make_db(tmp_path / "p.db", nav=NAV, trades=TRADES)
    # last NAV Fri 10-09; Thu 10-15 is 4 business days later -> stale; Wed 10-14 is 3 -> ok
    code, out = run(db, tmp_path, capsys, today=dt.date(2026, 10, 15))
    assert code == 1 and "stale" in out.err
    code, _ = run(db, tmp_path, capsys, today=dt.date(2026, 10, 14))
    assert code == 0


def test_empty_nav_fails(tmp_path, capsys):
    db = make_db(tmp_path / "p.db", nav=[], trades=TRADES)
    code, out = run(db, tmp_path, capsys)
    assert code == 1 and "[alpha] nav_history is empty" in out.err


def test_empty_file_fails(tmp_path, capsys):
    p = tmp_path / "empty.db"; p.write_bytes(b"")
    code, out = run(str(p), tmp_path, capsys)
    assert code == 1 and "ERROR" in out.err


def test_missing_db_fails_and_is_not_created(tmp_path, capsys):
    p = tmp_path / "nope.db"
    code, out = run(str(p), tmp_path, capsys)
    assert code == 1 and "not found" in out.err and not p.exists()


def test_one_bad_book_fails_whole_report(tmp_path, capsys):
    nav = NAV + [("beta", ts(2026, 10, 9), 100, 412)]
    db = make_db(tmp_path / "p.db", books=("alpha", "beta"), nav=nav, trades=TRADES)
    code, out = run(db, tmp_path, capsys)   # beta has no trades -> loud failure, alpha not reported alone
    assert code == 1 and "[beta]" in out.err and "[alpha]" not in out.err
    assert not (tmp_path / "reports").exists()


def test_empty_trades_fails(tmp_path, capsys):
    db = make_db(tmp_path / "p.db", nav=NAV, trades=[])
    code, out = run(db, tmp_path, capsys)
    assert code == 1 and "trades is empty" in out.err


def test_short_history_is_warned_not_hidden(tmp_path, capsys):
    nav = [n for n in NAV if n[1] >= ts(2026, 10, 5)]
    db = make_db(tmp_path / "p.db", nav=nav, trades=TRADES)
    code, out = run(db, tmp_path, capsys)
    assert code == 0 and "WARNING" in out.err and "n/a" in out.out


def test_reads_do_not_modify_db(tmp_path):
    db = make_db(tmp_path / "p.db", nav=NAV, trades=TRADES)
    before = open(db, "rb").read()
    wr.build_report(db, TODAY)
    assert open(db, "rb").read() == before
