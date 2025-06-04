"""
check_nested_if_master_measure.py

Checks YAML “master‐measure” objects for nested IF(...) depth > 1.
"""

import re
from typing import List, Dict
from yaml_agent.models import Repository, BaseObject
import os
from yaml_agent.yaml_loader import load_yaml_file

# Assign a default weight for this check; adjust as needed.
weight = 10

def run(repo_root: str) -> List[Dict]:
    """
    Scan every YAML file under repo_root, build a Repository of BaseObject instances,
    then for each object of node_type "YAML_MasterMeasure", check the maximum nesting
    of IF(...) calls. If depth > 1, emit a warning.
    """
    warnings: List[Dict] = []
    repo = Repository()

    # 1) Walk the directory and load every .yaml/.yml file
    for dirpath, _, filenames in os.walk(repo_root):
        for fname in filenames:
            if not (fname.lower().endswith(".yaml") or fname.lower().endswith(".yml")):
                continue
            full_path = os.path.join(dirpath, fname)
            data = load_yaml_file(full_path)
            if not data:
                continue

            # 2) Look for top‐level dicts or lists and create BaseObject entries
            def _scan_node(node, file_path):
                if isinstance(node, dict):
                    # If this dict represents a MasterMeasure, it should have a "qInfo" key, etc.
                    node_type = node.get("node_type") or node.get("type") or ""
                    if node_type == "YAML_MasterMeasure":
                        obj_id = node.get("obj_id") or f"{file_path}:{len(repo.objects)}"
                        fields = list(node.keys())
                        bo = BaseObject(
                            obj_id=obj_id,
                            node_type="YAML_MasterMeasure",
                            file_path=file_path,
                            fields=fields,
                            raw_yaml=node
                        )
                        repo.add_object(bo)
                    # Recurse into nested dicts
                    for v in node.values():
                        if isinstance(v, (dict, list)):
                            _scan_node(v, file_path)
                elif isinstance(node, list):
                    for item in node:
                        _scan_node(item, file_path)

            _scan_node(data, full_path)

    # 3) Now iterate over each BaseObject in the repository
    for obj in repo.objects.values():
        if obj.node_type == "YAML_MasterMeasure" and obj.raw_yaml:
            # Attempt to extract any expressions under this master‐measure node.
            # Conventionally, QMeasure expressions live under obj.raw_yaml["qMeasure"]["qDef"]["qDef"] 
            # or similar. Adjust the path according to your schema.
            exprs = []
            qm = obj.raw_yaml.get("qMeasure", {})
            qdef = qm.get("qDef", {})
            if isinstance(qdef, dict):
                # qDef might have a 'qDef' key or a 'expression' key
                inner = qdef.get("qDef") or qdef.get("expression") or ""
                if isinstance(inner, str):
                    exprs.append(inner)
                elif isinstance(inner, list):
                    exprs.extend([e for e in inner if isinstance(e, str)])

            # Also scan any other string‐valued fields for "IF("
            for v in obj.raw_yaml.values():
                if isinstance(v, str) and "IF" in v.upper():
                    exprs.append(v)

            # 4) For each expression, count nesting of IF(...)
            for expr in exprs:
                # Count occurrences of "IF(" recursively; this is a heuristic
                depth = 0
                tokens = re.findall(r"(IF\s*\()", expr, flags=re.IGNORECASE)
                if tokens:
                    # A rough depth measure: number of "IF(" occurrences
                    depth = len(tokens)
                if depth > 1:
                    warnings.append({
                        "file": obj.file_path,
                        "type": obj.node_type,
                        "issue": f"Nested IF depth={depth} in master‐measure",
                        "expression": expr,
                    })

    return warnings
