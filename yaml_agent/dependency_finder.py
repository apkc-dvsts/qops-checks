# … your existing imports …
import json
from yaml_agent.identifier_extractor import classify_and_extract
from yaml_agent.models import BaseObject, Repository
from yaml_agent.schema_inferer import infer_schema_for_base_object
from yaml_agent.knowledge_base import KnowledgeBase

# === NEW: helper to discover any string leaf matching a known obj_id ===
def find_additional_refs(yaml_node, known_ids: set, found: set = None) -> set:
    if found is None:
        found = set()

    if isinstance(yaml_node, dict):
        for k, v in yaml_node.items():
            # If the key name hints "id" or "ref" and value matches a known_id, capture it
            if isinstance(v, str) and (("id" in k.lower()) or ("ref" in k.lower())) and (v in known_ids):
                found.add(v)
            # Recurse on the child
            find_additional_refs(v, known_ids, found)

    elif isinstance(yaml_node, list):
        for elem in yaml_node:
            find_additional_refs(elem, known_ids, found)

    else:
        # If it’s a string equal to a known_id
        if isinstance(yaml_node, str) and yaml_node in known_ids:
            found.add(yaml_node)

    return found

def _create_base_object(info: dict, repo: Repository, kb: KnowledgeBase, logger):
    """
    Given a dict (with keys obj_id, type_name, fields, file_path, depends_on, raw_yaml_dict),
    construct a BaseObject, infer its final schema, record dependencies (if any),
    and add to repo (if not already present).
    """
    obj_id    = info.get("obj_id")
    raw_type  = info.get("type_name")
    file_path = info.get("file_path")
    fields    = info.get("fields", [])
    deps      = info.get("depends_on", [])[:]      # already‐extracted if any
    raw_yaml  = info.get("raw_yaml_dict")          # the entire subtree

    temp_obj = BaseObject(
        obj_id      = obj_id,
        node_type   = raw_type,
        file_path   = file_path,
        fields      = fields,
        depends_on  = deps,
        raw_yaml    = raw_yaml       # stash it for pass #2
    )

    # Infer the final schema/type_name (clusters known classes)
    final_type = infer_schema_for_base_object(kb, temp_obj, logger)
    temp_obj.node_type = final_type

    # Add to repo
    if obj_id and repo.find_by_id(obj_id) is None:
        repo.add_object(temp_obj)
        logger.info(f"Added object '{obj_id}' as '{final_type}'")
    else:
        logger.debug(f"Object '{obj_id}' already exists—skipping.")

def _scan_dict_node(node, file_path, repo, kb, logger, is_root=False):
    """
    Recursively scan a dict node. For the very root dict, is_root=True.
    """
    if not isinstance(node, dict):
        return

    info = classify_and_extract(node, file_path, is_root)
    if isinstance(info, list):
        for m in info:
            # *** Tweak: embed the raw subtree so we can re‐scan later ***
            m["raw_yaml_dict"] = node
            _create_base_object(m, repo, kb, logger)
    elif isinstance(info, dict):
        info["raw_yaml_dict"] = node
        _create_base_object(info, repo, kb, logger)
    # else: skip

    for value in node.values():
        if isinstance(value, dict):
            _scan_dict_node(value, file_path, repo, kb, logger, is_root=False)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _scan_dict_node(item, file_path, repo, kb, logger, is_root=False)

def process_yaml_file(yaml_data, file_path, repo, kb, logger):
    logger.info(f"Processing YAML file: {file_path}")
    if isinstance(yaml_data, list):
        for elem in yaml_data:
            if isinstance(elem, dict):
                _scan_dict_node(elem, file_path, repo, kb, logger, is_root=True)
    elif isinstance(yaml_data, dict):
        _scan_dict_node(yaml_data, file_path, repo, kb, logger, is_root=True)
    else:
        logger.warning(f"Top‐level YAML node in {file_path} is neither dict nor list—skipping.")
