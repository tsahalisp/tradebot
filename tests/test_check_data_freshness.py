import io, os, sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import check_data_freshness as cdf

ASOF = date(2026, 10, 7)  # Wednesday


def bars(start=date(2026, 8, 1), end=ASOF, skip=()):
    return [d for d in cdf.trading_days(start, end) if d not in set(skip)]


def write(dirpath, name, dates, header="Date,Close\n"):
    (dirpath / f"{name}.csv").write_text(header + "".join(f"{d},1.0\n" for d in dates))


def test_fresh_passes():
    last, stale, missing, problems = cdf.check_series(bars(), ASOF)
    assert problems == [] and missing == [] and last == ASOF and stale == 0


def test_fresh_across_holiday_has_no_false_gap():
    # Columbus/Labor Day etc. are not trading days; Labor Day 2026-09-07 must not be a gap
    assert date(2026, 9, 7) not in bars()
    assert cdf.check_series(bars(), ASOF)[3] == []


def test_stale_fails():
    dates = bars(end=ASOF - timedelta(days=10))
    _, stale, _, problems = cdf.check_series(dates, ASOF)
    assert stale > 3 and any(p.startswith("STALE") for p in problems)


def test_three_bdays_old_is_ok_four_is_not():
    assert not cdf.check_series(bars(end=date(2026, 10, 2)), ASOF)[3]   # Fri -> 3 bdays
    assert any(p.startswith("STALE") for p in cdf.check_series(bars(end=date(2026, 10, 1)), ASOF)[3])


def test_gap_fails_and_names_day():
    hole = date(2026, 9, 24)
    _, _, missing, problems = cdf.check_series(bars(skip=[hole]), ASOF)
    assert missing == [hole]
    assert any(p.startswith("GAPS") and "2026-09-24" in p for p in problems)


def test_empty_series_fails():
    assert any(p.startswith("EMPTY") for p in cdf.check_series([], ASOF)[3])


def test_empty_file_fails_loudly(tmp_path):
    (tmp_path / "SPY.csv").write_text("")
    out = io.StringIO()
    assert cdf.run(cdf.load_dir(str(tmp_path), ["SPY"]), ASOF, out) == 1
    assert "EMPTY" in out.getvalue() and "DATA CHECK FAILED" in out.getvalue()


def test_header_only_file_fails(tmp_path):
    write(tmp_path, "SPY", [])
    assert cdf.run(cdf.load_dir(str(tmp_path), ["SPY"]), ASOF, io.StringIO()) == 1


def test_missing_and_garbage_files_fail(tmp_path):
    (tmp_path / "BAD.csv").write_text("foo,bar\n1,2\n")
    out = io.StringIO()
    assert cdf.run(cdf.load_dir(str(tmp_path), ["BAD", "NOPE"]), ASOF, out) == 1
    assert "UNREADABLE" in out.getvalue() and "MISSING" in out.getvalue()


def test_main_exit_codes(tmp_path):
    write(tmp_path, "SPY", bars())
    args = ["--dir", str(tmp_path), "--asof", str(ASOF), "--tickers", "SPY"]
    assert cdf.main(args) == 0
    write(tmp_path, "SPY", bars(end=ASOF - timedelta(days=14)))
    assert cdf.main(args) == 1


def test_universe_includes_spy_and_core():
    u = cdf.universe()
    assert "SPY" in u and "AAPL" in u


def test_known_holidays():
    assert date(2026, 4, 3) in cdf.nyse_holidays(2026)    # Good Friday
    assert date(2026, 11, 26) in cdf.nyse_holidays(2026)  # Thanksgiving
    assert date(2026, 7, 3) in cdf.nyse_holidays(2026)    # Jul 4 is Saturday -> Fri observed
