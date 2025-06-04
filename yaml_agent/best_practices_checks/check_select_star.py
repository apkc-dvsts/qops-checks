"""
check_select_star.py

Warns if a script line contains “SELECT *” (case‐insensitive).
"""

import re
from typing import List, Dict

# Medium‐high priority: we usually want to catch SELECT * early.
weight = 9

def run(script_path: str) -> List[Dict]:
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    for idx, raw in enumerate(lines):
        if re.search(r"\bSELECT\s+\*\b", raw, flags=re.IGNORECASE):
            warnings.append({
                "line": idx + 1,
                "issue": "Avoid using SELECT * (not field‐specific).",
                "statement": raw.rstrip(),
            })
    return warnings
