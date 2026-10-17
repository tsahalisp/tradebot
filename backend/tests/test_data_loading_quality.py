"""Input data-quality tests for the ingest layer (datasource + holdings.import_csv).

The repo has no file-based prices/NAV/trades loader; the real ingest points are
datasource._coinbase (JSON candles), datasource._equity (yfinance frame -> bars)
and holdings.import_csv (broker CSV). All data here is synthetic (tmp_path / fakes).
Tests marked xfail(strict=True) document REAL bugs; the code is deliberately not fixed.
Benchmark: a clean SPY-like series must pass every check with zero flags.
"""
import io
import json
import math
import sys
import types

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import datasource  # noqa: E402
import holdings  # noqa: E402

DAY = 86400
T0 = 1_700_006_400  # a day-aligned epoch


def candle(t, c):
    # coinbase row: [time, low, high, open, close, volume]
    return [t, c, c, c, c, 1000.0]


def load_coinbase(monkeypatch, rows):
    class R(io.BytesIO):
        pass
    monkeypatch.setattr(datasource.urllib.request, "urlopen",
                        lambda req, timeout=0: R(json.dumps(rows).encode()))
    return datasource._coinbase("SPY-USD", "1d", 10_000)


def check_bars(bars, max_jump=0.5):
    """Reference data-quality checker: returns list of flag strings."""
    flags = []
    ts = [b["t"] for b in bars]
    if len(set(ts)) != len(ts):
        flags.append("duplicate_dates")
    if ts != sorted(ts):
        flags.append("unsorted")
    for b in bars:
        c = b["c"]
        if c is None or (isinstance(c, float) and math.isnan(c)):
            flags.append("nan_price")
        elif c <= 0:
            flags.append("non_positive_price")
    cs = [b["c"] for b in bars if b["c"] and not math.isnan(b["c"]) and b["c"] > 0]
    if any(abs(b / a - 1) > max_jump for a, b in zip(cs, cs[1:])):
        flags.append("extreme_jump")
    return flags


def spy_like(n=300):
    return [candle(T0 + i * DAY, 400 * (1 + 0.001 * i) * (1 + 0.004 * ((-1) ** i))) for i in range(n)]


# ---------- placebo / benchmark: clean SPY-like series passes with no flags ----------
def test_clean_spy_like_series_has_no_flags(monkeypatch):
    bars = load_coinbase(monkeypatch, spy_like())
    assert len(bars) == 300
    assert check_bars(bars) == []


# ---------- (1) duplicates ----------
@pytest.mark.xfail(strict=True, reason="BUG: _coinbase keeps duplicate timestamps silently (no dedupe/flag/raise)")
def test_duplicate_dates_not_silently_kept(monkeypatch):
    rows = spy_like(10) + [candle(T0 + 3 * DAY, 999.0)]
    try:
        bars = load_coinbase(monkeypatch, rows)
    except ValueError:
        return
    assert "duplicate_dates" not in check_bars(bars), "duplicate timestamp passed through silently"


def test_checker_flags_duplicates_on_clean_vs_dup():
    ok = [{"t": "2024-01-0%d" % i, "c": 1.0 + i} for i in range(1, 5)]
    assert check_bars(ok) == []
    assert "duplicate_dates" in check_bars(ok + [{"t": "2024-01-02", "c": 5.0}])


# ---------- (2) ordering ----------
def test_unsorted_input_is_sorted_deterministically(monkeypatch):
    rows = spy_like(20)
    shuffled = rows[::-1][5:] + rows[::-1][:5]
    a = load_coinbase(monkeypatch, shuffled)
    b = load_coinbase(monkeypatch, rows)
    assert [x["t"] for x in a] == sorted(x["t"] for x in a)
    assert a == b
    assert check_bars(a) == []


@pytest.mark.xfail(strict=True, reason="BUG: _coinbase re-sorts unsorted input with no warning/flag (silent reorder)")
def test_unsorted_input_emits_warning(monkeypatch):
    rows = spy_like(20)
    with pytest.warns(Warning):
        load_coinbase(monkeypatch, rows[::-1])


# ---------- (3) bad values ----------
@pytest.mark.xfail(strict=True, reason="BUG: _coinbase passes non-positive close prices through unflagged")
@pytest.mark.parametrize("bad", [0.0, -5.0])
def test_non_positive_price_flagged(monkeypatch, bad):
    rows = spy_like(10)
    rows[4] = candle(rows[4][0], bad)
    try:
        bars = load_coinbase(monkeypatch, rows)
    except ValueError:
        return
    assert all(b["c"] > 0 for b in bars), "non-positive price passed silently"


@pytest.mark.xfail(strict=True, reason="BUG: _coinbase passes NaN closes through (json NaN -> float nan) unflagged")
def test_nan_price_flagged(monkeypatch):
    rows = spy_like(10)
    rows[4][4] = float("nan")
    try:
        bars = load_coinbase(monkeypatch, rows)
    except ValueError:
        return
    assert not any(math.isnan(b["c"]) for b in bars), "NaN price passed silently"


@pytest.mark.xfail(strict=True, reason="BUG: _coinbase passes a >50% one-day jump (split-like error) through unflagged")
def test_extreme_jump_flagged(monkeypatch):
    rows = spy_like(10)
    for r in rows[5:]:
        r[1] = r[2] = r[3] = r[4] = r[4] * 0.4   # 60% overnight drop
    try:
        bars = load_coinbase(monkeypatch, rows)
    except ValueError:
        return
    assert "extreme_jump" not in check_bars(bars), "split-like jump passed silently"


def test_reference_checker_detects_each_bad_value():
    base = [{"t": i, "c": 100.0 + i} for i in range(5)]
    assert check_bars(base) == []
    assert "non_positive_price" in check_bars(base[:2] + [{"t": 9, "c": 0.0}])
    assert "nan_price" in check_bars(base[:2] + [{"t": 9, "c": float("nan")}])
    assert "extreme_jump" in check_bars(base[:2] + [{"t": 9, "c": 30.0}])


# ---------- equity path: NaN rows are dropped (existing behaviour), unsorted index is not ----------
class _Idx:
    def __init__(self, d): self.d = d
    def date(self): return self.d


class _Row(dict):
    pass


class _Frame:
    columns = types.SimpleNamespace(nlevels=1)
    def __init__(self, rows): self.rows = rows
    def dropna(self):
        return _Frame([(i, r) for i, r in self.rows
                       if not any(isinstance(v, float) and math.isnan(v) for v in r.values())])
    def __len__(self): return len(self.rows)
    def iterrows(self): return iter(self.rows)


def load_equity(monkeypatch, rows):
    fake = types.ModuleType("yfinance")
    fake.download = lambda *a, **k: _Frame(rows)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    return datasource._equity("SPY", 10_000)


def eq_rows(n=300):
    out = []
    for i in range(n):
        c = 400 * (1 + 0.001 * i)
        out.append((_Idx(__import__("datetime").date(2023, 1, 1) + __import__("datetime").timedelta(days=i)),
                    {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6}))
    return out


def test_equity_clean_spy_like_no_flags(monkeypatch):
    bars = load_equity(monkeypatch, eq_rows())
    assert len(bars) == 300 and check_bars(bars) == []


def test_equity_nan_rows_are_dropped_not_passed(monkeypatch):
    rows = eq_rows(20)
    rows[7][1]["Close"] = float("nan")
    bars = load_equity(monkeypatch, rows)
    assert len(bars) == 19 and not any(math.isnan(b["c"]) for b in bars)


@pytest.mark.xfail(strict=True, reason="BUG: _equity drops NaN rows silently (no count/log/warning) and keeps duplicate/unsorted dates")
def test_equity_duplicate_and_unsorted_not_silent(monkeypatch):
    rows = eq_rows(10)
    rows.append(rows[2])               # duplicate date
    rows[0], rows[1] = rows[1], rows[0]  # out of order
    try:
        bars = load_equity(monkeypatch, rows)
    except ValueError:
        return
    assert check_bars(bars) == []


# ---------- (4) malformed / missing-column CSV (holdings.import_csv) ----------
@pytest.fixture
def hold_path(tmp_path, monkeypatch):
    monkeypatch.setattr(holdings, "HOLD_PATH", tmp_path / "holdings.json")
    return tmp_path / "holdings.json"


def test_csv_missing_quantity_column_names_the_column(hold_path):
    r = holdings.import_csv("Symbol,Price\nSPY,400\n")
    assert r["imported"] == 0 and "Quantity" in r["error"]
    assert not hold_path.exists()


def test_csv_missing_symbol_column_names_the_column(hold_path):
    r = holdings.import_csv("Quantity,Cost\n5,400\n")
    assert r["imported"] == 0 and "Symbol" in r["error"]


def test_csv_empty_and_header_only(hold_path):
    assert holdings.import_csv("")["imported"] == 0
    assert "error" in holdings.import_csv("Symbol,Quantity\n")


@pytest.mark.xfail(strict=True, reason="BUG: import_csv error message does not name the file/source (it takes text, not a path)")
def test_csv_error_names_file(hold_path, tmp_path):
    f = tmp_path / "broker_export.csv"
    f.write_text("Symbol,Price\nSPY,400\n")
    r = holdings.import_csv(f.read_text())
    assert "broker_export.csv" in r["error"]


@pytest.mark.xfail(strict=True, reason="BUG: import_csv silently skips unparseable rows (bad qty) and reports only 'imported' count, no skipped count/error")
def test_csv_bad_rows_are_reported(hold_path):
    r = holdings.import_csv("Symbol,Quantity\nSPY,abc\nAAPL,5\n")
    assert r["imported"] == 1
    assert r.get("skipped") or r.get("errors") or r.get("warnings")


@pytest.mark.xfail(strict=True, reason="BUG: import_csv accepts negative/zero quantities without flagging")
def test_csv_non_positive_quantity_flagged(hold_path):
    r = holdings.import_csv("Symbol,Quantity\nSPY,-5\nAAPL,0\n")
    assert r["imported"] == 0 or r.get("errors") or r.get("warnings")


def test_csv_clean_benchmark_imports_with_no_flags(hold_path):
    r = holdings.import_csv("Symbol,Quantity,Average Cost\nSPY,10,400\nAAPL,5,150\n")
    assert r == {"imported": 2, "total_holdings": 2}
    assert {h["ticker"] for h in json.loads(hold_path.read_text())} == {"SPY", "AAPL"}
