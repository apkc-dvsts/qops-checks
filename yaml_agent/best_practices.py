#!/usr/bin/env python3
"""
best_practices.py

--------------------------------------------------------------------------------
DISCLAIMER:
  – Every “check” previously in this file has now been split into its own module
    under best_practices_checks/.  Each module exports:
       · weight (an integer)
       · run(...)  (the actual check function)

  – At runtime, best_practices.py will dynamically import all .py files in
    best_practices_checks/, gather (weight, run) pairs, sort by weight descending,
    and then call each run() in turn.

  – This design makes it easy to add or remove checks by dropping modules into
    best_practices_checks/ without touching this file again.
--------------------------------------------------------------------------------

Usage:
    # For repository (YAML) checks:
    python3 best_practices.py <repo_path>

    # For QVS script checks, using -s or --script:
    python3 best_practices.py -s <script_path> [<out_dir>]

    – If you pass a path ending in “.qvs” without flags, it also treats it as script.
    – If no <out_dir> is provided for script checks, defaults to current directory.
    – For repository mode, only <repo_path> is required; out_dir is ignored.
"""

import os
import sys
import importlib.util
from typing import List, Any, Dict

# ------------------------------------------------------------------------
# ADJUST PYTHONPATH SO check modules can import yaml_agent.models
# ------------------------------------------------------------------------
# Insert the parent directory of this file (i.e., the directory containing 'yaml_agent')
# so that 'import yaml_agent.models' works correctly when checks import from yaml_agent.
parent_dir = os.path.dirname(os.path.dirname(__file__))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# ------------------------------------------------------------------------
# PARAMETER PARSING
# ------------------------------------------------------------------------
args = sys.argv[1:]

if not args:
    print(__doc__)
    sys.exit(1)

is_script = False
target_path = ""
out_dir = ""

if args[0] in ("-s", "--script"):
    if len(args) < 2:
        print("Error: Missing script path after '-s'.\n")
        print(__doc__)
        sys.exit(1)
    is_script = True
    target_path = args[1]
    out_dir = args[2] if len(args) >= 3 else os.getcwd()
elif len(args) == 1:
    target_path = args[0]
    if target_path.lower().endswith(".qvs"):
        is_script = True
        out_dir = os.getcwd()
    else:
        is_script = False
        out_dir = ""
else:
    target_path = args[0]
    if target_path.lower().endswith(".qvs"):
        is_script = True
        out_dir = args[1]
    else:
        is_script = False
        out_dir = ""

if not os.path.exists(target_path):
    print(f"Error: Path '{target_path}' does not exist.")
    sys.exit(1)

# ------------------------------------------------------------------------
# DYNAMIC DISCOVERY OF “CHECK” MODULES
# ------------------------------------------------------------------------
CHECKS_DIR = os.path.join(os.path.dirname(__file__), "best_practices_checks")

def discover_check_modules(checks_dir: str) -> List[Dict[str, Any]]:
    """
    Scan checks_dir for every .py file (excluding __init__.py).  For each,
    dynamically import it, read its `weight` and `run` attributes, and store
    a dict { 'weight': <int>, 'run': <callable>, 'name': <module_name> }.
    """
    check_modules = []
    for fname in os.listdir(checks_dir):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        fullpath = os.path.join(checks_dir, fname)
        module_name = f"best_practices_checks.{fname[:-3]}"

        spec = importlib.util.spec_from_file_location(module_name, fullpath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if hasattr(mod, "weight") and hasattr(mod, "run"):
            check_modules.append({
                "weight": getattr(mod, "weight"),
                "run": getattr(mod, "run"),
                "name": module_name,
            })
        else:
            print(f"WARNING: Module {module_name} missing `weight` or `run`. Skipping.")
    check_modules.sort(key=lambda x: x["weight"], reverse=True)
    return check_modules

all_checks = discover_check_modules(CHECKS_DIR)

# ------------------------------------------------------------------------
# RUNNING THE CHECKS
# ------------------------------------------------------------------------
def run_all_checks(target: str, checks: List[Dict[str, Any]], is_script: bool) -> List[Dict]:
    """
    Invoke each check’s run() on `target`.  If is_script=True, run only
    script‐related checks; otherwise, run only repo‐related checks.

    Script checks are those whose module name contains any of:
      "select_star", "missing_semicolon", "hardcoded_date",
      "uppercase_keywords", "static_qvd_path", "subs_qvd_usage"

    Repo checks are those whose module name contains:
      "nested_if_master_measure", "variable_placeholder"
    """
    all_warnings = []

    for chk in checks:
        mod_name = chk["name"].split(".")[-1]
        try:
            if is_script:
                if any(substr in mod_name for substr in (
                        "select_star",
                        "missing_semicolon",
                        "hardcoded_date",
                        "uppercase_keywords",
                        "static_qvd_path",
                        "subs_qvd_usage"
                    )):
                    warnings = chk["run"](target)
                    all_warnings.extend(warnings)
            else:
                if any(substr in mod_name for substr in (
                        "nested_if_master_measure",
                        "variable_placeholder"
                    )):
                    warnings = chk["run"](target)
                    all_warnings.extend(warnings)
        except Exception as e:
            print(f"WARNING: check '{mod_name}' failed: {e}")
            continue

    return all_warnings

warnings = run_all_checks(target_path, all_checks, is_script)

# ------------------------------------------------------------------------
# IF WE'RE IN SCRIPT MODE, DUMP TO YAML
# ------------------------------------------------------------------------
if is_script:
    try:
        import yaml
    except ImportError:
        print("PyYAML is required to dump script_lint.yaml. Please install it.")
        sys.exit(1)

    if warnings:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "script_lint.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"script_warnings": warnings}, f, sort_keys=False)
        print(f"[script_lint.yaml written to {out_dir}] ({len(warnings)} warning(s))")
        for w in warnings:
            line_info = w.get("line", "N/A")
            issue = w.get("issue", "")
            stmt = w.get("statement", "").split("\n")[0]
            print(f"Line {line_info:>4}: {issue}")
            print(f"  → {stmt}")
        print()
    else:
        print("No script-lint warnings found.\n")
else:
    # REPO MODE: print YAML/repo‐based warnings
    if warnings:
        print(f"{len(warnings)} YAML/repo‐based warning(s) found:\n")
        for w in warnings:
            file_info = w.get("file", "<unknown>")
            issue = w.get("issue", "")
            expr = w.get("expression", "")
            print(f"{file_info} → {issue}")
            print(f"    {expr}\n")
    else:
        print("No repository‐based warnings found.\n")

if __name__ == "__main__":
    pass
