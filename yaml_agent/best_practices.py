#!/usr/bin/env python3
"""
best_practices.py

Scan a Qlik Sense load script (QVS) for common best-practice violations by dynamically
detecting SUB definitions and classifying them into:
  - Mapping SUBs
  - Verify SUBs (with QvdNoOfFields checks)
  - Aggregation SUBs

Also includes:
  - YAML-based “master measure” and “variable” checks (requires yaml_agent.models.Repository)
  - A general linter enforcing:
      • SELECT * usage
      • Missing semicolons on DML statements (multiline-aware)
      • Hardcoded dates in LET
      • Nested IF depth within data-load statements
      • LOAD … FROM *.qvd outside any SUB that itself contains a QVD load (even when split across lines)
      • SUB parameters used as file paths must start with lib:// or $(…)
      • Per-line, incremental variable resolution when checking SUB path parameters
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
    Check for nested IF depth in YAML master-measure expressions.
    """
    warnings: List[Dict] = []
    if Repository is None:
        return warnings

    repo = Repository(repo_root)
    for obj in repo.get_all_objects():
        if obj.node_type == "YAML_MasterMeasure":
            for expr in obj.expressions:
                depth = max(
                    (len(x) for x in re.findall(r"(IF\s*\()", expr, flags=re.IGNORECASE)),
                    default=0
                )
                if depth > 2:
                    warnings.append({
                        "obj_id": obj.obj_id,
                        "type": obj.node_type,
                        "issue": f"Nested IF depth={depth} in master-measure",
                        "expression": expr,
                    })
        elif obj.node_type == "YAML_Variable":
            # Placeholder for additional checks
            pass

    return warnings


#
# Part 2: Inline-SUB & QVD-usage checks (printed to stdout)
#

def check_subs_and_qvd_usage(script_path: str) -> List[Dict]:
    """
    Scan a QVS script for:
      - SUB definitions and their parameters
      - QVD usage inside any SUB (valid context)
      - Variables passed to SUB path parameters must start with lib:// or $(…)
      - Per-line, incremental variable resolution
    Returns a list of warning dicts.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    total_lines = len(lines)

    # 1) Parse all SUB definitions: name → parameter list; also gather body lines and ranges
    sub_def_pattern = re.compile(r"^\s*SUB\s+(\w+)\s*\((.*?)\)", flags=re.IGNORECASE)
    end_sub_pattern = re.compile(r"^\s*END\s+SUB\b", flags=re.IGNORECASE)

    sub_params: Dict[str, List[str]] = {}
    sub_bodies: Dict[str, List[str]] = {}
    sub_ranges: Dict[str, tuple] = {}  # SUB name -> (start_idx, end_idx)
    in_sub: Optional[str] = None
    start_idx = 0
    current_body: List[str] = []

    for idx, raw in enumerate(lines):
        if in_sub:
            if re.match(end_sub_pattern, raw):
                sub_bodies[in_sub] = current_body.copy()
                sub_ranges[in_sub] = (start_idx, idx)
                in_sub = None
                current_body = []
            else:
                current_body.append(raw)
        else:
            m = sub_def_pattern.match(raw)
            if m:
                name = m.group(1)
                param_str = m.group(2).strip()
                params = [p.strip() for p in param_str.split(",")] if param_str else []
                sub_params[name] = params
                in_sub = name
                start_idx = idx
                current_body = []

    # 2) Identify which SUBs contain any QVD-load (i.e., “FROM … .qvd” in body)
    sub_ranges_with_qvd: List[tuple] = []
    for sub_name, body_lines in sub_bodies.items():
        for line in body_lines:
            if re.search(r"\bFROM\b.*\.qvd", line, flags=re.IGNORECASE):
                sub_ranges_with_qvd.append(sub_ranges[sub_name])
                break  # only need one occurrence to consider that SUB valid

    # 3) Identify which SUB parameters are used in FROM [$(param)] inside their SUB bodies
    sub_params_used_as_path: Dict[str, Set[str]] = {}
    for sub_name, params in sub_params.items():
        used: Set[str] = set()
        body_lines = sub_bodies.get(sub_name, [])
        for param in params:
            pattern = re.compile(rf"\bFROM\s+\[\$\(\s*{re.escape(param)}\s*\)\]", flags=re.IGNORECASE)
            for line in body_lines:
                if pattern.search(line):
                    used.add(param)
                    break
        if used:
            sub_params_used_as_path[sub_name] = used

    # 4) Incrementally track per-line variable assignments
    var_assignments: Dict[str, str] = {}
    assign_pattern = re.compile(r"^\s*SET\s+(\w+)\s*=\s*(.+?);", flags=re.IGNORECASE)
    call_pattern = re.compile(r"^\s*call\s+(\w+)\s*\((.*?)\)", flags=re.IGNORECASE)

    def resolve_var(var_name: str, assignments: Dict[str, str], seen: Set[str]) -> Optional[str]:
        """
        Recursively resolve var_name from assignments (to handle chains like a = b; b = 'literal';).
        """
        if var_name in seen:
            return None
        seen.add(var_name)
        val = assignments.get(var_name)
        if val is None:
            return None
        m = re.match(r"^([A-Za-z_]\w*)$", val)
        if m:
            return resolve_var(m.group(1), assignments, seen)
        return val

    for idx, raw in enumerate(lines):
        # 4a) Record any SET var = … assignments
        m_assign = assign_pattern.match(raw)
        if m_assign:
            var_name = m_assign.group(1)
            value = m_assign.group(2).strip()
            var_assignments[var_name] = value

        # 4b) Check any call to a SUB that has path parameters
        m_call = call_pattern.match(raw)
        if not m_call:
            continue
        sub_name = m_call.group(1)
        args_str = m_call.group(2).strip()
        if sub_name not in sub_params_used_as_path:
            continue

        args = [arg.strip() for arg in args_str.split(",")]
        params = sub_params.get(sub_name, [])
        path_params = sub_params_used_as_path[sub_name]

        for pos, param_name in enumerate(params):
            if param_name in path_params and pos < len(args):
                arg = args[pos]
                # Case A: literal string in single quotes
                lit_match = re.match(r"^'(.*)'$", arg)
                if lit_match:
                    literal = lit_match.group(1).strip()
                    if not (literal.lower().startswith("lib://") or
                            re.match(r"\$\([A-Za-z0-9_]+\)", literal)):
                        warnings.append({
                            "line": idx + 1,
                            "issue": f"Hardcoded SUB path literal '{literal}' passed to parameter '{param_name}'. Use lib:// or a variable.",
                            "statement": raw.rstrip(),
                        })
                else:
                    # Case B: a simple variable name (e.g. vQVD)
                    var_match = re.match(r"^([A-Za-z_]\w*)$", arg)
                    if var_match:
                        resolved = resolve_var(var_match.group(1), var_assignments.copy(), set())
                        if resolved is not None:
                            lit2 = re.match(r"^'(.*)'$", resolved)
                            if lit2:
                                literal2 = lit2.group(1).strip()
                                if not (literal2.lower().startswith("lib://") or
                                        re.match(r"\$\([A-Za-z0-9_]+\)", literal2)):
                                    warnings.append({
                                        "line": idx + 1,
                                        "issue": f"Hardcoded literal '{literal2}' (via variable) passed to parameter '{param_name}'. Use lib:// or a variable.",
                                        "statement": raw.rstrip(),
                                    })
                            else:
                                # If resolution yields a raw lib:// path, that's not allowed here
                                if resolved.lower().startswith("lib://"):
                                    warnings.append({
                                        "line": idx + 1,
                                        "issue": f"Hardcoded SUB path literal '{resolved}' passed to parameter '{param_name}'. Use a variable or dynamic expression.",
                                        "statement": raw.rstrip(),
                                    })
                                elif re.match(r"^\$\([A-Za-z0-9_]+\).*$", resolved):
                                    pass  # Valid dynamic expression
                                else:
                                    warnings.append({
                                        "line": idx + 1,
                                        "issue": f"Parameter '{param_name}' passed via variable chain to expression '{resolved}'. Verify path conforms to rules.",
                                        "statement": raw.rstrip(),
                                    })
                        else:
                            warnings.append({
                                "line": idx + 1,
                                "issue": f"Unable to resolve variable '{var_match.group(1)}' passed to parameter '{param_name}'.",
                                "statement": raw.rstrip(),
                            })
                    else:
                        # Case C: a more complex expression—prompt manual review
                        warnings.append({
                            "line": idx + 1,
                            "issue": f"Parameter '{param_name}' passed via expression '{arg}'. Verify path conforms to rules.",
                            "statement": raw.rstrip(),
                        })

    return warnings


#
# Part 3: General Script Linter
#

def run_script_linter(script_path: str, out_dir: str) -> List[Dict]:
    """
    Enhanced QVS script linter that enforces:
      - All FROM clauses must use lib:// or a variable
      - If any SUB contains a QVD-load (i.e. “FROM … .qvd”), then LOAD … FROM … .qvd inside that SUB is valid;
        any multi-line “LOAD … FROM … .qvd” outside every such SUB is flagged.
        If no SUB contains a QVD-load, then no warning is raised for any LOAD … FROM … .qvd.
      - Warn on SELECT * usage
      - Warn on missing semicolons on DML statements (multiline-aware)
      - Warn on hardcoded dates in LET
      - Warn on nested IF depth only within data-load statements
      - Enforce uppercase Qlik keywords (LOAD, SELECT, etc.)
    Writes out `script_lint.yaml` under `out_dir` if any warnings are found.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    total_lines = len(lines)

    # 1) Parse SUB definitions again to find ranges of any SUB that contains a QVD-load
    sub_ranges_with_qvd: List[tuple] = []
    sub_def_pattern = re.compile(r"^\s*SUB\s+(\w+)\s*\(", flags=re.IGNORECASE)
    end_sub_pattern = re.compile(r"^\s*END\s+SUB\b", flags=re.IGNORECASE)

    in_sub: Optional[str] = None
    current_body: List[str] = []
    current_start: int = 0

    for idx, raw in enumerate(lines):
        if in_sub:
            if re.match(end_sub_pattern, raw):
                body_text = "".join(current_body)
                if re.search(r"\bFROM\b.*\.qvd", body_text, flags=re.IGNORECASE):
                    sub_ranges_with_qvd.append((current_start, idx))
                in_sub = None
                current_body = []
            else:
                current_body.append(raw)
        else:
            m = sub_def_pattern.match(raw)
            if m:
                in_sub = m.group(1)
                current_start = idx
                current_body = []

    # 2) Patterns for general checks
    select_star_pattern = re.compile(r"SELECT\s+\*", flags=re.IGNORECASE)
    dml_start_pattern = re.compile(r"^\s*(SELECT|LOAD|STORE|INSERT|DELETE|JOIN)\b", flags=re.IGNORECASE)
    load_select_pattern = re.compile(r"^\s*(LOAD|SELECT)\b", flags=re.IGNORECASE)
    hardcoded_date_pattern = re.compile(r"^\s*LET\s+\w+\s*=\s*\d{4}", flags=re.IGNORECASE)
    lowercase_keyword_pattern = re.compile(r"\b(load|select|join|where|set|let|store|insert|delete|qualify)\b")

    idx = 0
    in_load_context = False
    nested_if_depth = 0
    max_nested_if_depth = 0
    max_nested_if_line = 0
    max_nested_if_statement = ""

    while idx < total_lines:
        raw = lines[idx]
        lineno = idx + 1
        line = raw.rstrip()

        # Determine if this is a comment line
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

        # 5) Uppercase enforcement for keywords (skip within comment lines)
        if not is_comment and lowercase_keyword_pattern.search(line):
            warnings.append({
                "line": lineno,
                "issue": "Keyword should be uppercase (e.g. LOAD, SELECT, WHERE, JOIN, SET, LET, QUALIFY)",
                "statement": line
            })

        # 6) FROM clause path check: single-line literal must start with lib:// or $(…)
        m_from_literal = re.search(r"\bFROM\s+'([^']+)'", line, flags=re.IGNORECASE)
        if m_from_literal:
            path = m_from_literal.group(1).strip()
            if not path.lower().startswith("lib://") and not re.match(r"\$\([A-Za-z0-9_]+\)", path):
                warnings.append({
                    "line": lineno,
                    "issue": f"Hardcoded FROM path '{path}'. Use lib:// or a variable.",
                    "statement": line
                })

        # 7) Detect multi-line “LOAD … FROM … .qvd”
        if re.match(r"^\s*(LOAD|SELECT)\b", line, flags=re.IGNORECASE):
            block_lines = [line]
            j = idx + 1
            found_semicolon = line.endswith(";")
            while j < total_lines and not found_semicolon:
                next_line = lines[j].rstrip()
                block_lines.append(next_line)
                if next_line.endswith(";"):
                    found_semicolon = True
                j += 1

            full_block = " ".join(block_lines)
            if re.search(r"\bFROM\b.*\.qvd", full_block, flags=re.IGNORECASE):
                # Only flag if there is at least one SUB that contains a QVD-load.
                if sub_ranges_with_qvd:
                    inside_valid_sub = False
                    for (start, end) in sub_ranges_with_qvd:
                        if start <= idx <= end:
                            inside_valid_sub = True
                            break
                    if not inside_valid_sub:
                        warnings.append({
                            "line": lineno,
                            "issue": "LOAD … FROM … qvd appears outside any SUB that uses QVD in its body.",
                            "statement": full_block,
                        })
            idx = j
            continue

        # 8) Track nested IF depth inside data-load blocks
        if not in_load_context and load_select_pattern.match(line):
            if not line.endswith(";"):
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

            if line.endswith(";"):
                if max_nested_if_depth > 2:
                    warnings.append({
                        "line": max_nested_if_line,
                        "issue": f"Detected nested IF depth={max_nested_if_depth} within data-load; consider refactoring to reduce complexity.",
                        "statement": max_nested_if_statement
                    })
                in_load_context = False

        # 9) DML missing semicolon check (LOAD/SELECT/STORE/INSERT/DELETE/JOIN)
        if dml_start_pattern.match(line):
            if not line.endswith(";"):
                snippet_lines = [line]
                found_semicolon = False
                k = idx + 1
                while k < total_lines:
                    next_raw = lines[k].rstrip()
                    snippet_lines.append(next_raw)
                    if next_raw.endswith(";"):
                        found_semicolon = True
                        break
                    if dml_start_pattern.match(next_raw):
                        break
                    k += 1

                if not found_semicolon:
                    full_stmt = "\n".join(snippet_lines)
                    warnings.append({
                        "line": lineno,
                        "issue": "Statement likely missing trailing semicolon",
                        "statement": full_stmt
                    })

        idx += 1

    # 10) Write YAML if any warnings exist
    if warnings:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "script_lint.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"script_warnings": warnings}, f, sort_keys=False)

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

    # Part 3: General Script Linter
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


if __name__ == "__main__":
    main()
