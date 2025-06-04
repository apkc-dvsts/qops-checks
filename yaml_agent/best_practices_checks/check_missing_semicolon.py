"""
check_missing_semicolon.py

Warns on missing trailing semicolons for DML‐style statements. 
This runs a simple multiline check: if a DML line starts but never ends with a semicolon before the next DML, flag it.
"""

import re
from typing import List, Dict

# Lower weight because missing semicolons are less severe than structural issues.
weight = 5

def run(script_path: str) -> List[Dict]:
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    # A naive DML keyword pattern:
    dml_start_pattern = re.compile(r"^\s*(LOAD|SELECT|INSERT|UPDATE|DELETE)\b", flags=re.IGNORECASE)

    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        m = dml_start_pattern.match(raw)
        if m:
            # Collect snippet until we either find “;” on a line or hit next DML:
            snippet_lines = [raw]
            found_semicolon = raw.rstrip().endswith(";")
            lineno = idx + 1
            k = idx + 1
            while k < len(lines) and not found_semicolon:
                next_raw = lines[k]
                snippet_lines.append(next_raw)
                if next_raw.rstrip().endswith(";"):
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
                    "statement": full_stmt,
                })
            idx = k
        else:
            idx += 1

    return warnings
