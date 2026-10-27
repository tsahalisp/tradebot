"""Regression tests for tools/portfolio_params_snapshot.py (live-param drift guard).

All configs are synthetic files under tmp_path; no live source, network or DB is touched.
Placebo: an unchanged config must produce zero problems -- every drift test is
paired against that baseline.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import portfolio_params_snapshot as pps  # noqa: E402

SRC = "N = 10\nMIN_CONV = 45.0\nUNIVERSE = ['AAPL', 'MSFT']\nW = {'a': 1, 'b': 2}\n\ndef pick(x):\n    return x[:N]\n"
MANIFEST = {"alpha": {"universe_rule": "u1", "files": {
    "alpha.py": {"constants": ["N", "MIN_CONV", "UNIVERSE", "W"], "functions": ["pick"]}}}}


def write(tmp_path, src=SRC, name="alpha.py"):
    (tmp_path / name).write_text(src)


def snap(tmp_path, manifest=MANIFEST):
    cur, errs = pps.collect(tmp_path, manifest)
    assert not errs
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(cur))
    return p


def run(tmp_path, snapshot, manifest=MANIFEST):
    return pps.main(["--root", str(tmp_path), "--snapshot", str(snapshot)], manifest=manifest)


def test_placebo_unchanged_config_passes(tmp_path, capsys):
    write(tmp_path)
    s = snap(tmp_path)
    assert run(tmp_path, s) == 0
    out = capsys.readouterr()
    assert "alpha" in out.out and "OK" in out.out and out.err == ""


def test_changed_parameter_flagged_with_field_name(tmp_path, capsys):
    write(tmp_path)
    s = snap(tmp_path)
    write(tmp_path, SRC.replace("MIN_CONV = 45.0", "MIN_CONV = 50.0"))
    assert run(tmp_path, s) == 1
    err = capsys.readouterr().err
    assert "alpha" in err and "MIN_CONV" in err and "DRIFT" in err
    assert "N:" not in err.replace("MIN_CONV", "")  # unchanged fields are not blamed


@pytest.mark.parametrize("old,new,field", [
    ("UNIVERSE = ['AAPL', 'MSFT']", "UNIVERSE = ['AAPL']", "UNIVERSE"),
    ("{'a': 1, 'b': 2}", "{'a': 1, 'b': 3}", "W"),
    ("return x[:N]", "return x[:N + 1]", "fn:pick"),
])
def test_other_param_kinds_flagged(tmp_path, capsys, old, new, field):
    write(tmp_path)
    s = snap(tmp_path)
    write(tmp_path, SRC.replace(old, new))
    assert run(tmp_path, s) == 1
    assert field in capsys.readouterr().err


def test_comment_only_edit_is_not_drift(tmp_path):
    write(tmp_path)
    s = snap(tmp_path)
    write(tmp_path, SRC.replace("N = 10", "N = 10  # same value"))
    assert run(tmp_path, s) == 0


def test_missing_config_reported_not_skipped(tmp_path, capsys):
    write(tmp_path)
    s = snap(tmp_path)
    (tmp_path / "alpha.py").unlink()
    assert run(tmp_path, s) == 1
    err = capsys.readouterr().err
    assert "alpha" in err and "alpha.py" in err and "CONFIG ERROR" in err


def test_unreadable_and_missing_param_reported(tmp_path, capsys):
    write(tmp_path)
    s = snap(tmp_path)
    write(tmp_path, "N = (\n")  # syntax error
    assert run(tmp_path, s) == 1
    assert "SyntaxError" in capsys.readouterr().err
    write(tmp_path, SRC.replace("MIN_CONV = 45.0\n", ""))  # parameter deleted
    assert run(tmp_path, s) == 1
    assert "MIN_CONV" in capsys.readouterr().err
    write(tmp_path, SRC.replace("N = 10", "N = int('10')"))  # no longer a literal
    assert run(tmp_path, s) == 1


def test_missing_or_corrupt_snapshot_fails(tmp_path, capsys):
    write(tmp_path)
    assert run(tmp_path, tmp_path / "nope.json") == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert run(tmp_path, bad) == 1
    assert "SNAPSHOT ERROR" in capsys.readouterr().err


def test_hash_deterministic_and_key_order_independent():
    a = {"x": 1, "y": {"p": [1, 2], "q": 3}, "z": "s"}
    b = {"z": "s", "y": {"q": 3, "p": [1, 2]}, "x": 1}
    assert pps.params_hash(a) == pps.params_hash(b) == pps.params_hash(dict(a))
    assert pps.params_hash(a) != pps.params_hash({**a, "x": 2})
    assert pps.params_hash(a) != pps.params_hash({**a, "y": {"p": [2, 1], "q": 3}})  # list order matters


def test_collect_independent_of_source_key_order(tmp_path):
    write(tmp_path)
    h1 = pps.collect(tmp_path, MANIFEST)[0]["alpha"]["hash"]
    write(tmp_path, SRC.replace("{'a': 1, 'b': 2}", "{'b': 2, 'a': 1}"))
    assert pps.collect(tmp_path, MANIFEST)[0]["alpha"]["hash"] == h1


def test_new_portfolio_not_in_snapshot_flagged(tmp_path, capsys):
    write(tmp_path)
    write(tmp_path, SRC, "beta.py")
    s = snap(tmp_path)  # snapshot knows only alpha
    both = {**MANIFEST, "beta": {"universe_rule": "u2", "files": {"beta.py": {"constants": ["N"]}}}}
    assert run(tmp_path, s, both) == 1
    err = capsys.readouterr().err
    assert "beta" in err and "NEW" in err and "alpha:" not in err


def test_removed_portfolio_flagged(tmp_path, capsys):
    write(tmp_path)
    write(tmp_path, SRC, "beta.py")
    both = {**MANIFEST, "beta": {"universe_rule": "u2", "files": {"beta.py": {"constants": ["N"]}}}}
    s = snap(tmp_path, both)
    assert run(tmp_path, s, MANIFEST) == 1
    err = capsys.readouterr().err
    assert "beta" in err and "REMOVED" in err


def test_update_refuses_on_missing_config_and_writes_otherwise(tmp_path):
    out = tmp_path / "s.json"
    args = ["--root", str(tmp_path), "--snapshot", str(out), "--update"]
    assert pps.main(args, manifest=MANIFEST) == 1 and not out.exists()
    write(tmp_path)
    assert pps.main(args, manifest=MANIFEST) == 0 and out.exists()
    assert run(tmp_path, out) == 0


def test_committed_snapshot_matches_live_params():
    """Real guard: the repo's live paper params equal the committed snapshot."""
    assert pps.main([]) == 0
