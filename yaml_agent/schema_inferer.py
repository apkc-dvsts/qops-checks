# === yaml_dependency_agent/yaml_agent/schema_inferer.py ===

import json
from typing import List
from yaml_agent.knowledge_base import KnowledgeBase

# Pre-defined known Qlik YAML object classes
KNOWN_SCHEMAS = {
    "YAML_Dimension",
    "YAML_MasterMeasure",
    "YAML_MasterObject",
    "YAML_Variable",
    "YAML_Sheet",
    "YAML_Widget"
}

def extract_field_names(yaml_subtree: dict) -> List[str]:
    """
    Return a sorted list of keys (as lowercase strings) for fallback matching.
    """
    if not isinstance(yaml_subtree, dict):
        return []
    return sorted([str(k).lower() for k in yaml_subtree.keys()])

def propose_type_for_fields(
    kb: KnowledgeBase,
    fields: List[str],
    existing_type: str = None
) -> str:
    """
    If existing_type is already in KNOWN_SCHEMAS, return it immediately.
    Otherwise, attempt exact match on fields; if none, fuzzy match; if none, create new UnknownType_N.
    """
    # 1) If node_type is already known, keep it
    if existing_type and existing_type in KNOWN_SCHEMAS:
        return existing_type

    normalized = [f.lower() for f in fields]

    # 2) Exact match on field sets
    for t in kb.list_types():
        existing_fields = [fld.lower() for fld in t["fields"]]
        if set(existing_fields) == set(normalized):
            return t["type_name"]

    # 3) Fuzzy match (if enabled)
    candidate_id = None
    try:
        candidate_id = kb.find_candidate_type(normalized, threshold=0.9)
    except Exception:
        candidate_id = None

    if candidate_id:
        row = next((r for r in kb.list_types() if r["type_id"] == candidate_id), None)
        if row:
            return row["type_name"]

    # 4) No match → create a new UnknownType
    existing = kb.list_types()
    max_id = max((r["type_id"] for r in existing), default=0)
    new_type = f"UnknownType_{max_id + 1}"
    kb.add_type(new_type, normalized)
    return new_type

def infer_schema_for_base_object(kb: KnowledgeBase, base_obj, logger=None) -> str:
    """
    Given a BaseObject with raw node_type and fields, return the final node_type:
      - If raw node_type is in KNOWN_SCHEMAS, use that.
      - Otherwise, call propose_type_for_fields on base_obj.fields.
    """
    logger = logger or __import__("logging").getLogger(__name__)
    raw_type = base_obj.node_type

    if raw_type in KNOWN_SCHEMAS:
        logger.debug(f"    Using known schema '{raw_type}' for '{base_obj.obj_id}'")
        return raw_type

    fields = base_obj.fields or []
    inferred = propose_type_for_fields(kb, fields, existing_type=None)
    logger.debug(f"    Inferred schema '{inferred}' for '{base_obj.obj_id}' (fields={fields})")
    return inferred
