# === yaml_dependency_agent/yaml_agent/report_generator.py ===

import json
import os
from datetime import datetime
import networkx as nx
from yaml_agent.models import Repository

def generate_object_report(repo: Repository, out_dir: str):
    """
    Write a JSON file listing each object (obj_id, node_type, file_path, fields, depends_on).
    """
    os.makedirs(out_dir, exist_ok=True)
    objects_list = []
    for obj_id, obj in repo.objects.items():
        objects_list.append({
            "obj_id": obj.obj_id,
            "type_name": obj.node_type,
            "file_path": obj.file_path,
            "fields": obj.fields,
            "depends_on": obj.depends_on
        })
    with open(os.path.join(out_dir, "all_objects.json"), "w", encoding="utf-8") as f:
        json.dump(objects_list, f, indent=2)

def generate_dependency_graph_output(G: nx.DiGraph, out_path: str):
    """
    Write nodes + edges to JSON, including node attributes.
    """
    data = {"nodes": [], "edges": []}
    for node, attrs in G.nodes(data=True):
        data["nodes"].append({
            "id": node,
            "type_name": attrs.get("type_name", ""),
            "file_path": attrs.get("file_path", "")
        })
    for src, dst in G.edges():
        data["edges"].append({"from": src, "to": dst})
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)

def generate_markdown_report(G: nx.DiGraph, repo: Repository, out_path: str):
    """
    Produce a human-readable Markdown: for each object, show type, file, fields, dependencies.
    """
    with open(out_path, "w", encoding="utf-8") as md:
        md.write(f"# YAML Dependency & Schema Report  \n")
        md.write(f"Generated: {datetime.utcnow().isoformat()} UTC  \n\n")
        md.write(f"## Objects ({len(repo.objects)})\n\n")
        for obj_id, obj in repo.objects.items():
            md.write(f"### `{obj_id}`  \n")
            md.write(f"- **Type**: {obj.node_type}  \n")
            md.write(f"- **File**: {obj.file_path}  \n")
            md.write(f"- **Fields**: {', '.join(obj.fields)}  \n")
            if obj.depends_on:
                md.write(f"- **Depends on**: {', '.join(obj.depends_on)}  \n")
            else:
                md.write(f"- **Depends on**: *(none)*  \n")
            md.write("\n")
        md.write("\n## Dependency Graph Summary  \n")
        md.write(f"- **Total nodes**: {G.number_of_nodes()}  \n")
        md.write(f"- **Total edges**: {G.number_of_edges()}  \n\n")
        md.write("*(See `dependency_graph.json` for full adjacency list.)*\n")
