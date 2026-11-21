#!/usr/bin/env python3
"""Run every tools/*_check.py as a subprocess and print one summary table.

Read-only aggregator: it only launches the check scripts and reports.
Exit-code convention assumed for each check: 0 = PASS, 1 = FAIL, anything
else (crash, signal, other code) = ERROR. Timeout = TIMEOUT. A check mapped
to null in the argument map, or expected but absent, is never skipped
silently: it shows as NOT-RUN / ERROR and the runner exits non-zero.

Usage: run_all_checks.py [--tools-dir DIR] [--timeout SEC]
                         [--args-map JSON_FILE] [--expect NAME ...]
args-map JSON: {"data_gap_alarm_check.py": ["--days", "5"], ...}
Scripts absent from the map run with no arguments (their own defaults).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PASS, FAIL, ERROR, TIMEOUT, NOT_RUN = "PASS", "FAIL", "ERROR", "TIMEOUT", "NOT-RUN"
DEFAULT_TIMEOUT = 300
TAIL_LINES = 5


def _tail(text, n=TAIL_LINES):
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return [l for l in (text or "").splitlines() if l.strip()][-n:]


def validate_arg_map(arg_map):
    """Raise ValueError unless arg_map is {str: list[str] | None}."""
    if not isinstance(arg_map, dict):
        raise ValueError("argument map must be a JSON object")
    for k, v in arg_map.items():
        if not isinstance(k, str):
            raise ValueError(f"argument map key {k!r} is not a string")
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(a, str) for a in v):
            raise ValueError(f"arguments for {k!r} must be a list of strings or null")
    return arg_map


def discover(tools_dir):
    return sorted(Path(tools_dir).glob("*_check.py"))


def run_one(script, args, timeout):
    """Return dict(name, status, reason, tail)."""
    name = Path(script).name
    if args is None:
        return dict(name=name, status=NOT_RUN, reason="disabled in argument map", tail=[])
    if not Path(script).is_file():
        return dict(name=name, status=ERROR, reason="script missing", tail=[])
    try:
        p = subprocess.run([sys.executable, str(script), *args], capture_output=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        return dict(name=name, status=TIMEOUT, reason=f"exceeded {timeout}s",
                    tail=_tail(e.stdout) + _tail(e.stderr))
    except (OSError, ValueError) as e:
        return dict(name=name, status=ERROR, reason=f"cannot launch: {e}", tail=[])
    tail = _tail(p.stdout) + _tail(p.stderr)
    tail = tail[-TAIL_LINES:]
    if p.returncode == 0:
        return dict(name=name, status=PASS, reason="exit 0", tail=tail)
    if p.returncode == 1:
        return dict(name=name, status=FAIL, reason=tail[-1] if tail else "exit 1", tail=tail)
    reason = f"unexpected exit code {p.returncode}"
    if tail:
        reason += f": {tail[-1]}"
    return dict(name=name, status=ERROR, reason=reason, tail=tail)


def run_all(tools_dir, arg_map=None, timeout=DEFAULT_TIMEOUT, expect=()):
    arg_map = validate_arg_map(arg_map or {})
    scripts = discover(tools_dir)
    results = [run_one(s, arg_map.get(s.name, []), timeout) for s in scripts]
    found = {s.name for s in scripts}
    for name in list(expect) + [k for k in arg_map if k not in found]:
        if name not in found and not any(r["name"] == name for r in results):
            results.append(dict(name=name, status=ERROR, reason="script missing", tail=[]))
    return results


def format_table(results):
    w = max([len(r["name"]) for r in results] + [5])
    lines = [f"{'CHECK':<{w}}  {'STATUS':<8}  REASON", "-" * (w + 40)]
    for r in results:
        lines.append(f"{r['name']:<{w}}  {r['status']:<8}  {r['reason']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tools-dir", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--args-map", help="JSON file: {script_name: [args] | null}")
    ap.add_argument("--expect", nargs="*", default=[], help="script names that must exist")
    ns = ap.parse_args(argv)

    arg_map = {}
    if ns.args_map:
        try:
            arg_map = validate_arg_map(json.loads(Path(ns.args_map).read_text()))
        except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
            print(f"FATAL: bad argument map: {e}", file=sys.stderr)
            return 2
    if ns.timeout <= 0:
        print("FATAL: --timeout must be positive", file=sys.stderr)
        return 2

    results = run_all(ns.tools_dir, arg_map, ns.timeout, ns.expect)
    if not results:
        print(f"FATAL: no *_check.py scripts found in {ns.tools_dir}", file=sys.stderr)
        return 3
    print(format_table(results))
    bad = [r for r in results if r["status"] != PASS]
    for r in bad:
        print(f"\n--- {r['name']} [{r['status']}] last output ---")
        print("\n".join(r["tail"]) or "(no output)")
    if bad:
        print("\nFAILED CHECKS: " + ", ".join(f"{r['name']}={r['status']}" for r in bad),
              file=sys.stderr)
        return 1
    print(f"\nall {len(results)} checks PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
