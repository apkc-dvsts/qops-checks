# === yaml_dependency_agent/yaml_agent/schema_documenter.py ===

import os
import json
from typing import Dict, List
from yaml_agent.models import Repository

def gather_schemas_with_cardinality(repo: Repository) -> Dict[str, Dict]:
    """
    For each final node_type in repo, build:
      {
        "fields": [...]
        "mandatory": [...]
        "optional": [...]
      }
    """
    type_to_fieldsets: Dict[str, List[List[str]]] = {}
    for obj in repo.objects.values():
        tn = obj.node_type
        fs = obj.fields or []
        type_to_fieldsets.setdefault(tn, []).append(fs)

    output = {}
    for tn, list_of_fieldlists in type_to_fieldsets.items():
        all_fields = set()
        for flds in list_of_fieldlists:
            all_fields.update(flds)

        # Mandatory: intersection of all field-lists
        mand = set(list_of_fieldlists[0])
        for flds in list_of_fieldlists[1:]:
            mand.intersection_update(flds)

        # Optional: fields present sometimes but not always
        opt = all_fields - mand

        output[tn] = {
            "fields": sorted(all_fields),
            "mandatory": sorted(mand),
            "optional": sorted(opt)
        }
    return output

def write_schema_docs_with_cardinality(schema_map: Dict[str, Dict], out_dir: str):
    """
    For each node_type, write:
      - <out_dir>/schemas/<node_type>.json
      - <out_dir>/schemas/<node_type>.md
    """
    schemas_dir = os.path.join(out_dir, "schemas")
    os.makedirs(schemas_dir, exist_ok=True)

    for type_name, info in schema_map.items():
        safe_name = "".join(c if c.isalnum() else "_" for c in type_name)
        json_path = os.path.join(schemas_dir, f"{safe_name}.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump({
                "type_name": type_name,
                "fields": info["fields"],
                "mandatory": info["mandatory"],
                "optional": info["optional"]
            }, jf, indent=2)

        md_path = os.path.join(schemas_dir, f"{safe_name}.md")
        with open(md_path, "w", encoding="utf-8") as mf:
            mf.write(f"# Schema: {type_name}\n\n")
            mf.write("## Mandatory Fields (appear in every object of this type)\n\n")
            if info["mandatory"]:
                for fld in info["mandatory"]:
                    mf.write(f"- `{fld}`\n")
            else:
                mf.write("_None_\n")
            mf.write("\n## Optional Fields (appear in some objects, but not all)\n\n")
            if info["optional"]:
                for fld in info["optional"]:
                    mf.write(f"- `{fld}`\n")
            else:
                mf.write("_None_\n")
            mf.write("\n**Full Field List:**\n\n")
            for fld in info["fields"]:
                mf.write(f"- `{fld}`\n")
            mf.write("\n")
