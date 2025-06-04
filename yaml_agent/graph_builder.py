# === yaml_agent/graph_builder.py ===

import networkx as nx
from yaml_agent.models import Repository

def build_dependency_graph(repo: Repository) -> nx.DiGraph:
    """
    Every obj_id in repo.objects becomes a node (with type_name, file_path).
    Add an edge (A → B) if A.depends_on contains B.
    Any object whose obj_id is None or empty will be skipped.
    """
    G = nx.DiGraph()

    # Add nodes
    for obj_id, obj in repo.objects.items():
        if not obj_id:
            # Skip any objects that ended up with obj_id = None or empty.
            continue
        G.add_node(obj_id, type_name=obj.node_type, file_path=obj.file_path)

    # Add edges
    for obj_id, obj in repo.objects.items():
        if not obj_id:
            continue
        for dep in set(obj.depends_on):
            if not dep:
                continue
            if dep in repo.objects and dep != obj_id:
                G.add_edge(obj_id, dep)
            else:
                # If dependency not in repo (or missing), create a node labeled "Unknown"
                if dep:
                    G.add_node(dep, type_name="Unknown", file_path="")
                    G.add_edge(obj_id, dep)

    return G
