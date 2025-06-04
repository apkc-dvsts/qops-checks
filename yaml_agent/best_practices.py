#!/usr/bin/env python3
"""
best_practices.py

Scan a Qlik Sense load script (QVS) for common best-practice violations by dynamically
detecting SUB definitions and classifying them into:
  - Mapping SUBs (perform MAPPING LOAD)
  - Verify SUBs (perform QvdNoOfFields checks and optimized LOAD)
  - Aggregation SUBs (perform re-aggregation with Store)

Also includes:
  - YAML-based “master measure” and “variable” checks (requires yaml_agent.models.Repository)
  - A more general script-linter enforcing:
      • SELECT * warnings
      • Missing semicolons on DML (multiline-aware) → now logs the full statement
      • Hardcoded dates in LET
      • Too many IF/ELSE clauses → logs a summary line with count
      • Enforcing uppercase Qlik keywords
      • Hardcoded FROM paths instead of lib:// or variables
      • LOAD … FROM qvd statements outside SUB LoadVerifyQVD

Usage:
    python best_practices.py --script path/to/your_script.qvs --out output_directory
    # (Optionally add --repo path/to/your_yaml_repo to run the YAML-based checks.)

Outputs:
  - Inline-SUB warnings printed to stdout
  - script_lint.yaml in `--out` if any script-lint warnings are found
  - best_practices.yaml in `--out` if any YAML-based warnings are found
"""

import os
import re
import sys
import yaml
import argparse
from typing import List, Dict

# If you have yaml_agent installed, uncomment the following import.
# Otherwise, skip YAML-based checks.
try:
    from yaml_agent.models import Repository
except ImportError:
    Repository = None


#
# Part 1: Inline-SUB & QVD usage checks
#

def load_script_lines(path: str) -> List[str]:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read().splitlines()

def load_script_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def find_subs(script_text: str):
    """
    Parse all SUB definitions and classify each into 'mapping', 'verify', or 'agg'.
    Returns three sets of SUB names.
    """
    mapping_subs = set()
    verify_subs = set()
    agg_subs = set()

    # Regex to match: SUB <name>(<params>) ... END sub;   (case-insensitive, multiline)
    sub_pattern = re.compile(
        r"SUB\s+(\w+)\s*\((.*?)\)(.*?)END\s+sub;",
        re.IGNORECASE | re.DOTALL
    )
    for match in sub_pattern.finditer(script_text):
        sub_name = match.group(1)
        sub_body = match.group(3)

        # Classify as mapping if body contains "MAPPING LOAD"
        if re.search(r"\bMAPPING\s+LOAD\b", sub_body, re.IGNORECASE):
            mapping_subs.add(sub_name)
            continue

        # Classify as verify if body checks QvdNoOfFields and does a LOAD … FROM
        if re.search(r"\bQvdNoOfFields\b", sub_body, re.IGNORECASE) and \
           re.search(r"\bFROM\s+\[.*?\.qvd\]", sub_body, re.IGNORECASE):
            verify_subs.add(sub_name)
            continue

        # Classify as aggregation if body contains "STORE … INTO … .qvd"
        if re.search(r"\bSTORE\b.*\bINTO\b.*\.qvd", sub_body, re.IGNORECASE):
            agg_subs.add(sub_name)
            continue

        # Fallback: if sub_name contains "reaggr", assume aggregation
        if "reaggr" in sub_name.lower():
            agg_subs.add(sub_name)
            continue

        # Otherwise, leave unclassified.
    return mapping_subs, verify_subs, agg_subs

def find_inline_usages(lines: List[str], pattern: str):
    """
    Find inline occurrences matching `pattern`, return list of (line_no, qvd_path, line_text).
    The regex must contain one capturing group for the QVD path.
    """
    occurrences = []
    for idx, line in enumerate(lines, start=1):
        m = re.search(pattern, line, re.IGNORECASE)
        if m:
            qvd = m.group(1)
            occurrences.append((idx, qvd, line.rstrip()))
    return occurrences

def sub_called_for_qvd(script_text: str, sub_names: set, qvd: str) -> bool:
    """
    Return True if any SUB in `sub_names` is called with the given QVD path.
    Matches patterns like: call <SubName>( ..., 'thatQvd.qvd' ...)
    """
    for sub in sub_names:
        call_pattern = re.compile(
            rf"call\s+{re.escape(sub)}\s*\([^)]*['\"]{re.escape(qvd)}['\"]",
            re.IGNORECASE
        )
        if call_pattern.search(script_text):
            return True
    return False

def inline_sub_checks(script_path: str):
    """
    Perform the inline-SUB and QVD usage checks and print results to stdout.
    """
    lines = load_script_lines(script_path)
    script_text = load_script_text(script_path)

    # 1) Parse SUB definitions and classify
    mapping_subs, verify_subs, agg_subs = find_subs(script_text)

    # 2) Define inline patterns (capture QVD path in group 1)
    inline_mapping_pattern = r"MAPPING\s+LOAD\b.*\bFROM\s+['\"]([^'\"]+\.qvd)['\"]"
    inline_load_pattern    = r"\bLOAD\b.*\bFROM\s+['\"]([^'\"]+\.qvd)['\"]"
    inline_store_pattern   = r"\bSTORE\b.*\bINTO\s+['\"]([^'\"]+\.qvd)['\"]"

    # 3) Gather inline occurrences
    inline_mapping_locs = find_inline_usages(lines, inline_mapping_pattern)
    inline_load_locs    = find_inline_usages(lines, inline_load_pattern)
    inline_store_locs   = find_inline_usages(lines, inline_store_pattern)

    # 4) Report inline MAPPING LOAD occurrences lacking a mapping SUB call
    print("\n=== Inline MAPPING LOAD → Use a Mapping SUB (detected SUBs: {}) ===".format(
        ", ".join(sorted(mapping_subs)) or "none"
    ))
    for ln, qvd, text in inline_mapping_locs:
        if not sub_called_for_qvd(script_text, mapping_subs, qvd):
            print(f"Line {ln}:    {text}")
            print(f"  → Recommend: Use a mapping SUB ({sorted(mapping_subs)}) to load '{qvd}'.\n")

    # 5) Report inline LOAD … FROM '…qvd' lacking a verify/agg SUB call
    print("=== Inline LOAD … FROM '…qvd' → Use a Verify or Aggregation SUB (detected SUBs: verify={}; agg={}) ===".format(
        sorted(verify_subs) or "none", sorted(agg_subs) or "none"
    ))
    for ln, qvd, text in inline_load_locs:
        # Skip if a verify or agg SUB already handles this QVD
        if sub_called_for_qvd(script_text, verify_subs, qvd) or sub_called_for_qvd(script_text, agg_subs, qvd):
            continue
        # Also skip if the line itself is already calling a SUB
        if any(
            re.search(rf"call\s+{re.escape(sub)}\s*\(", text, re.IGNORECASE)
            for sub in (mapping_subs | verify_subs | agg_subs)
        ):
            continue
        print(f"Line {ln}:    {text}")
        print(f"  → Recommend: Use a verify SUB ({sorted(verify_subs)}) or aggregation SUB ({sorted(agg_subs)}) for '{qvd}'.\n")

    # 6) Report STORE … INTO '…qvd' lacking an aggregation SUB call
    print("=== STORE … INTO '…qvd' → Use an Aggregation SUB (detected SUBs: {}) ===".format(
        sorted(agg_subs) or "none"
    ))
    for ln, qvd, text in inline_store_locs:
        if not sub_called_for_qvd(script_text, agg_subs, qvd):
            print(f"Line {ln}:    {text}")
            print(f"  → Recommend: Use an aggregation SUB ({sorted(agg_subs)}) to store '{qvd}'.\n")

    # 7) Summary
    print("=== Summary ===")
    print(f"Detected mapping SUBs:    {sorted(mapping_subs) or ['none']}")
    print(f"Detected verify SUBs:     {sorted(verify_subs) or ['none']}")
    print(f"Detected aggregation SUBs:{sorted(agg_subs) or ['none']}")
    print(f"Inline MAPPING LOADs:     {len(inline_mapping_locs)}")
    print(f"Inline LOAD … FROM:       {len(inline_load_locs)}")
    print(f"Inline STORE … INTO:      {len(inline_store_locs)}")
    print("\nRefactor inline QVD operations to use the appropriate SUB definitions.\n")


#
# Part 2: YAML-based Master-Measure and Variable Checks
#

def run_best_practices_checks(repo: Repository, out_dir: str) -> List[Dict]:
    """
    Inspect each BaseObject in repo for common Qlik “best-practices” warnings:
      - MasterMeasure definitions that exceed a certain length
      - Nested IF(...) deeper than 2 levels
      - Use of SELECT * in expressions
      - References to nonexistent variables
    Returns a list of warning dicts, and writes them to best_practices.yaml under out_dir.
    """
    if Repository is None:
        # yaml_agent not installed; skip
        return []

    warnings = []
    var_pattern = re.compile(r"\$(\w+)|\$\(([\w_]+)\)")

    for obj in repo.objects.values():
        if obj.node_type == "YAML_MasterMeasure":
            try:
                data = yaml.safe_load(open(obj.file_path, "r", encoding="utf-8"))
            except Exception:
                continue

            measures = data.get("qHyperCubeDef", {}).get("qMeasures", [])
            expr = ""
            for m in measures:
                mi_qi = m.get("qInfo", {})
                mid = mi_qi.get("qId") or m.get("qLibraryId")
                if mid == obj.obj_id:
                    expr = m.get("qDef", "")
                    break

            # 1) Check length
            if len(expr) > 200:
                warnings.append({
                    "obj_id": obj.obj_id,
                    "type": obj.node_type,
                    "issue": "Long expression (>200 chars)",
                    "expression": expr[:200] + "…",
                })

            # 2) Check nested IF depth
            depth = max(
                (len(x) for x in re.findall(r"(IF\s*\()", expr, flags=re.IGNORECASE)),
                default=0
            )
            if depth > 2:
                warnings.append({
                    "obj_id": obj.obj_id,
                    "type": obj.node_type,
                    "issue": f"Nested IF depth={depth}",
                    "expression": expr,
                })

            # 3) Check SELECT *
            if re.search(r"SELECT\s+\*", expr, flags=re.IGNORECASE):
                warnings.append({
                    "obj_id": obj.obj_id,
                    "type": obj.node_type,
                    "issue": "Use of SELECT * in expression",
                    "expression": expr,
                })

            # 4) Check for undefined variable references
            for var_ref in var_pattern.findall(expr):
                varname = var_ref[0] or var_ref[1]
                if varname not in repo.objects:
                    warnings.append({
                        "obj_id": obj.obj_id,
                        "type": obj.node_type,
                        "issue": f"Undefined variable reference: {varname}",
                        "expression": expr,
                    })

        elif obj.node_type == "YAML_Variable":
            try:
                data = yaml.safe_load(open(obj.file_path, "r", encoding="utf-8"))
            except Exception:
                continue
            props = data.get("Properties", {})
            expr = props.get("qDefinition", "") or data.get("definition", "")
            if len(expr) > 200:
                warnings.append({
                    "obj_id": obj.obj_id,
                    "type": obj.node_type,
                    "issue": "Long variable definition (>200 chars)",
                    "expression": expr[:200] + "…",
                })

    if warnings:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "best_practices.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"warnings": warnings}, f, sort_keys=False)

    return warnings


#
# Part 3: General Script Linter (with multiline-aware semicolon check 
#           and “LOAD … FROM qvd” only outside SUB LoadVerifyQVD)
#

def run_script_linter(script_path: str, out_dir: str) -> List[Dict]:
    """
    Enhanced QVS script linter that enforces:
      - All FROM clauses must use lib:// or a variable
      - If SUB LoadVerifyQVD is defined, then no standalone LOAD … FROM … should exist outside that SUB
      - Warn on SELECT * usage
      - Warn on missing semicolons on DML statements (multiline-aware) → logs offending statement
      - Warn on hardcoded dates in LET
      - Warn on too many IF/ELSE (suggest mapping table) → logs summary
      - Enforce uppercase Qlik keywords (LOAD, SELECT, etc.)

    Returns a list of warning dicts, writes them to script_lint.yaml under out_dir.
    """
    warnings: List[Dict] = []
    try:
        lines = open(script_path, "r", encoding="utf-8").readlines()
    except Exception:
        return warnings

    total_lines = len(lines)

    # 1) Detect SUB LoadVerifyQVD definition and its block range
    sub_start_idx = None
    sub_end_idx = None
    for idx, raw in enumerate(lines):
        if re.match(r"^\s*SUB\s+LoadVerifyQVD\b", raw, flags=re.IGNORECASE):
            sub_start_idx = idx
            break
    if sub_start_idx is not None:
        for j in range(sub_start_idx + 1, total_lines):
            if re.match(r"^\s*END\s+SUB\b", lines[j], flags=re.IGNORECASE):
                sub_end_idx = j
                break

    # 2) Patterns
    select_star_pattern = re.compile(r"SELECT\s+\*", flags=re.IGNORECASE)
    dml_start_pattern = re.compile(r"^\s*(SELECT|LOAD|STORE|INSERT|DELETE|JOIN)\b", flags=re.IGNORECASE)
    hardcoded_date_pattern = re.compile(r"^\s*LET\s+\w+\s*=\s*\d{4}", flags=re.IGNORECASE)
    if_else_pattern = re.compile(r"\bIF\s*\(|\bELSE\b", flags=re.IGNORECASE)
    lowercase_keyword_pattern = re.compile(r"\b(load|select|join|where|set|let|store|insert|delete|qualify)\b")

    seen_if_else = 0

    idx = 0
    while idx < total_lines:
        raw = lines[idx]
        lineno = idx + 1
        line = raw.rstrip()  # preserve whitespace to show full statement in log

        # Skip comment lines
        if line.lstrip().startswith("//") or line.lstrip().startswith("/*"):
            idx += 1
            continue

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

        # 5) Uppercase enforcement for keywords
        if lowercase_keyword_pattern.search(line) and not line.lstrip().startswith("//"):
            warnings.append({
                "line": lineno,
                "issue": "Keyword should be uppercase (e.g. LOAD, SELECT, WHERE, JOIN, SET, LET, QUALIFY)",
                "statement": line
            })

        # 6) FROM clause path check: must begin with lib:// or be a variable
        m = re.search(r"\bFROM\s+'([^']+)'", line, flags=re.IGNORECASE)
        if m:
            path = m.group(1).strip()
            if not path.lower().startswith("lib://") and not re.match(r"\$\([A-Za-z0-9_]+\)", path):
                warnings.append({
                    "line": lineno,
                    "issue": f"Hardcoded FROM path '{path}'. Use lib:// or a variable.",
                    "statement": line
                })

        # 7) Count IF/ELSE occurrences for mapping-table suggestion
        if if_else_pattern.search(line):
            seen_if_else += 1

        # 8) Detect “LOAD … FROM … qvd” and warn if outside SUB LoadVerifyQVD
        if re.match(r"^\s*LOAD\b.*\bFROM\b.*\.qvd", line, flags=re.IGNORECASE):
            if sub_start_idx is not None:
                if not (sub_start_idx <= idx <= (sub_end_idx or sub_start_idx)):
                    warnings.append({
                        "line": lineno,
                        "issue": "LOAD … FROM … qvd appears outside of SUB LoadVerifyQVD. All QVD loads should be wrapped by LoadVerifyQVD.",
                        "statement": line
                    })

        # 9) DML statements (SELECT/LOAD/STORE/INSERT/DELETE/JOIN). Check for trailing semicolon—
        #    but allow multiline up to the line that actually ends in “;”. Warn only if no “;” appears
        #    before the next DML start or EOF. Log the full statement (concatenated snippet).
        if dml_start_pattern.match(line):
            if not line.rstrip().endswith(";"):
                # Collect multiline block until semicolon or next DML or EOF
                snippet_lines = [line]
                found_semicolon = False
                j = idx + 1
                while j < total_lines:
                    next_raw = lines[j].rstrip()
                    snippet_lines.append(next_raw)
                    if next_raw.rstrip().endswith(";"):
                        found_semicolon = True
                        break
                    if dml_start_pattern.match(next_raw):
                        break
                    j += 1

                if not found_semicolon:
                    full_stmt = "\n".join(snippet_lines)
                    warnings.append({
                        "line": lineno,
                        "issue": "Statement likely missing trailing semicolon",
                        "statement": full_stmt
                    })
                # Move idx to j (so we skip scanning intermediate lines again)
                idx = j
                continue  # skip idx += 1 at bottom

        idx += 1

    # 10) If too many IF/ELSE, recommend mapping table (include count in statement)
    if seen_if_else > 5:
        warnings.append({
            "line": 1,
            "issue": f"Detected {seen_if_else} IF/ELSE occurrences; consider using a mapping table instead of repeated IF/ELSE.",
            "statement": f"Total IF/ELSE count = {seen_if_else}"
        })

    # 11) Write YAML if any warnings
    if warnings:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "script_lint.yaml")
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"script_warnings": warnings}, f, sort_keys=False)

    return warnings


#
# Part 4: CLI Entry Point
#

def main():
    parser = argparse.ArgumentParser(
        description="Run Qlik Sense best-practices checks (inline-SUB, script lint, optional YAML)."
    )
    parser.add_argument(
        "--script", "-s", required=True,
        help="Path to the .qvs (or any text) script to lint and analyze."
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

    # 1) Inline-SUB & QVD usage checks (printed to stdout)
    print("\n>>> Running inline-SUB QVD usage checks:")
    inline_sub_checks(script_path)

    # 2) Script linter
    print("\n>>> Running general script linter:")
    script_warnings = run_script_linter(script_path, out_dir)
    if script_warnings:
        print(f"[script_lint.yaml written to {out_dir}] ({len(script_warnings)} warning(s))")
        # Also print each warning here for immediate feedback:
        for w in script_warnings:
            print(f"Line {w['line']:>4}: {w['issue']}")
            # Indent the offending statement (up to the first newline) by two spaces:
            stmt = w.get("statement", "").split("\n")[0]
            print(f"  → {stmt}")
        print()
    else:
        print("No script-lint warnings found.\n")

if __name__ == "__main__":
    main()
