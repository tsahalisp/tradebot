"""Tests for tools/benchmark_placebo_check.py. Synthetic files in tmp_path only.

Benchmark/placebo for this guard itself: the positive cases (placebo present) must
pass and the negative baseline cases must fail, so the checker discriminates.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import benchmark_placebo_check as bpc  # noqa: E402


def write(d, name, body):
    p = d / name
    p.write_text(body)
    return p


def run(d, capsys, *extra):
    code = bpc.main([str(d), *extra])
    return code, capsys.readouterr()


GOOD = "def test_a():\n    benchmark = 1\n    assert benchmark\n"
BAD = "def test_a():\n    assert 1 + 1 == 2\n"


def test_file_with_placebo_passes(tmp_path, capsys):
    write(tmp_path, "test_good.py", GOOD)
    code, out = run(tmp_path, capsys)
    assert code == 0
    assert "test_good.py" in out.out and "yes" in out.out


def test_file_without_placebo_flagged_by_name(tmp_path, capsys):
    write(tmp_path, "test_good.py", GOOD)
    write(tmp_path, "test_bad.py", BAD)
    code, out = run(tmp_path, capsys)
    assert code == 1
    assert "test_bad.py" in out.err and "test_good.py" not in out.err


def test_syntax_error_file_is_failure_not_skipped(tmp_path, capsys):
    write(tmp_path, "test_good.py", GOOD)
    write(tmp_path, "test_broken.py", "def test_x(:\n")
    code, out = run(tmp_path, capsys)
    assert code == 1
    assert "test_broken.py" in out.err and "cannot parse" in out.err
    assert "test_broken.py" in out.out  # still shown in the table


def test_allowlisted_file_passes(tmp_path, capsys):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write(tdir, "test_io.py", BAD)
    al = write(tmp_path, "al.json", json.dumps({"allow": [{"file": "test_io.py", "reason": "pure IO"}]}))
    code, out = run(tdir, capsys, "--allowlist", str(al))
    assert code == 0
    assert "allowlisted" in out.out


@pytest.mark.parametrize("entry", [
    {"file": "test_io.py"},
    {"file": "test_io.py", "reason": ""},
    {"file": "test_io.py", "reason": "   "},
])
def test_allowlist_entry_without_reason_rejected(tmp_path, capsys, entry):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write(tdir, "test_io.py", BAD)
    al = write(tmp_path, "al.json", json.dumps({"allow": [entry]}))
    code, out = run(tdir, capsys, "--allowlist", str(al))
    assert code == 1
    assert "reason" in out.err


def test_invalid_allowlist_json_fails(tmp_path, capsys):
    tdir = tmp_path / "t"
    tdir.mkdir()
    write(tdir, "test_good.py", GOOD)
    al = write(tmp_path, "al.json", "{not json")
    code, _ = run(tdir, capsys, "--allowlist", str(al))
    assert code == 1


def test_empty_directory_fails(tmp_path, capsys):
    code, out = run(tmp_path, capsys)
    assert code == 1 and "no test_*.py" in out.err


def test_missing_path_fails(tmp_path, capsys):
    code, out = run(tmp_path / "nope", capsys)
    assert code == 1 and "not found" in out.err


@pytest.mark.parametrize("body", [
    "import random\ndef test_a():\n    random.seed(42)\n",
    "import numpy as np\ndef test_a():\n    rng = np.random.default_rng(7)\n",
    "import numpy as np\ndef test_a():\n    np.random.seed(0)\n",
    "import random\ndef test_a():\n    r = random.Random(3)\n",
    "def test_a():\n    make(random_state=1)\n",
])
def test_seeded_random_counts(body):
    assert bpc.analyze(body)[1] is True


@pytest.mark.parametrize("body", [
    "import random\ndef test_a():\n    x = random.random()\n",
    "import numpy as np\ndef test_a():\n    rng = np.random.default_rng()\n",
    "import numpy as np\ndef test_a():\n    rng = np.random.default_rng(None)\n",
    "import random\ndef test_a():\n    random.seed()\n",
    "import random\ndef test_a():\n    random.seed(some_var)\n",
    "def test_a():\n    make(random_state=None)\n",
])
def test_unseeded_random_does_not_count(body):
    assert bpc.analyze(body)[1] is False


@pytest.mark.parametrize("body", [
    "def test_a():\n    x = 'SPY-like series'\n",
    "def test_a(spy_prices):\n    pass\n",
    "def test_placebo_shuffle():\n    pass\n",
    "def test_a():\n    BaselineRun = 1\n",
])
def test_name_and_string_references(body):
    assert bpc.analyze(body)[1] is True


def test_no_false_hit_on_substring_spy():
    assert bpc.analyze("def test_a():\n    x = 'espying'\n")[1] is False


def test_test_count(tmp_path):
    body = "def test_a(): pass\nclass TestX:\n    def test_b(self): pass\n    def helper(self): pass\n"
    assert bpc.analyze(body)[0] == 2


def test_scanned_file_is_not_executed(tmp_path, capsys):
    marker = tmp_path / "ran"
    write(tmp_path, "test_side.py",
          f"open({str(marker)!r}, 'w').write('x')\ndef test_a():\n    baseline = 1\n")
    code, _ = run(tmp_path, capsys)
    assert code == 0 and not marker.exists()


def test_shipped_allowlist_is_empty_and_valid():
    allowed, errs = bpc.load_allowlist(bpc.DEFAULT_ALLOWLIST)
    assert allowed == {} and errs == []
