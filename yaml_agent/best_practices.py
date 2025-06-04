#!/usr/bin/env python3
"""
best_practices.py

Scan a Qlik Sense load script (QVS) for common best-practice violations by dynamically
detecting SUB definitions and classifying them into:
  - Mapping SUBs (perform MAPPING LOAD)
  - Verify SUBs (perform QvdNoOfFields checks and optimized LOAD)
  - Aggregation SUBs (perform re-aggregation with Store)

Also includes:
  - YAML-based “master measure” and “variable” checks (requires yaml_agent.models.Repository)
  - A more general script-linter enforcing:
      • SELECT * warnings
      • Missing semicolons on DML (multiline-aware) → logs the full statement
      • Hardcoded dates in LET
      • Nested IF depth within data-load statements (suggest refactoring) → logs specific statement
      • LOAD … FROM qvd statements outside SUB LoadVerifyQVD
"""

import os
import re
import yaml
from typing import Dict, List, Optional, Set

try:
    from yaml_agent.models import Repository
except ImportError:
    Repository = None  # YAML-based checks are disabled if yaml_agent isn't installed

#
# Part 1: YAML-based “master measure” and “variable” checks
#

def check_master_measures_and_variables(repo_root: str) -> List[Dict]:
    """
    Check for missing or undefined master measures/variables in YAML-based Qlik repository.
    Returns a list of warning dicts.
    """
    warnings: List[Dict] = []
    if Repository is None:
        return warnings

    repo = Repository(repo_root)
    for obj in repo.get_all_objects():
        if obj.node_type == "YAML_MasterMeasure":
            # Check if referenced measures exist
            for expr in obj.expressions:
                # Count nested IF depth in master-measure expressions (reuse existing logic)
                depth = max(
                    (len(x) for x in re.findall(r"(IF\s*\()", expr, flags=re.IGNORECASE)),
                    default=0
                )
                if depth > 2:
                    warnings.append({
                        "obj_id": obj.obj_id,
                        "type": obj.node_type,
                        "issue": f"Nested IF depth={depth}",
                        "expression": expr,
                    })
        elif obj.node_type == "YAML_Variable":
            # Example placeholder for additional variable checks
            pass

    return warnings


#
# Part 2: Inline-SUB & QVD usage checks (printed to stdout)
#

def check_subs_and_qvd_usage(script_path: str) -> List[Dict]:
    """
    Scan a QVS script for:
      - Inlined SUB definitions (mapping, verify, aggregation)
      - QVD usage patterns
    Returns a list of warning dicts.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    total_lines = len(lines)

    # 1) Detect SUB LoadVerifyQVD definition and its block range
    sub_start_idx: Optional[int] = None
    sub_end_idx: Optional[int] = None
    for idx, raw in enumerate(lines):
        if re.match(r"^\s*SUB\s+LoadVerifyQVD\b", raw, flags=re.IGNORECASE):
            sub_start_idx = idx
            break
    if sub_start_idx is not None:
        for j in range(sub_start_idx + 1, total_lines):
            if re.match(r"^\s*END\s+SUB\b", lines[j], flags=re.IGNORECASE):
                sub_end_idx = j
                break

    # 2) Classify SUBs (mapping, verify, aggregation)
    sub_pattern = re.compile(r"^\s*SUB\s+(\w+)\b", flags=re.IGNORECASE)
    end_sub_pattern = re.compile(r"^\s*END\s+SUB\b", flags=re.IGNORECASE)
    current_sub: Optional[str] = None
    current_body: List[str] = []
    sub_bodies: Dict[str, List[str]] = {}

    for idx, raw in enumerate(lines):
        if re.match(end_sub_pattern, raw):
            if current_sub:
                sub_bodies[current_sub] = current_body.copy()
            current_sub = None
            current_body = []
        elif current_sub:
            current_body.append(raw)
        else:
            m = sub_pattern.match(raw)
            if m:
                current_sub = m.group(1)
                current_body = []

    mapping_subs: Set[str] = set()
    verify_subs: Set[str] = set()
    agg_subs: Set[str] = set()

    for sub_name, body in sub_bodies.items():
        sub_body = "".join(body)
        if re.search(r"\bMAPPING\s+LOAD\b", sub_body, flags=re.IGNORECASE):
            mapping_subs.add(sub_name)
        if re.search(r"\bQvdNoOfFields\b", sub_body, flags=re.IGNORECASE) and re.search(r"\bLOAD\b.*\bFROM\b", sub_body, flags=re.IGNORECASE):
            verify_subs.add(sub_name)
        if re.search(r"\bSTORE\b.*\bINTO\b.*\.qvd", sub_body, flags=re.IGNORECASE):
            agg_subs.add(sub_name)

    # 3) Warn if any QVD loads appear outside of SUB LoadVerifyQVD
    for idx, raw in enumerate(lines):
        if re.match(r"^\s*LOAD\b.*\bFROM\b.*\.qvd", raw, flags=re.IGNORECASE):
            if sub_start_idx is not None:
                if not (sub_start_idx <= idx <= (sub_end_idx or sub_start_idx)):
                    warnings.append({
                        "line": idx + 1,
                        "issue": "LOAD … FROM … qvd appears outside LoadVerifyQVD. All QVD loads should be wrapped by LoadVerifyQVD.",
                        "statement": raw.rstrip(),
                    })

    return warnings


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Best-practice scanner for Qlik Sense load scripts and YAML-based repository."
    )
    parser.add_argument(
        "--script", "-s", required=True,
        help="Path to the QVS load script to lint."
    )
    parser.add_argument(
        "--out", "-o", default=".",
        help="Directory where output YAML files (script_lint.yaml, best_practices.yaml) will be written."
    )
    parser.add_argument(
        "--repo", "-r", default=None,
        help="(Optional) Path to your YAML-based Qlik repository root for master-measure/variable checks."
    )
    args = parser.parse_args()

    script_path = args.script
    out_dir = args.out
    repo_path = args.repo

    # Part 1: YAML-based checks
    yaml_warnings: List[Dict] = []
    if repo_path:
        yaml_warnings = check_master_measures_and_variables(repo_path)
        if yaml_warnings:
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, "best_practices.yaml")
            with open(out_path, "w", encoding="utf-8") as f:
                yaml.dump({"warnings": yaml_warnings}, f, sort_keys=False)
            print(f"[best_practices.yaml written to {out_dir}] ({len(yaml_warnings)} warning(s))\n")
        else:
            print("No YAML-based best-practice warnings found.\n")

    # Part 2: Inline-SUB & QVD usage checks
    sub_warnings = check_subs_and_qvd_usage(script_path)
    if sub_warnings:
        print(f"[sub_qvd_warnings printed below] ({len(sub_warnings)} warning(s))")
        for w in sub_warnings:
            print(f"Line {w['line']:>4}: {w['issue']}")
            print(f"  → {w['statement'].splitlines()[0]}")
        print()
    else:
        print("No SUB/QVD usage warnings found.\n")

    # Part 3: General Script Linter (with multiline-aware semicolon check 
    #           and “LOAD … FROM qvd” only outside SUB LoadVerifyQVD)
    script_warnings = run_script_linter(script_path, out_dir)
    if script_warnings:
        print(f"[script_lint.yaml written to {out_dir}] ({len(script_warnings)} warning(s))")
        for w in script_warnings:
            print(f"Line {w['line']:>4}: {w['issue']}")
            stmt = w.get("statement", "").split("\n")[0]
            print(f"  → {stmt}")
        print()
    else:
        print("No script-lint warnings found.\n")


#
# Part 3: General Script Linter (with multiline-aware semicolon check 
#           and “LOAD … FROM qvd” only outside SUB LoadVerifyQVD)
#

def run_script_linter(script_path: str, out_dir: str) -> List[Dict]:
    """
    Enhanced QVS script linter that enforces:
      - All FROM clauses must use lib:// or a variable
      - If SUB LoadVerifyQVD is defined, then no standalone LOAD … FROM … should exist outside that SUB
      - Warn on SELECT * usage
      - Warn on missing semicolons on DML statements (multiline-aware) → logs offending statement
      - Warn on hardcoded dates in LET
      - Warn on nested IF depth only within data-load statements (suggest refactoring) → logs specific statement
      - Enforce uppercase Qlik keywords (LOAD, SELECT, etc.)

    Returns a list of warning dicts, writes them to script_lint.yaml under out_dir.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    total_lines = len(lines)

    # 1) Detect SUB LoadVerifyQVD definition and its block range
    sub_start_idx: Optional[int] = None
    sub_end_idx: Optional[int] = None
    for idx, raw in enumerate(lines):
        if re.match(r"^\s*SUB\s+LoadVerifyQVD\b", raw, flags=re.IGNORECASE):
            sub_start_idx = idx
            break
    if sub_start_idx is not None:
        for j in range(sub_start_idx + 1, total_lines):
            if re.match(r"^\s*END\s+SUB\b", lines[j], flags=re.IGNORECASE):
                sub_end_idx = j
                break

    # 2) Patterns
    select_star_pattern = re.compile(r"SELECT\s+\*", flags=re.IGNORECASE)
    dml_start_pattern = re.compile(r"^\s*(SELECT|LOAD|STORE|INSERT|DELETE|JOIN)\b", flags=re.IGNORECASE)
    load_select_pattern = re.compile(r"^\s*(LOAD|SELECT)\b", flags=re.IGNORECASE)
    hardcoded_date_pattern = re.compile(r"^\s*LET\s+\w+\s*=\s*\d{4}", flags=re.IGNORECASE)
    lowercase_keyword_pattern = re.compile(r"\b(load|select|join|where|set|let|store|insert|delete|qualify)\b")

    # State for tracking data-load context
    in_load_context = False
    nested_if_depth = 0
    max_nested_if_depth = 0
    max_nested_if_line = 0
    max_nested_if_statement = ""

    idx = 0
    while idx < total_lines:
        raw = lines[idx]
        lineno = idx + 1
        line = raw.rstrip()  # preserve whitespace to show full statement in log

        # Determine if this is a comment line (for suppressing only that check, not for counting)
        is_comment = line.lstrip().startswith("//") or line.lstrip().startswith("/*")

        # 3) SELECT * usage
        if select_star_pattern.search(line):
            warnings.append({
                "line": lineno,
                "issue": "Use of SELECT * in script (list fields explicitly)",
                "statement": line
            })

        # 4) Hardcoded date in LET
        if hardcoded_date_pattern.match(line):
            warnings.append({
                "line": lineno,
                "issue": "Hardcoded year/date in LET (compute dynamically, e.g. Year(Today()) - X)",
                "statement": line
            })

        # 5) Uppercase enforcement for keywords (skip only within comment lines)
        if not is_comment and lowercase_keyword_pattern.search(line):
            warnings.append({
                "line": lineno,
                "issue": "Keyword should be uppercase (e.g. LOAD, SELECT, WHERE, JOIN, SET, LET, QUALIFY)",
                "statement": line
            })

        # 6) FROM clause path check: must begin with lib:// or be a variable
        m = re.search(r"\bFROM\s+'([^']+)'", line, flags=re.IGNORECASE)
        if m:
            path = m.group(1).strip()
            if not path.lower().startswith("lib://") and not re.match(r"\$\([A-Za-z0-9_]+\)", path):
                warnings.append({
                    "line": lineno,
                    "issue": f"Hardcoded FROM path '{path}'. Use lib:// or a variable.",
                    "statement": line
                })

        # 7) Detect “LOAD … FROM … qvd” and warn if outside SUB LoadVerifyQVD
        if re.match(r"^\s*LOAD\b.*\bFROM\b.*\.qvd", line, flags=re.IGNORECASE):
            if sub_start_idx is not None:
                if not (sub_start_idx <= idx <= (sub_end_idx or sub_start_idx)):
                    warnings.append({
                        "line": lineno,
                        "issue": "LOAD … FROM … qvd appears outside LoadVerifyQVD. All QVD loads should be wrapped by LoadVerifyQVD.",
                        "statement": line
                    })

        # 8) Track nested IF depth only within data-load blocks
        if not in_load_context and load_select_pattern.match(line):
            # Entering a data-load context if statement does not end in semicolon
            if not line.rstrip().endswith(";"):
                in_load_context = True
                nested_if_depth = 0
                max_nested_if_depth = 0
                max_nested_if_line = 0
                max_nested_if_statement = ""
        if in_load_context:
            if re.match(r"^\s*IF\b.*\bTHEN\b", line, flags=re.IGNORECASE):
                nested_if_depth += 1
                if nested_if_depth > max_nested_if_depth:
                    max_nested_if_depth = nested_if_depth
                    max_nested_if_line = lineno
                    max_nested_if_statement = line
            elif re.match(r"^\s*END\s+IF\b", line, flags=re.IGNORECASE):
                nested_if_depth = max(nested_if_depth - 1, 0)

            # Check end of data-load when semicolon appears
            if line.rstrip().endswith(";"):
                # At end of this data-load block, evaluate nested depth
                if max_nested_if_depth > 2:
                    warnings.append({
                        "line": max_nested_if_line,
                        "issue": f"Detected nested IF depth={max_nested_if_depth} within data-load; consider refactoring to reduce complexity.",
                        "statement": max_nested_if_statement
                    })
                in_load_context = False

        # 9) DML statements (LOAD/SELECT/STORE/INSERT/DELETE/JOIN). Check for trailing semicolon—
        #    but allow multiline up to the line that actually ends in “;”. Warn only if no “;” appears
        #    before the next DML start or EOF. Log the full statement (concatenated snippet).
        if dml_start_pattern.match(line):
            if not line.rstrip().endswith(";"):
                snippet_lines = [line]
                found_semicolon = False
                j = idx + 1
                while j < total_lines:
                    next_raw = lines[j].rstrip()
                    snippet_lines.append(next_raw)
                    if next_raw.rstrip().endswith(";"):
                        found_semicolon = True
                        break
                    if dml_start_pattern.match(next_raw):
                        break
                    j += 1

                if not found_semicolon:
                    full_stmt = "\n".join(snippet_lines)
                    warnings.append({
                        "line": lineno,
                        "issue": "Statement likely missing trailing semicolon",
                        "statement": full_stmt
                    })
                # Do not skip ahead; continue scanning each line for accurate line numbers
        idx += 1

    # 10) Write YAML if any warnings
    if warnings:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "script_lint.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"script_warnings": warnings}, f, sort_keys=False)

    return warnings


if __name__ == "__main__":
    main()
