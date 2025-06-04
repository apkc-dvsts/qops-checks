#!/usr/bin/env python3
"""
check_subs_qvd_usage.py

Scans a QVS script for:
  - SUB definitions and their parameters
  - Which SUBs contain QVD loads (i.e., “FROM … .qvd” in their body)
  - Flags any “LOAD … FROM … .qvd” outside those SUB ranges (even if spread over multiple lines)
  - Ensures SUB parameters representing file paths are passed as lib:// or $(…)
  - Performs per-line, incremental variable resolution for SUB path parameters
"""

import os
import re
from typing import List, Dict, Optional, Set

# Module weight for ordering in best-practices checks
weight = 8

def run(script_path: str) -> List[Dict]:
    """
    Entrypoint for the lint framework. Returns a list of warning dicts:
      { "line": int, "issue": str, "statement": str }.
    """
    warnings: List[Dict] = []
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return warnings

    total_lines = len(lines)

    # -------------------------------------------------------------------------
    # 1) Parse all SUB definitions: name → params; also collect body lines and (start, end) indices.
    # -------------------------------------------------------------------------
    sub_def_pattern = re.compile(r"^\s*SUB\s+(\w+)\s*\((.*?)\)", flags=re.IGNORECASE)
    end_sub_pattern = re.compile(r"^\s*END\s+SUB\b", flags=re.IGNORECASE)

    sub_params: Dict[str, List[str]] = {}
    sub_bodies: Dict[str, List[str]] = {}
    sub_ranges: Dict[str, tuple] = {}  # sub_name → (start_idx, end_idx)
    in_sub: Optional[str] = None
    start_idx: int = 0
    current_body: List[str] = []

    for idx, raw in enumerate(lines):
        if in_sub:
            if end_sub_pattern.match(raw):
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

    # -------------------------------------------------------------------------
    # 2) Identify which SUBs contain any QVD-load (“FROM … .qvd”) in their body.
    # -------------------------------------------------------------------------
    sub_ranges_with_qvd: List[tuple] = []
    for sub_name, body_lines in sub_bodies.items():
        for line in body_lines:
            if re.search(r"\bFROM\b.*\.qvd", line, flags=re.IGNORECASE):
                sub_ranges_with_qvd.append(sub_ranges[sub_name])
                break

    # -------------------------------------------------------------------------
    # 3) For each SUB, find which parameters are used inside a “FROM [$(param)]” in its body.
    #    We only enforce path validation for those parameters.
    # -------------------------------------------------------------------------
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

    # -------------------------------------------------------------------------
    # 4) Incrementally track per-line variable assignments (LET/SET) to resolve parameters.
    # -------------------------------------------------------------------------
    var_assignments: Dict[str, str] = {}
    assign_pattern = re.compile(r"^\s*(LET|SET)\s+(\w+)\s*=\s*(.+?);", flags=re.IGNORECASE)
    call_pattern = re.compile(r"^\s*CALL\s+(\w+)\s*\((.*?)\)", flags=re.IGNORECASE)

    def resolve_var(var_name: str, assignments: Dict[str, str], seen: Set[str]) -> Optional[str]:
        """
        Recursively resolve var_name through assignments dict.
        Avoid infinite loops by tracking 'seen'.
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

    # -------------------------------------------------------------------------
    # 5) Scan each line:
    #    a) Record any LET/SET assignments.
    #    b) If it's a SUB call to a SUB with “qvd” parameters, validate each argument.
    # -------------------------------------------------------------------------
    for idx, raw in enumerate(lines):
        line_no = idx + 1

        # 5a) Record LET/SET:  LET var = value;  or  SET var = value;
        m_assign = assign_pattern.match(raw)
        if m_assign:
            var_name = m_assign.group(2)
            value = m_assign.group(3).strip()
            var_assignments[var_name] = value

        # 5b) Check calls: CALL SubName(arg1, arg2, …)
        m_call = call_pattern.match(raw)
        if not m_call:
            continue
        sub_name = m_call.group(1)
        args_str = m_call.group(2).strip()
        if sub_name not in sub_params_used_as_path:
            continue

        args = [arg.strip() for arg in re.split(r"\s*,\s*", args_str) if arg.strip() != ""]
        params = sub_params.get(sub_name, [])
        path_params = sub_params_used_as_path[sub_name]

        for pos, param_name in enumerate(params):
            if param_name not in path_params or pos >= len(args):
                continue
            arg = args[pos]

            # Case A: literal in single quotes
            lit_match = re.match(r"^'(.*)'$", arg)
            if lit_match:
                literal = lit_match.group(1).strip()
                if not (literal.lower().startswith("lib://") or
                        re.match(r"^\$\([A-Za-z0-9_]+\)", literal)):
                    warnings.append({
                        "line": line_no,
                        "issue": f"Hardcoded SUB path literal '{literal}' passed to parameter '{param_name}'. Use lib:// or a variable.",
                        "statement": raw.rstrip(),
                    })
                continue

            # Case B: simple variable name
            var_match = re.match(r"^([A-Za-z_]\w*)$", arg)
            if var_match:
                var_name = var_match.group(1)
                resolved = resolve_var(var_name, var_assignments.copy(), set())
                if resolved:
                    # If resolution yields a literal in quotes
                    lit2 = re.match(r"^'(.*)'$", resolved)
                    if lit2:
                        literal2 = lit2.group(1).strip()
                        if not (literal2.lower().startswith("lib://") or
                                re.match(r"^\$\([A-Za-z0-9_]+\)", literal2)):
                            warnings.append({
                                "line": line_no,
                                "issue": f"Hardcoded literal '{literal2}' (via variable) passed to parameter '{param_name}'. Use lib:// or a variable.",
                                "statement": raw.rstrip(),
                            })
                    else:
                        # If resolution yields a raw lib:// path, it is considered hardcoded here
                        if resolved.lower().startswith("lib://"):
                            warnings.append({
                                "line": line_no,
                                "issue": f"Hardcoded SUB path literal '{resolved}' passed to parameter '{param_name}'. Use a variable or dynamic expression.",
                                "statement": raw.rstrip(),
                            })
                        elif re.match(r"^\$\([A-Za-z0-9_]+\).*$", resolved):
                            # A dynamic $(var) expression—OK
                            pass
                        else:
                            warnings.append({
                                "line": line_no,
                                "issue": f"Parameter '{param_name}' passed via expression '{resolved}'. Verify path conforms to rules.",
                                "statement": raw.rstrip(),
                            })
                else:
                    warnings.append({
                        "line": line_no,
                        "issue": f"Unable to resolve variable '{var_name}' passed to parameter '{param_name}'.",
                        "statement": raw.rstrip(),
                    })
                continue

            # Case C: Complex expression—prompt manual review
            warnings.append({
                "line": line_no,
                "issue": f"Parameter '{param_name}' received complex expression '{arg}'. Verify path conforms to rules.",
                "statement": raw.rstrip(),
            })

    # -------------------------------------------------------------------------
    # 6) Independently scan all lines for any “LOAD … FROM … .qvd” even across multiple lines,
    #    and flag if the entire block lies outside valid SUB ranges.
    # -------------------------------------------------------------------------
    load_select_pattern = re.compile(r"^\s*(LOAD|SELECT)\b", flags=re.IGNORECASE)
    multi_from_qvd_pattern = re.compile(r"\bFROM\b.*\.qvd", flags=re.IGNORECASE)

    if sub_ranges_with_qvd:
        idx = 0
        while idx < total_lines:
            raw = lines[idx]
            if load_select_pattern.match(raw):
                block_start = idx
                block_lines = [raw.rstrip()]
                j = idx + 1
                found_semicolon = raw.rstrip().endswith(";")
                while j < total_lines and not found_semicolon:
                    next_line = lines[j].rstrip()
                    block_lines.append(next_line)
                    if next_line.endswith(";"):
                        found_semicolon = True
                    j += 1

                full_block = " ".join(block_lines)
                if multi_from_qvd_pattern.search(full_block):
                    inside_valid = False
                    for (start, end) in sub_ranges_with_qvd:
                        if start <= block_start <= end:
                            inside_valid = True
                            break
                    if not inside_valid:
                        warnings.append({
                            "line": block_start + 1,
                            "issue": "LOAD … FROM … qvd appears outside any SUB that contains QVD logic.",
                            "statement": full_block,
                        })
                idx = j
                continue
            idx += 1

    return warnings
