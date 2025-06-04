"""
check_uppercase_keywords.py

Warns if Qlik keywords are not fully uppercase (e.g., “load” instead of “LOAD”).
"""

import re
from typing import List, Dict

# Medium weight—stylistic but often enforced.
weight = 6

# A small list of common Qlik script keywords to check:
KEYWORDS = [
    ""
    #, "LOAD"
    # ,"SELECT"
    # ,"FROM"
    # ,"JOIN"
    # ,"INNER"
    # ,"OUTER"
    # ,"LEFT"
    # ,"RIGHT"
    # ,"WHERE"
    # ,"GROUP"
    # ,"BY"
    # ,"ORDER"
    # ,"AS"
    # ,"IF"
    # ,"ELSE"
    # ,"DROP"
    # ,"STORE"
]

def run(script_path: str) -> List[Dict]:
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    for idx, raw in enumerate(lines):
        for kw in KEYWORDS:
            # match the keyword in any case, but skip if it’s already uppercase:
            pattern = re.compile(rf"\b{kw}\b", flags=re.IGNORECASE)
            if pattern.search(raw) and not re.search(rf"\b{kw}\b", raw):
                # meaning we found “load” or “Load” but not exact “LOAD”
                warnings.append({
                    "line": idx + 1,
                    "issue": f"Keyword '{kw}' not fully uppercase.",
                    "statement": raw.rstrip(),
                })
                # only warn once per line per keyword
                break
    return warnings
