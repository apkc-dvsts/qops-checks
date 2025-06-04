"""
check_static_qvd_path.py

Flags any literal or variable→literal path to a .qvd file that is fully static
(e.g., “lib://mydata/folder/file.qvd”).
"""

import re
from typing import List, Dict

# Highest priority, because reading static QVD paths is a big issue.
weight = 11

def run(script_path: str) -> List[Dict]:
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    # Look for “… FROM … .qvd” with a literal lib://path
    pattern = re.compile(r"FROM\s+'(lib://.*\.qvd)'", flags=re.IGNORECASE)
    for idx, raw in enumerate(lines):
        m = pattern.search(raw)
        if m:
            literal_path = m.group(1)
            warnings.append({
                "line": idx + 1,
                "issue": f"Static QVD path used: {literal_path}",
                "statement": raw.rstrip(),
            })
    return warnings
