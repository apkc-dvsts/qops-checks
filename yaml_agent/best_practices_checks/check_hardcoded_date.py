"""
check_hardcoded_date.py

Warns if a LET statement contains a hardcoded date literal (e.g., LET vDate = ’2021-01-01’).
"""

import re
from typing import List, Dict

# Lower weight than SELECT * but still important to catch.
weight = 4

def run(script_path: str) -> List[Dict]:
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    # Look for LET <var> = 'YYYY-MM-DD' (very simple date pattern):
    pattern = re.compile(r"^\s*LET\s+\w+\s*=\s*'(\d{4}-\d{2}-\d{2})'", flags=re.IGNORECASE)

    for idx, raw in enumerate(lines):
        m = pattern.match(raw)
        if m:
            date_literal = m.group(1)
            warnings.append({
                "line": idx + 1,
                "issue": f"Hardcoded date literal ({date_literal}) in LET.",
                "statement": raw.rstrip(),
            })
    return warnings
