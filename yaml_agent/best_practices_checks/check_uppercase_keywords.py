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
    # ,"LOAD"
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
    ,"STORE"
]

def run(script_path: str) -> List[Dict]:
    warnings: List[Dict] = []
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return warnings

    in_block_comment = False  # Tracks whether we're inside /* ... */ comment

    for idx, raw in enumerate(lines):
        line = raw
        processed_line = ""
        i = 0

        # Remove all multi-line comment segments, tracking state across lines
        while i < len(line):
            if not in_block_comment:
                start_idx = line.find("/*", i)
                if start_idx == -1:
                    # No start of block comment on this line
                    processed_line += line[i:]
                    break
                else:
                    # Append everything up to the start of block comment
                    processed_line += line[i:start_idx]
                    i = start_idx + 2
                    in_block_comment = True
            else:
                end_idx = line.find("*/", i)
                if end_idx == -1:
                    # Block comment continues beyond this line
                    i = len(line)
                else:
                    # End of block comment found; skip the commented segment
                    i = end_idx + 2
                    in_block_comment = False

        # At this point, processed_line has no multi-line comments for this line
        stripped = processed_line.lstrip()

        # Skip if the (remaining) line is a single-line comment
        if stripped.startswith("//"):
            continue

        # Now check for keywords in processed_line
        for kw in KEYWORDS:
            # Case-insensitive search for the keyword (whole word)
            pattern_ci = re.compile(rf"\b{kw}\b", flags=re.IGNORECASE)
            pattern_upper = re.compile(rf"\b{kw}\b")
            if pattern_ci.search(processed_line) and not pattern_upper.search(processed_line):
                warnings.append({
                    "line": idx + 1,
                    "issue": f"Keyword '{kw}' not fully uppercase.",
                    "statement": raw.rstrip(),
                })
                # Only warn once per line per keyword
                break

    return warnings