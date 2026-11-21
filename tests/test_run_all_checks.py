import json
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent.parent / "tools" / "run_all_checks.py"


def make(d, name, body):
    (d / name).write_text(body)


def run(tools, *extra, timeout=5):
    p = subprocess.run([sys.executable, str(RUNNER), "--tools-dir", str(tools),
                        "--timeout", str(timeout), *extra],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def tools(tmp_path):
    d = tmp_path / "tools"
    d.mkdir()
    return d


def test_all_pass_and_placebo_no_false_failures(tools):
    make(tools, "a_check.py", "import random\nrandom.seed(0)\nraise SystemExit(0)\n")
    make(tools, "placebo_check.py", "pass\n")  # no-op placebo always passes
    rc, out, err = run(tools)
    assert rc == 0, out + err
    assert out.count("PASS") >= 2 and "FAIL" not in out and "ERROR" not in out
    assert "placebo_check.py" in out


def test_failing_check_named(tools):
    make(tools, "ok_check.py", "pass\n")
    make(tools, "bad_check.py", "print('gap found')\nraise SystemExit(1)\n")
    rc, out, err = run(tools)
    assert rc != 0
    assert "bad_check.py" in err and "ok_check.py" not in err
    assert "gap found" in out


def test_crash_is_error(tools):
    make(tools, "boom_check.py", "raise RuntimeError('x')\n")
    rc, out, err = run(tools)
    assert rc != 0
    line = next(l for l in out.splitlines() if l.startswith("boom_check.py"))
    assert "ERROR" in line or "FAIL" in line
    assert "boom_check.py" in err


def test_unexpected_exit_code_is_error(tools):
    make(tools, "odd_check.py", "raise SystemExit(7)\n")
    rc, out, _ = run(tools)
    assert rc != 0 and "ERROR" in out and "7" in out


def test_timeout_reported(tools):
    make(tools, "slow_check.py", "import time\ntime.sleep(30)\n")
    rc, out, err = run(tools, timeout=1)
    assert rc != 0 and "TIMEOUT" in out and "slow_check.py" in err


def test_empty_tools_dir_fails(tools):
    rc, out, err = run(tools)
    assert rc != 0 and "no *_check.py" in err


@pytest.mark.parametrize("content", ["{not json", "[1,2]", '{"a_check.py": "--x"}',
                                     '{"a_check.py": [1]}'])
def test_malformed_arg_map_fails(tools, tmp_path, content):
    make(tools, "a_check.py", "pass\n")
    m = tmp_path / "map.json"
    m.write_text(content)
    rc, out, err = run(tools, "--args-map", str(m))
    assert rc != 0 and "bad argument map" in err


def test_arg_map_passes_args_and_null_is_not_run(tools, tmp_path):
    make(tools, "a_check.py", "import sys\nraise SystemExit(0 if sys.argv[1:]==['--k','v'] else 1)\n")
    make(tools, "b_check.py", "pass\n")
    m = tmp_path / "map.json"
    m.write_text(json.dumps({"a_check.py": ["--k", "v"], "b_check.py": None}))
    rc, out, err = run(tools, "--args-map", str(m))
    assert rc != 0 and "NOT-RUN" in out
    assert "b_check.py" in err and "a_check.py=" not in err


def test_missing_expected_script_is_error(tools):
    make(tools, "a_check.py", "pass\n")
    rc, out, err = run(tools, "--expect", "gone_check.py")
    assert rc != 0 and "gone_check.py" in err and "script missing" in out
