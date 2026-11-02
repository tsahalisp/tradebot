#!/usr/bin/env python3
"""Read-only static guard for the rule "every new test must report against a placebo/benchmark".

Scans test_*.py under a directory with `ast` (scanned files are never imported or run).
A file passes if it references a placebo/benchmark: any identifier or string token
containing 'placebo', 'benchmark' or 'baseline', the token 'spy', or a *seeded* random
source (random.seed(1), default_rng(1), RandomState(1), random.Random(1), random_state=1).
Unseeded randomness does not count. Unparseable files are failures, never skipped.

Allowlist (JSON): {"allow": [{"file": "test_io.py", "reason": "pure IO, no strategy output"}]}
An entry without a non-empty reason is rejected (exit non-zero).

Usage: benchmark_placebo_check.py [TESTS_DIR ...] [--allowlist PATH]
Exit 0 only if every file passes/is allowlisted and no errors occurred.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = Path(__file__).resolve().parent / "placebo_allowlist.json"
SUBSTRINGS = ("placebo", "benchmark", "baseline")
SEEDED_CTORS = {"default_rng", "RandomState", "Random"}


def _tokens(text):
    return [t for t in re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", text).lower()) if t]


def _name_hit(text):
    toks = _tokens(text)
    return any(s in t for t in toks for s in SUBSTRINGS) or "spy" in toks


def _chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def _fixed(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _seeded_call(call):
    chain = _chain(call.func)
    if not chain:
        return False
    name = chain[-1]
    seed_arg = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "seed"), None)
    if name in SEEDED_CTORS or (name == "seed" and "random" in [c.lower() for c in chain[:-1]]):
        if seed_arg is not None and _fixed(seed_arg):
            return True
    return any(k.arg == "random_state" and _fixed(k.value) for k in call.keywords)


def analyze(source, filename="<test>"):
    """Return (test_count, has_reference). Raises SyntaxError/ValueError on bad source."""
    tree = ast.parse(source, filename=filename)
    tests = 0
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found = found or _name_hit(node.name)
        elif isinstance(node, ast.Name):
            found = found or _name_hit(node.id)
        elif isinstance(node, ast.Attribute):
            found = found or _name_hit(node.attr)
        elif isinstance(node, ast.arg):
            found = found or _name_hit(node.arg)
        elif isinstance(node, ast.alias):
            found = found or _name_hit(node.name) or _name_hit(node.asname or "")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found = found or _name_hit(node.value)
        elif isinstance(node, ast.Call):
            found = found or _seeded_call(node)
    return tests, found


def load_allowlist(path):
    """Return ({file: reason}, [errors])."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        return {}, [f"allowlist {path}: unreadable/invalid JSON: {e}"]
    entries = data.get("allow") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}, [f"allowlist {path}: expected an object with an 'allow' list"]
    allowed, errors = {}, []
    for i, e in enumerate(entries):
        f = e.get("file") if isinstance(e, dict) else None
        reason = e.get("reason") if isinstance(e, dict) else None
        if not isinstance(f, str) or not f.strip():
            errors.append(f"allowlist entry #{i}: missing 'file'")
        elif not isinstance(reason, str) or not reason.strip():
            errors.append(f"allowlist entry '{f}': missing reason (a stated reason is required)")
        else:
            allowed[f] = reason.strip()
    return allowed, errors


def check(tests_dir, allowlist_path=None):
    """Return (rows, problems). rows: (file, tests, status). problems: failure strings."""
    problems, rows = [], []
    allowed = {}
    if allowlist_path is not None:
        allowed, errs = load_allowlist(allowlist_path)
        problems += errs
    d = Path(tests_dir)
    if not d.is_dir():
        return rows, problems + [f"tests path not found or not a directory: {d}"]
    files = sorted(d.glob("test_*.py"))
    if not files:
        return rows, problems + [f"no test_*.py files found in {d}"]
    for f in files:
        try:
            n, ok = analyze(f.read_bytes().decode("utf-8"), str(f))
        except (SyntaxError, ValueError, UnicodeDecodeError, OSError) as e:
            rows.append((f.name, 0, "ERROR"))
            problems.append(f"{f.name}: cannot parse ({type(e).__name__}: {e})")
            continue
        if ok:
            rows.append((f.name, n, "yes"))
        elif f.name in allowed:
            rows.append((f.name, n, f"allowlisted ({allowed[f.name]})"))
        else:
            rows.append((f.name, n, "NO"))
            problems.append(f"{f.name}: no placebo/benchmark reference")
    for name in sorted(set(allowed) - {f.name for f in files}):
        print(f"warning: allowlist entry '{name}' matches no scanned file", file=sys.stderr)
    return rows, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tests_dirs", nargs="*", help="default: tests/ and backend/tests/ under the repo root")
    ap.add_argument("--allowlist", default=None)
    args = ap.parse_args(argv)
    al = args.allowlist
    if al is None and DEFAULT_ALLOWLIST.exists():
        al = DEFAULT_ALLOWLIST
    dirs = args.tests_dirs or [str(ROOT / "tests"), str(ROOT / "backend" / "tests")]
    rows, problems = [], []
    for d in dirs:
        r, p = check(d, al)
        rows += [(f"{Path(d).name}/{n}" if len(dirs) > 1 else n, c, s) for n, c, s in r]
        problems += [f"{Path(d).name}/{x}" if len(dirs) > 1 and not x.startswith(("allowlist", "tests path", "no test_")) else x for x in p]
    if rows:
        w = max(len(r[0]) for r in rows + [("file", 0, "")])
        print(f"{'file'.ljust(w)}  tests  placebo")
        for name, n, status in rows:
            print(f"{name.ljust(w)}  {str(n).rjust(5)}  {status}")
    problems = list(dict.fromkeys(problems))
    if problems:
        print("\nFAIL:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("\nOK: every test file references a placebo/benchmark (or is allowlisted).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
