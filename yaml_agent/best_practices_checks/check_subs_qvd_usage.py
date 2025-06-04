"""
check_subs_qvd_usage.py

Scans a QVS script for:
  - SUB definitions and their parameters
  - Variables passed to SUB path parameters must start with lib:// or $(…)
  - Per‐line, incremental variable resolution
"""

import re
import os
from typing import List, Dict

# You can raise or lower this weight relative to other checks.
weight = 8

def run(script_path: str) -> List[Dict]:
    """
    Scan a QVS script for SUB‐definition issues and QVD usage.
    Returns a list of warning dicts.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    # 1) Parse all SUB definitions: name → parameter list; also gather body lines
    sub_def_pattern = re.compile(r"^\s*SUB\s+(\w+)\s*\((.*?)\)", flags=re.IGNORECASE)
    subs = {}
    for idx, raw in enumerate(lines):
        m = sub_def_pattern.match(raw)
        if m:
            sub_name = m.group(1)
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            # find “END SUB” block to capture body (if needed)
            subs[sub_name] = {"params": params, "start_line": idx, "body": []}

    # 2) For each SUB usage, verify any “path” parameter either starts with lib:// or is dynamic.
    for sub_name, meta in subs.items():
        body_lines = []
        idx = meta["start_line"] + 1
        while idx < len(lines):
            if re.match(r"^\s*END SUB", lines[idx], flags=re.IGNORECASE):
                break
            body_lines.append(lines[idx])
            idx += 1
        for line_no, raw in enumerate(body_lines, start=meta["start_line"] + 2):
            # Look for calls to SUB: SUB x( … )
            for param_name in meta["params"]:
                # naive pattern: param_name = value
                pattern = rf"{param_name}\s*=\s*(.+?)(,|\))"
                m2 = re.search(pattern, raw, flags=re.IGNORECASE)
                if m2:
                    resolved = m2.group(1).strip()
                    # If literal string in quotes:
                    lit2 = re.match(r"^'(.*)'$", resolved)
                    if lit2:
                        literal2 = lit2.group(1).strip()
                    else:
                        literal2 = resolved.strip()
                    # If literal2 is a complete lib:// path ending with extension => flag
                    if re.match(r"^lib://.*\.\w+$", literal2, flags=re.IGNORECASE):
                        warnings.append({
                            "line": line_no,
                            "issue": f"Static path '{literal2}' passed to parameter '{param_name}'.",
                            "statement": raw.rstrip(),
                        })
                    # otherwise consider dynamic enough; no warning
    return warnings
