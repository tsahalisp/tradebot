"""Tests for tools/universe_pit_check.py. Synthetic data only (tmp_path, fixed seeds).

Benchmark/placebo: a clean PIT universe must pass, and a fixed-seed random subset of it
(placebo) must show no look-ahead flags. The repo contains no real universe membership
file or universe.py, so there is no real-universe test (see report).
"""
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import universe_pit_check as u  # noqa: E402

AS_OF = date(2024, 12, 31)


def checks(rep):
    return {c for c, _ in rep.failures}


def write(path, lines):
    path.write_text("\n".join(lines) + "\n")
    return path


def clean(tmp_path, **kw):
    p = tmp_path / "u.csv"
    u.synthetic(p, seed=42, as_of=AS_OF, **kw)
    return p


def test_clean_pit_passes(tmp_path):
    rep = u.check_file(clean(tmp_path), AS_OF)
    assert rep.ok, rep.failures
    assert len(rep.rows) >= 20 and any(k > 0 for _, _, k, _ in rep.rows)


def test_placebo_random_subset_no_lookahead(tmp_path):
    lines = clean(tmp_path).read_text().splitlines()
    hdr, body = lines[0], lines[1:]
    rng = random.Random(7)
    sub = rng.sample(body, len(body) // 2)
    rep = u.check_file(write(tmp_path / "placebo.csv", [hdr] + sub), AS_OF)
    assert not checks(rep) & {"FUTURE_ADD", "TRUNCATION"}, rep.failures
    assert not any("FUTURE" in f or "TRUNC" in f for *_, f in rep.rows)


def test_survivorship_only_flagged(tmp_path):
    rep = u.check_file(clean(tmp_path, survivors_only=True), AS_OF)
    assert "SURVIVORSHIP" in checks(rep)
    assert any("SURV" in f for *_, f in rep.rows)


def test_future_dated_addition(tmp_path):
    p = clean(tmp_path)
    with open(p, "a") as f:
        f.write("NEWCO,2025-06-01,\n")
    assert "FUTURE_ADD" in checks(u.check_file(p, AS_OF))


def test_snapshot_lookahead_and_truncation_mismatch(tmp_path):
    rows = ["date,symbol,added,removed"]
    for i in range(24):
        d = date(2022 + i // 12, i % 12 + 1, 28).isoformat()
        rows += [f"{d},AAA,2020-01-01,", f"{d},BBB,2020-01-01,2023-01-01" if i < 12 else f"{d},CCC,2020-01-01,"]
    rows.append("2022-03-28,ZZZ,2022-09-01,")  # member before its add date
    rep = u.check_file(write(tmp_path / "s.csv", rows), date(2024, 6, 1))
    assert {"FUTURE_ADD", "TRUNCATION"} <= checks(rep)


def test_snapshot_clean_passes(tmp_path):
    rows = ["date,symbol"]
    for i in range(24):
        d = date(2022 + i // 12, i % 12 + 1, 28).isoformat()
        rows += [f"{d},AAA", f"{d},BBB" if i < 12 else f"{d},CCC"]
    rep = u.check_file(write(tmp_path / "s.csv", rows), date(2024, 6, 1))
    assert rep.ok, rep.failures


def test_bad_dates_and_duplicates(tmp_path):
    p = clean(tmp_path)
    with open(p, "a") as f:
        f.write("BADD,not-a-date,\nMISS,,\nREV,2023-05-01,2023-01-01\n"
                "S001,2020-01-01,\nS001,2020-06-01,\n")
    c = checks(u.check_file(p, AS_OF))
    assert {"BAD_DATE", "REMOVED_BEFORE_ADDED", "DUP_SYMBOL"} <= c


def test_empty_and_short_history_insufficient(tmp_path):
    rep = u.check_file(write(tmp_path / "e.csv", ["symbol,added,removed"]), AS_OF)
    assert rep.insufficient and "INSUFFICIENT_DATA" in checks(rep) and rep.rows == []
    short = write(tmp_path / "s.csv", ["symbol,added,removed", "A,2024-01-01,", "B,2024-02-01,"])
    rep = u.check_file(short, AS_OF)
    assert rep.insufficient and rep.rows == []


def test_missing_and_malformed_file(tmp_path, capsys):
    assert u.main([str(tmp_path / "nope.csv"), "--as-of", "2024-12-31"]) == 2
    assert "nope.csv" in capsys.readouterr().err
    bad = write(tmp_path / "bad.csv", ["foo,bar", "1,2"])
    assert u.main([str(bad)]) == 2
    err = capsys.readouterr().err
    assert "bad.csv" in err and "INPUT" in err
    binf = tmp_path / "bin.csv"
    binf.write_bytes(b"\xff\xfe\x00\x80\x81")
    assert u.main([str(binf)]) == 2


def test_cli_names_file_and_check_and_exit_code(tmp_path, capsys):
    good, bad = clean(tmp_path), tmp_path / "s.csv"
    u.synthetic(bad, seed=1, as_of=AS_OF, survivors_only=True)
    assert u.main([str(good), "--as-of", "2024-12-31"]) == 0
    capsys.readouterr()
    assert u.main([str(good), str(bad), "--as-of", "2024-12-31"]) == 1  # no file skipped
    cap = capsys.readouterr()
    assert f"FAIL {bad}: SURVIVORSHIP" in cap.err and "date" in cap.out
