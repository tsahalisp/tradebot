"""Tests for tools/universe_pit_check.py (read-only point-in-time universe check).

Existing coverage found: none. README mentions universe.py / momentum_pit.py but neither is
in the repo, and no membership file exists; autoportfolio.universe_for is a static list
(MEGACAP_CORE + congress buys) with no dates. backend/tests/ only covers data loading.
So all inputs here are synthetic files in tmp_path with fixed seeds.
Benchmark/placebo: a random-signal strategy on the clean PIT universe must show ~zero edge
vs an equal-weight baseline; the same signal on a survivors-only universe must show inflated edge.
"""
import csv
import json
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import universe_pit_check as upc  # noqa: E402

D = date.fromisoformat
ASOFS = [date(2021, 6, 30), date(2022, 6, 30), date(2023, 6, 30)]


def write_csv(path, rows, header=("symbol", "added_date", "removed_date")):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def clean_rows(n=30, removed=3):
    rows = [(f"S{i:02d}", "2019-01-01", "") for i in range(n)]
    for i in range(removed):
        rows[i] = (rows[i][0], "2019-01-01", "2022-01-01")
    return rows


def run(path, capsys, **kw):
    rc = upc.run(path, **kw)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# (1) clean universe passes + placebo benchmark
def test_clean_universe_passes(tmp_path, capsys):
    p = write_csv(tmp_path / "u.csv", clean_rows())
    rc, out, err = run(p, capsys, asofs=ASOFS)
    assert rc == 0 and err == ""
    assert "2021-06-30" in out and "30" in out and "OK" in out


def test_clean_universe_json_and_default_asofs(tmp_path, capsys):
    p = tmp_path / "u.json"
    p.write_text(json.dumps([dict(symbol=s, added_date=a, removed_date=r or None)
                             for s, a, r in clean_rows()]))
    rc, out, err = run(p, capsys)
    assert rc == 0, err


def simulate(seed=7, n_sym=400, months=120, k=50):
    """Monthly random-signal strategy vs equal-weight PIT baseline. Returns mean edge in %/month
    for (clean PIT universe, survivors-only universe)."""
    rng = random.Random(seed)
    syms = []
    for _ in range(n_sym):
        dead = rng.random() < 0.4
        end = rng.randrange(24, months) if dead else months
        syms.append((dead, end))
    ret = {}
    for i, (dead, end) in enumerate(syms):
        for m in range(end):
            r = rng.gauss(0.007, 0.06)
            if dead and m >= end - 12:
                r -= 0.03          # slide before delisting
            if dead and m == end - 1:
                r = -0.6           # terminal loss
            ret[i, m] = r
    pick_rng = random.Random(seed + 1)
    clean, surv = [], []
    for m in range(months):
        alive = [i for i, (_, e) in enumerate(syms) if m < e]
        base = statistics.fmean(ret[i, m] for i in alive)
        pit = pick_rng.sample(alive, k)
        so = pick_rng.sample([i for i in alive if not syms[i][0]], k)
        clean.append(statistics.fmean(ret[i, m] for i in pit) - base)
        surv.append(statistics.fmean(ret[i, m] for i in so) - base)
    return 100 * statistics.fmean(clean), 100 * statistics.fmean(surv)


def test_placebo_clean_near_zero_survivors_inflated(tmp_path, capsys):
    clean_edge, surv_edge = simulate()
    assert abs(clean_edge) < 0.15, clean_edge
    assert surv_edge > 0.2, surv_edge
    assert surv_edge > clean_edge + 0.25


def test_survivors_only_file_is_flagged_but_pit_file_is_not(tmp_path, capsys):
    late = [("LATE", "2024-01-01", "")]
    pit = write_csv(tmp_path / "pit.csv", clean_rows(40, removed=10) + late)
    surv = write_csv(tmp_path / "surv.csv", clean_rows(40, removed=0) + late)
    assert run(pit, capsys, asofs=ASOFS)[0] == 0
    assert run(surv, capsys, asofs=ASOFS)[0] == 1


# (2) look-ahead membership
def test_lookahead_membership(tmp_path, capsys):
    rows = clean_rows() + [("NEWCO", "2023-01-01", "")]
    p = write_csv(tmp_path / "u.csv", rows)
    u = write_csv(tmp_path / "use.csv", [("2021-06-30", "NEWCO"), ("2021-06-30", "S05")],
                  header=("asof_date", "symbol"))
    rc, out, err = run(p, capsys, asofs=ASOFS, usage_path=u)
    assert rc == 1
    assert f"file={p}" in err and "symbol=NEWCO" in err and "check=lookahead" in err
    assert "S05" not in err


def test_usage_of_removed_and_unknown_symbol(tmp_path, capsys):
    p = write_csv(tmp_path / "u.csv", clean_rows())
    u = write_csv(tmp_path / "use.csv", [("2023-06-30", "S00"), ("2023-06-30", "GHOST")],
                  header=("asof_date", "symbol"))
    rc, out, err = run(p, capsys, asofs=ASOFS, usage_path=u)
    assert rc == 1 and "symbol=S00 check=lookahead" in err and "symbol=GHOST check=lookahead" in err


# (3) survivorship-only
def test_survivorship_only_universe(tmp_path, capsys):
    p = write_csv(tmp_path / "u.csv", clean_rows(30, removed=0) + [("LATE", "2024-01-01", "")])
    rc, out, err = run(p, capsys, asofs=ASOFS)
    assert rc == 1
    assert "check=survivorship" in err and "2019-01-01..2024-01-01" in err and f"file={p}" in err


def test_short_span_without_removals_not_survivorship(tmp_path, capsys):
    rows = [(f"S{i}", "2023-01-01", "") for i in range(25)]
    rc, out, err = run(write_csv(tmp_path / "u.csv", rows), capsys, asofs=[D("2023-06-30")])
    assert rc == 0, err


# (4) inverted / overlapping
def test_inverted_interval(tmp_path, capsys):
    rows = clean_rows() + [("BAD", "2021-01-01", "2020-01-01")]
    rc, out, err = run(write_csv(tmp_path / "u.csv", rows), capsys, asofs=ASOFS)
    assert rc == 1 and "symbol=BAD check=interval" in err and "before added_date" in err


def test_overlapping_duplicates_but_readd_after_removal_ok(tmp_path, capsys):
    rows = clean_rows() + [("S10", "2021-01-01", "")]          # S10 already open from 2019
    rc, out, err = run(write_csv(tmp_path / "u.csv", rows), capsys, asofs=ASOFS)
    assert rc == 1 and "symbol=S10 check=interval" in err and "overlap" in err
    rows = clean_rows() + [("S00", "2022-06-01", "")]          # S00 removed 2022-01-01, re-added
    rc, out, err = run(write_csv(tmp_path / "u2.csv", rows), capsys, asofs=ASOFS)
    assert rc == 0, err


# (5) bad / missing dates, empty symbols
@pytest.mark.parametrize("row,needle", [
    (("BADD", "not-a-date", ""), "symbol=BADD check=bad_row"),
    (("NOADD", "", ""), "symbol=NOADD check=bad_row"),
    (("BADR", "2020-01-01", "31/12/2022"), "symbol=BADR check=bad_row"),
    (("", "2020-01-01", ""), "check=bad_row"),
    (("  ", "2020-01-01", ""), "check=bad_row"),
])
def test_bad_rows_are_reported_not_skipped(tmp_path, capsys, row, needle):
    p = write_csv(tmp_path / "u.csv", clean_rows() + [row])
    rc, out, err = run(p, capsys, asofs=ASOFS)
    assert rc == 1 and needle in err and f"file={p}" in err and "row 31" in err


# (6) small universe
def test_small_universe_insufficient_data(tmp_path, capsys):
    rc, out, err = run(write_csv(tmp_path / "u.csv", clean_rows(19, removed=1)), capsys, asofs=ASOFS)
    assert rc == 1 and "check=small_universe" in err and "insufficient data" in err
    assert "n/a" in out and "insufficient data" in out


def test_exactly_20_members_ok_and_threshold_per_date(tmp_path, capsys):
    rows = clean_rows(21, removed=1)   # 21 until 2022-01-01, then 20
    assert run(write_csv(tmp_path / "u.csv", rows), capsys, asofs=ASOFS)[0] == 0
    rows = clean_rows(20, removed=1)   # dips to 19 after 2022-01-01
    rc, out, err = run(write_csv(tmp_path / "v.csv", rows), capsys, asofs=ASOFS)
    assert rc == 1 and "as-of 2022-06-30: 19" in err and "as-of 2021-06-30" not in err


# (7) missing / malformed file
def test_missing_file(tmp_path, capsys):
    p = tmp_path / "nope.csv"
    rc, out, err = run(p, capsys, asofs=ASOFS)
    assert rc == 1 and f"file={p}" in err and "check=bad_file" in err


@pytest.mark.parametrize("name,content", [
    ("empty.csv", ""),
    ("hdr.csv", "symbol,added_date,removed_date\n"),
    ("cols.csv", "ticker,start\nAAA,2020-01-01\n"),
    ("bad.json", "{not json"),
    ("obj.json", '{"symbol": "A"}'),
    ("wide.csv", "symbol,added_date,removed_date\nAAA,2020-01-01,,extra\n"),
])
def test_malformed_file(tmp_path, capsys, name, content):
    p = tmp_path / name
    p.write_text(content)
    rc, out, err = run(p, capsys, asofs=ASOFS)
    assert rc == 1 and f"file={p}" in err and "check=bad_file" in err


def test_cli_main_exit_codes(tmp_path, capsys):
    p = write_csv(tmp_path / "u.csv", clean_rows())
    assert upc.main([str(p), "--asof", "2021-06-30"]) == 0
    assert upc.main([str(p), "--asof", "garbage"]) == 1
    assert upc.main([str(tmp_path / "x.csv")]) == 1
    assert "check=bad_file" in capsys.readouterr().err


def test_does_not_modify_input(tmp_path, capsys):
    p = write_csv(tmp_path / "u.csv", clean_rows())
    before = p.read_bytes()
    run(p, capsys, asofs=ASOFS)
    assert p.read_bytes() == before
