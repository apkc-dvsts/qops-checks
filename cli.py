# === yaml_dependency_agent/cli.py ===

import os
import click
import logging

from yaml_agent.file_discovery import discover_app_folders
from yaml_agent.yaml_loader import load_yaml_file
from yaml_agent.knowledge_base import KnowledgeBase
from yaml_agent.dependency_finder import process_yaml_file
from yaml_agent.graph_builder import build_dependency_graph
from yaml_agent.report_generator import (
    generate_object_report,
    generate_dependency_graph_output,
    generate_markdown_report
)
from yaml_agent.schema_documenter import (
    gather_schemas_with_cardinality,
    write_schema_docs_with_cardinality
)
from yaml_agent.best_practices import (
    run_best_practices_checks,
    run_script_linter
)
from yaml_agent.models import Repository

@click.command()
@click.argument("root_dir", type=click.Path(exists=True))
@click.option("--out-dir", "-o", default="yaml_agent_output",
              help="Base directory to write all outputs (per-app and aggregated)")
@click.option("--md-report", is_flag=True, default=False,
              help="Also produce a Markdown report")
@click.option("--verbose", "-v", is_flag=True, default=False,
              help="Enable verbose (DEBUG) logging")
def analyze(root_dir, out_dir, md_report, verbose):
    """
    Multi-App: Scans ROOT_DIR for all subfolders containing App.yaml (i.e. Qlik apps),
    then for each app folder:
      - Loads every .yaml under it, infers schemas, builds dependency graph,
      - Runs best-practices checks on YAML expressions & Script.qvs,
      - Writes per-app outputs under OUT_DIR/<app_name>/…,
    Finally produces an aggregated summary under OUT_DIR/_aggregate.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S"
    )
    logger = logging.getLogger(__name__)
    logger.info(f"Starting multi-app analysis of `{root_dir}` (verbose={verbose})\n")

    # 1) Discover all app directories (those containing "App.yaml")
    app_folders = discover_app_folders(root_dir)
    if not app_folders:
        logger.error("No subfolders with App.yaml found under root. Exiting.")
        return

    logger.info(f"Found {len(app_folders)} app(s):")
    for app in app_folders:
        logger.info(f"  • {app}")

    # 2) Prepare aggregate structures
    aggregate_repo = Repository()
    aggregate_kb = KnowledgeBase(os.path.join(out_dir, "_aggregate_kb"), logger=logger)
    aggregate_graph_nodes = []
    aggregate_graph_edges = []

    # 3) Process each app separately
    for app_path in app_folders:
        app_name = os.path.basename(app_path.rstrip(os.sep))
        app_out = os.path.join(out_dir, app_name)
        os.makedirs(app_out, exist_ok=True)
        logger.info(f"\n=== Processing App: {app_name} ===")

        # 3a) Initialize per-app KB & Repository
        kb = KnowledgeBase(app_out, logger=logger)
        repo = Repository()

        # 3b) Find all YAML files under this app
        yaml_files = []
        for root, _, files in os.walk(app_path):
            for fname in files:
                if fname.lower().endswith((".yml", ".yaml")):
                    yaml_files.append(os.path.join(root, fname))
        logger.info(f"  Found {len(yaml_files)} YAML file(s) in this app.\n")

        # 3c) Process each YAML
        for yml in yaml_files:
            logger.debug(f"  Loading YAML file: {yml}")
            data = load_yaml_file(yml)
            if not data:
                logger.warning(f"  Skipping invalid or empty YAML: {yml}")
                continue
            process_yaml_file(data, yml, repo, kb, logger)

        logger.info(f"  → Total objects discovered (this app): {len(repo.objects)}")

        # 3d) Build per-app dependency graph
        G = build_dependency_graph(repo)
        logger.info(f"  → Dependency graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.\n")

        # 3e) Write per-app JSON reports
        logger.info(f"  Writing objects list to `{app_out}/all_objects.json` …")
        generate_object_report(repo, app_out)

        dep_path = os.path.join(app_out, "dependency_graph.json")
        logger.info(f"  Writing dependency graph JSON to `{dep_path}` …")
        generate_dependency_graph_output(G, dep_path)

        if md_report:
            md_path = os.path.join(app_out, "dependency_report.md")
            logger.info(f"  Writing Markdown report to `{md_path}` …")
            generate_markdown_report(G, repo, md_path)

        # 3f) Generate per-app schema documentation
        logger.info("  Generating schema documentation …")
        schema_map = gather_schemas_with_cardinality(repo)
        write_schema_docs_with_cardinality(schema_map, app_out)

        # 3g) Run best-practices checks on YAML expressions
        logger.info("  Running best-practices checks on YAML expressions …")
        bp_warnings = run_best_practices_checks(repo, app_out)
        if bp_warnings:
            logger.info(f"  → Found {len(bp_warnings)} YAML-best-practice warnings (see best_practices.yaml).")
        else:
            logger.info("  → No YAML-best-practice warnings.")

        # 3h) If Script.qvs exists, run a lightweight script linter
        script_path = os.path.join(app_path, "Script.qvs")
        if os.path.isfile(script_path):
            logger.info("  Running QVS script linter …")
            lint_warnings = run_script_linter(script_path, app_out)
            logger.info(f"  → Found {len(lint_warnings)} script linter warnings (see script_lint.yaml).")
        else:
            logger.debug("  No Script.qvs found in this app.")

        # 3i) Merge per-app into aggregate
        for obj_id, obj in repo.objects.items():
            if obj_id not in aggregate_repo.objects:
                aggregate_repo.add_object(obj)
        # (We keep separate KBs per app; we’ll only aggregate graphs here.)
        aggregate_graph_nodes.extend([(n, G.nodes[n]) for n in G.nodes()])
        aggregate_graph_edges.extend([(u, v) for u, v in G.edges()])

        # Close per-app KB
        kb.close()

    # 4) Write aggregated summary under OUT_DIR/_aggregate
    agg_out = os.path.join(out_dir, "_aggregate")
    os.makedirs(agg_out, exist_ok=True)
    logger.info("\n=== Writing aggregated summary ===")

    import networkx as nx
    AggG = nx.DiGraph()
    for n, attrs in aggregate_graph_nodes:
        AggG.add_node(n, **attrs)
    for u, v in aggregate_graph_edges:
        AggG.add_edge(u, v)

    logger.info(f"  Aggregate graph: {AggG.number_of_nodes()} nodes, {AggG.number_of_edges()} edges.")
    agg_dep_path = os.path.join(agg_out, "aggregate_dependency_graph.json")
    generate_dependency_graph_output(AggG, agg_dep_path)

    logger.info(f"  Writing aggregate objects list to `{agg_out}/all_objects.json` …")
    generate_object_report(aggregate_repo, agg_out)

    if md_report:
        md_path = os.path.join(agg_out, "dependency_report.md")
        logger.info(f"  Writing aggregate Markdown report to `{md_path}` …")
        generate_markdown_report(AggG, aggregate_repo, md_path)

    logger.info("\nMulti-app analysis complete.\n")


if __name__ == "__main__":
    analyze()
