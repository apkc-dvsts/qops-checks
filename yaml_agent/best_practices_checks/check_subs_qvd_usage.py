#!/usr/bin/env python3
"""
check_subs_qvd_usage.py
=======================

Rule
----
• Every final `LOAD … (qvd)` must reside inside a *QVD-verifying loader SUB*.
• If the script contains **zero** such SUBs, the rule is silent.
• A SUB is *verifying* when it keeps at least one produced table
  **and** either
      – calls  QvdNoOfFields( / QvdFieldName( ),  **or**
      – contains a literal  FROM … (qvd|.qvd)  load.

“Produced tables” include
  • aliases loaded from a QVD,
  • aliases **stored** into a new QVD (`STORE alias INTO … (qvd)`),
  • parameters whose names contain “table”.

Exports
-------
    weight : int
    run(script_path) -> List[Dict]

Logging
-------
Each SUB is logged (INFO) as:

    SUB <name> → verifier / non-verifier
          (QvdCalls=Y/N, Stores=Y/N, KeepsTable=Y/N)
"""

from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "reaggr", "reagg", "aggregate", "reprocess",
    "process",    # ⬅ added
    "recalc", "pivot", "refresh", "transform",
)
weight = 8  # ordering priority

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _strip_comments(line: str, in_block: bool) -> tuple[str, bool]:
    """Remove // … and /* … */ comments (supports nested block comments)."""
    i, out = 0, []
    while i < len(line):
        if in_block:
            end = line.find("*/", i)
            if end == -1:
                return "", True
            i = end + 2
            in_block = False
        else:
            dbl = line.find("//", i)
            blk = line.find("/*", i)
            if dbl == blk == -1:
                out.append(line[i:])
                break
            if dbl != -1 and (blk == -1 or dbl < blk):
                out.append(line[i:dbl])
                break
            out.append(line[i:blk])
            i = blk + 2
            in_block = True
    return "".join(out), in_block


def _resolve_chain(var: str, assigns: Dict[str, str], seen: Set[str]) -> Optional[str]:
    """Follow LET/SET chains until a literal/lib:///$(…) expression."""
    if var in seen:
        return None
    seen.add(var)
    val = assigns.get(var)
    if val is None:
        return None
    m = re.match(r"^([A-Za-z_]\w*)$", val)
    return _resolve_chain(m.group(1), assigns, seen) if m else val

# ---------------------------------------------------------------------------
# main rule
# ---------------------------------------------------------------------------
def run(script_path: str | Path) -> List[Dict]:
    warnings: List[Dict] = []

    script_path = Path(script_path)
    try:
        raw_lines = script_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # pragma: no cover
        log.error("Cannot read %s: %s", script_path, exc)
        return warnings

    # 0) strip comments
    lines, in_block = [], False
    for raw in raw_lines:
        clean, in_block = _strip_comments(raw, in_block)
        lines.append(clean)

    # 1) SUB boundaries & params
    sub_def_rx = re.compile(r"^\s*SUB\s+(\w+)\s*\((.*?)\)", re.I)
    sub_end_rx = re.compile(r"^\s*END\s+SUB\b", re.I)
    sub_ranges, sub_bodies, sub_params = {}, {}, {}

    in_sub: Optional[str] = None
    body: List[str] = []
    start_idx = 0

    for idx, ln in enumerate(lines):
        if in_sub:
            if sub_end_rx.match(ln):
                sub_bodies[in_sub] = body.copy()
                sub_ranges[in_sub] = (start_idx, idx)
                in_sub, body = None, []
            else:
                body.append(ln)
        else:
            if (m := sub_def_rx.match(ln)):
                in_sub = m.group(1)
                param_str = m.group(2).strip()
                params = [p.strip() for p in param_str.split(",")] if param_str else []
                sub_params[in_sub] = params
                start_idx, body = idx, []

    # 2) classify verifier SUBs
    qvd_load_rx  = re.compile(r"\bFROM\b.*(\.qvd\b|\(\s*qvd\s*\))", re.I)
    verify_fn_rx = re.compile(r"(QvdNoOfFields|QvdFieldName)\s*\(", re.I)
    alias_lbl_rx = re.compile(r"^\s*(\w+)\s*:\s*$", re.I)         # Alias:
    concat_rx    = re.compile(r"\bCONCATENATE\s*\(\s*(\w+)\s*\)", re.I)
    # ← NEW: capture alias inside [], quotes, or bare identifier
    store_rx     = re.compile(
        r"""^\s*STORE\s+
            (?:
              \[\s*([^\]]+?)\s*\]     |   # [alias]
              "([^"]+)"               |   # "alias"
              (\w+)                       # bare alias
            )
            \s+INTO\b.*\(qvd\)""",
        re.I | re.X,
    )
    drop_rx      = re.compile(r"^\s*DROP\s+TABLE\s+(\w+)\b", re.I)

    verifier_ranges: List[tuple[int, int]] = []

    for sub, (s_idx, e_idx) in sub_ranges.items():
        body = sub_bodies[sub]
        lower_name = sub.lower()

        if any(kw in lower_name for kw in NEGATIVE_KEYWORDS):
            log.info("SUB %-30s → non-verifier (negative name)", sub)
            continue

        has_verify_call = any(verify_fn_rx.search(l) for l in body)

        produced, dropped = set(), set()
        current_alias: Optional[str] = None
        has_literal_qvd = False
        has_store = False

        # Add param aliases containing "table"
        produced.update(p for p in sub_params[sub] if "table" in p.lower())

        for ln in body:
            if (m := alias_lbl_rx.match(ln)):
                current_alias = m.group(1)
                continue
            if (m := concat_rx.search(ln)):
                current_alias = m.group(1)

            if qvd_load_rx.search(ln):
                has_literal_qvd = True
                if current_alias:
                    produced.add(current_alias)

            if (m := store_rx.match(ln)):
                alias = m.group(1) or m.group(2) or m.group(3)
                produced.add(alias)
                has_store = True

            if (m := drop_rx.match(ln)):
                dropped.add(m.group(1))

        keeps_table = bool(produced - dropped)
        is_verifier = keeps_table and (has_verify_call or has_literal_qvd)

        log.info(
            "SUB %-30s → %-12s  (QvdCalls=%s, Stores=%s, KeepsTable=%s)",
            sub,
            "verifier" if is_verifier else "non-verifier",
            "Y" if has_verify_call else "N",
            "Y" if has_store else "N",
            "Y" if keeps_table else "N",
        )

        if is_verifier:
            verifier_ranges.append((s_idx, e_idx))

    # If no verifier – rule silent
    if not verifier_ranges:
        log.info("➡  No QVD-verifying SUB present – outer-LOAD rule disabled.")
        return warnings

    # 3) collect SET/LET and path-parameters
    assigns: Dict[str, str] = {}
    assign_rx = re.compile(r"^\s*(LET|SET)\s+(\w+)\s*=\s*(.+?);", re.I)
    call_rx   = re.compile(r"^\s*CALL\s+(\w+)\s*\((.*?)\)", re.I)
    path_param_rx = re.compile(r"\bFROM\s+\[\$\(\s*([A-Za-z_]\w*)\s*\)\]", re.I)

    for ln in lines:
        if (m := assign_rx.match(ln)):
            assigns[m.group(2)] = m.group(3).strip()

    path_params: Dict[str, Set[str]] = {
        sub: {m.group(1) for l in body for m in path_param_rx.finditer(l)}
        for sub, body in sub_bodies.items()
    }

    def warn(idx: int, issue: str, stmt: str) -> None:
        warnings.append({"line": idx + 1, "issue": issue, "statement": stmt})

    # 4) validate CALL … arg paths
    for idx, ln in enumerate(lines):
        if not (m := call_rx.match(ln)):
            continue
        sub, arg_str = m.group(1), m.group(2).strip()
        if sub not in path_params or not path_params[sub]:
            continue

        args = [a.strip() for a in re.split(r"\s*,\s*", arg_str) if a.strip()]
        for pos, param in enumerate(sub_params[sub]):
            if param not in path_params[sub] or pos >= len(args):
                continue
            arg = args[pos]

            # literal
            if (lit := re.match(r"^'(.*)'$", arg)):
                v = lit.group(1).strip()
                if not (v.lower().startswith("lib://") or re.match(r"^\$\([A-Za-z0-9_]+\)", v)):
                    warn(idx, f"Hard-coded path '{v}' passed to '{param}'.", ln)
                continue

            # variable (unresolved allowed)
            if (var := re.match(r"^([A-Za-z_]\w*)$", arg)):
                res = _resolve_chain(var.group(1), assigns, set())
                if res:
                    if (lit := re.match(r"^'(.*)'$", res)):
                        vv = lit.group(1).strip()
                        if not (vv.lower().startswith("lib://") or re.match(r"^\$\([A-Za-z0-9_]+\)", vv)):
                            warn(idx, f"Hard-coded literal '{vv}' (via var) passed to '{param}'.", ln)
                    elif res.lower().startswith("lib://"):
                        warn(idx, f"Hard-coded lib path '{res}' passed to '{param}'.", ln)
                    elif not re.match(r"^\$\([A-Za-z0-9_]+\)", res):
                        warn(idx, f"Unverified expression '{res}' passed to '{param}'.", ln)
                continue

            # anything else
            warn(idx, f"Complex expression '{arg}' passed to '{param}'.", ln)

    # 5) flag outer LOAD … (qvd)
    load_start_rx = re.compile(r"^\s*(CONCATENATE\s*\([^)]*\)\s*)?LOAD\b", re.I)

    i = 0
    while i < len(lines):
        if not load_start_rx.match(lines[i]):
            i += 1
            continue
        start = i
        block, i = [lines[i]], i + 1
        while i < len(lines) and not lines[i].rstrip().endswith(";"):
            block.append(lines[i])
            i += 1
        if i < len(lines):
            block.append(lines[i])

        full = " ".join(block)
        if qvd_load_rx.search(full) and not any(s <= start <= e for s, e in verifier_ranges):
            warn(start, "LOAD … (qvd) outside any QVD-verifying SUB.", full)
        i += 1

    return warnings
