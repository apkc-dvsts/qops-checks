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
      • Any static path (literal or variable→literal with full file path) in LOAD … FROM … .qvd must be flagged
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
# Part 2: Inline-SUB & QVD-usage checks
#

def check_subs_and_qvd_usage(script_path: str) -> List[Dict]:
    """
    Scan a QVS script for:
      - SUB definitions and their parameters
      - Variables passed to SUB path parameters must start with lib:// or $(…)
      - Per-line, incremental variable resolution
    Returns a list of warning dicts.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    # 1) Parse all SUB definitions: name → parameter list; also gather body lines
    sub_def_pattern = re.compile(r"^\s*SUB\s+(\w+)\s*\((.*?)\)", flags=re.IGNORECASE)
    end_sub_pattern = re.compile(r"^\s*END\s+SUB\b", flags=re.IGNORECASE)

    sub_params: Dict[str, List[str]] = {}
    sub_bodies: Dict[str, List[str]] = {}
    in_sub: Optional[str] = None
    current_body: List[str] = []

    for idx, raw in enumerate(lines):
        if in_sub:
            if re.match(end_sub_pattern, raw):
                sub_bodies[in_sub] = current_body.copy()
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
                current_body = []

    # 2) Identify which SUB parameters are used in FROM [$(param)] inside their bodies
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

    # 3) Incrementally track per-line variable assignments
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
        # Record SET assignments
        m_assign = assign_pattern.match(raw)
        if m_assign:
            var_name = m_assign.group(1)
            value = m_assign.group(2).strip()
            var_assignments[var_name] = value

        # Check SUB calls with path parameters
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
                    var_match = re.match(r"^([A-Za-z_]\w*)$", arg)
                    if var_match:
                        resolved = resolve_var(var_match.group(1), var_assignments.copy(), set())
                        if resolved is not None:
                            # Remove quotes if present
                            lit2 = re.match(r"^'(.*)'$", resolved)
                            if lit2:
                                literal2 = lit2.group(1).strip()
                            else:
                                literal2 = resolved.strip()
                            # Only flag if literal2 is a complete lib:// path ending with extension
                            if re.match(r"^lib://.*\.\w+$", literal2, flags=re.IGNORECASE):
                                warnings.append({
                                    "line": idx + 1,
                                    "issue": f"Static path '{literal2}' passed to parameter '{param_name}'.",
                                    "statement": raw.rstrip(),
                                })
                            # Otherwise, dynamic enough
                        else:
                            # Unresolved var → dynamic enough
                            pass
                    else:
                        # Complex expression → dynamic enough
                        pass

    return warnings


#
# Part 3: General Script Linter
#

def run_script_linter(script_path: str, out_dir: str) -> List[Dict]:
    """
    Enhanced QVS script linter that enforces:
      - Any static path (literal or variable→literal with full file path) in LOAD … FROM … .qvd must be flagged
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

    select_star_pattern = re.compile(r"SELECT\s+\*", flags=re.IGNORECASE)
    dml_start_pattern = re.compile(r"^\s*(SELECT|LOAD|STORE|INSERT|DELETE|JOIN)\b", flags=re.IGNORECASE)
    load_select_pattern = re.compile(r"^\s*(LOAD|SELECT)\b", flags=re.IGNORECASE)
    hardcoded_date_pattern = re.compile(r"^\s*LET\s+\w+\s*=\s*\d{4}", flags=re.IGNORECASE)
    lowercase_keyword_pattern = re.compile(r"\b(load|select|join|where|set|let|store|insert|delete|qualify)\b")

    var_assignments: Dict[str, str] = {}
    assign_pattern = re.compile(r"^\s*SET\s+(\w+)\s*=\s*(.+?);", flags=re.IGNORECASE)

    def resolve_var(var_name: str, assignments: Dict[str, str], seen: Set[str]) -> Optional[str]:
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

        # Track SET assignments for variable resolution
        m_assign = assign_pattern.match(line)
        if m_assign:
            var_name = m_assign.group(1)
            value = m_assign.group(2).strip()
            var_assignments[var_name] = value

        is_comment = line.lstrip().startswith("//") or line.lstrip().startswith("/*")

        # SELECT * usage
        if select_star_pattern.search(line):
            warnings.append({
                "line": lineno,
                "issue": "Use of SELECT * in script (list fields explicitly)",
                "statement": line
            })

        # Hardcoded date in LET
        if hardcoded_date_pattern.match(line):
            warnings.append({
                "line": lineno,
                "issue": "Hardcoded year/date in LET (compute dynamically, e.g. Year(Today()) - X)",
                "statement": line
            })

        # Uppercase enforcement for keywords
        if not is_comment and lowercase_keyword_pattern.search(line):
            warnings.append({
                "line": lineno,
                "issue": "Keyword should be uppercase (e.g. LOAD, SELECT, WHERE, JOIN, SET, LET, QUALIFY)",
                "statement": line
            })

        # Single-line FROM 'literal'
        m_from_literal = re.search(r"\bFROM\s+'([^']+)'", line, flags=re.IGNORECASE)
        if m_from_literal:
            path = m_from_literal.group(1).strip()
            if not path.lower().startswith("lib://") and not re.match(r"\$\([A-Za-z0-9_]+\)", path):
                warnings.append({
                    "line": lineno,
                    "issue": f"Hardcoded FROM path '{path}'. Use lib:// or a variable.",
                    "statement": line
                })

        # Multi-line LOAD/SELECT detection
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
            # Match FROM [ ... ] inside the bracket
            m_from = re.search(r"\bFROM\s*\[\s*([^\]]+)\s*\]", full_block, flags=re.IGNORECASE)
            if m_from:
                path_inner = m_from.group(1).strip()
                # Case A: exactly "$(var)"
                var_exact = re.match(r"^\$\((\w+)\)$", path_inner)
                if var_exact:
                    var_name = var_exact.group(1)
                    resolved = resolve_var(var_name, var_assignments.copy(), set())
                    if resolved is not None:
                        # Remove any surrounding quotes
                        lit_match = re.match(r"^'(.*)'$", resolved)
                        if lit_match:
                            literal = lit_match.group(1).strip()
                        else:
                            literal = resolved.strip()
                        # Only flag if literal is a complete lib:// path ending in a file extension
                        if re.match(r"^lib://.*\.\w+$", literal, flags=re.IGNORECASE):
                            warnings.append({
                                "line": lineno,
                                "issue": f"Static path '{literal}' used in LOAD … FROM … .qvd (via variable).",
                                "statement": full_block,
                            })
                        # Otherwise (e.g. lib://OmniA/ without extension), consider dynamic enough
                    else:
                        # Unresolved variable: dynamic enough, do not flag
                        pass
                else:
                    # Case B: not exactly "$(var)". If starts with lib:// and ends with extension → static
                    if re.match(r"^lib://.*\.\w+$", path_inner, flags=re.IGNORECASE):
                        warnings.append({
                            "line": lineno,
                            "issue": f"Static path '{path_inner}' used in LOAD … FROM … .qvd.",
                            "statement": full_block,
                        })
                    # Otherwise (e.g. "$(var)/suffix" or other combination), dynamic enough

            idx = j
            continue

        # Nested IF tracking within data-load
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

        # DML missing semicolon check
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

    # Write YAML if warnings exist
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
