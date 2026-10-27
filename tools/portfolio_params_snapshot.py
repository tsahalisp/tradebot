#!/usr/bin/env python3
"""Parameter-drift guard for the live paper portfolios (read-only).

Rule it enforces: never change a live paper strategy's logic or parameters on your
own -- propose it. The live parameters are module-level constants in
backend/autoportfolio.py (N, MIN_CONV, FEE_BPS, REBAL_DAYS, START_CASH, MEGACAP_CORE)
and backend/conviction.py (WEIGHTS), plus the rebalance/universe functions. Source
files are parsed with `ast` and never imported or executed (no network, no DB).

    python tools/portfolio_params_snapshot.py            # check vs snapshot, exit 1 on drift
    python tools/portfolio_params_snapshot.py --update   # (re)write snapshot after an approved change

Exit codes: 0 = no drift, 1 = drift / new / removed portfolio / missing or unreadable
config, 2 = usage error. Nothing is ever skipped silently.
"""
import argparse
import ast
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent
DEFAULT_SNAPSHOT = HERE / "params_snapshot.json"

_AP = "backend/autoportfolio.py"
_CV = "backend/conviction.py"
_SHARED = {
    _AP: {"constants": ["N", "MIN_CONV", "FEE_BPS", "REBAL_DAYS", "START_CASH"],
          "functions": ["rebalance", "universe_for", "_congress_buys"]},
    _CV: {"constants": ["WEIGHTS"], "functions": []},
}
# portfolio -> {"universe_rule": str, "files": {relpath: {"constants": [...], "functions": [...]}}}
MANIFEST = {
    "core": {"universe_rule": "megacap+congress",
             "files": {**_SHARED, _AP: {**_SHARED[_AP],
                       "constants": _SHARED[_AP]["constants"] + ["MEGACAP_CORE"]}}},
    "congress": {"universe_rule": "congress-only", "files": _SHARED},
}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=repr)


def params_hash(fields):
    """Deterministic sha256 of a fields dict; independent of key order."""
    return hashlib.sha256(canonical(fields).encode()).hexdigest()


def _extract_file(path, spec):
    """Return {field: value} for one source file; raises on missing/unreadable/invalid."""
    tree = ast.parse(pathlib.Path(path).read_text(), filename=str(path))
    consts, funcs = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            consts[node.targets[0].id] = node.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
    out = {}
    for name in spec.get("constants", []):
        if name not in consts:
            raise KeyError(f"constant {name} not found")
        out[name] = ast.literal_eval(consts[name])  # ValueError if no longer a literal
    for name in spec.get("functions", []):
        if name not in funcs:
            raise KeyError(f"function {name} not found")
        # AST dump: ignores comments/whitespace, changes on any logic change
        out[f"fn:{name}"] = hashlib.sha256(ast.dump(funcs[name]).encode()).hexdigest()[:16]
    return out


def collect(root, manifest=None):
    """-> {portfolio: {"fields": {...}, "hash": str}} and {portfolio: [error, ...]}."""
    manifest = MANIFEST if manifest is None else manifest
    root = pathlib.Path(root)
    result, errors = {}, {}
    for pf, spec in manifest.items():
        fields = {"universe_rule": spec.get("universe_rule")}
        errs = []
        for rel, fspec in spec["files"].items():
            try:
                for k, v in _extract_file(root / rel, fspec).items():
                    fields[f"{rel}:{k}"] = v
            except Exception as e:  # missing file, syntax error, absent/non-literal param
                errs.append(f"{rel}: {type(e).__name__}: {e}")
        if errs:
            errors[pf] = errs
        else:
            result[pf] = {"fields": fields, "hash": params_hash(fields)}
    return result, errors


def compare(current, errors, snapshot):
    """Return (rows, problems). rows: (portfolio, status, hash, detail)."""
    rows, problems = [], []
    for pf in sorted(set(current) | set(errors) | set(snapshot)):
        if pf in errors:
            for e in errors[pf]:
                problems.append(f"{pf}: CONFIG ERROR {e}")
            rows.append((pf, "ERROR", "-", "; ".join(errors[pf])))
        elif pf not in snapshot:
            problems.append(f"{pf}: NEW portfolio not in snapshot (propose, then --update)")
            rows.append((pf, "NEW", current[pf]["hash"][:12], "not in snapshot"))
        elif pf not in current:
            problems.append(f"{pf}: REMOVED portfolio still in snapshot")
            rows.append((pf, "REMOVED", "-", "in snapshot only"))
        else:
            old, new = snapshot[pf]["fields"], current[pf]["fields"]
            changed = [f"{k}: {old.get(k, '<absent>')!r} -> {new.get(k, '<absent>')!r}"
                       for k in sorted(set(old) | set(new)) if old.get(k, "<absent>") != new.get(k, "<absent>")]
            if changed or snapshot[pf].get("hash") != current[pf]["hash"]:
                for c in changed or ["hash mismatch (snapshot edited?)"]:
                    problems.append(f"{pf}: DRIFT field {c}")
                rows.append((pf, "DRIFT", current[pf]["hash"][:12], f"{len(changed)} field(s)"))
            else:
                rows.append((pf, "OK", current[pf]["hash"][:12], ""))
    return rows, problems


def format_table(rows):
    head = ("PORTFOLIO", "STATUS", "HASH", "DETAIL")
    w = [max(len(str(r[i])) for r in [head] + rows) for i in range(4)]
    return "\n".join("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in [head] + rows)


def load_snapshot(path):
    return json.loads(pathlib.Path(path).read_text())


def main(argv=None, manifest=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="repo root holding the source files")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    ap.add_argument("--update", action="store_true", help="write snapshot from current state")
    a = ap.parse_args(argv)
    current, errors = collect(a.root, manifest)
    if a.update:
        if errors:
            for pf, es in errors.items():
                for e in es:
                    print(f"{pf}: CONFIG ERROR {e}", file=sys.stderr)
            print("refusing to write snapshot: unreadable/missing config", file=sys.stderr)
            return 1
        pathlib.Path(a.snapshot).write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(format_table([(pf, "SNAPSHOT", d["hash"][:12], "written") for pf, d in sorted(current.items())]))
        return 0
    try:
        snapshot = load_snapshot(a.snapshot)
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot is not a JSON object")
    except Exception as e:
        print(f"SNAPSHOT ERROR: cannot read {a.snapshot}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    rows, problems = compare(current, errors, snapshot)
    print(format_table(rows))
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"FAIL: {len(problems)} problem(s) -- live paper params must not change without a proposal",
              file=sys.stderr)
        return 1
    print("OK: no parameter drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
