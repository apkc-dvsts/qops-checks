# === yaml_dependency_agent/yaml_agent/file_discovery.py ===

import os
from typing import List

def discover_app_folders(root_dir: str) -> List[str]:
    """
    Walks root_dir recursively and returns every subfolder path that contains an "App.yaml" file.
    """
    app_folders = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if "App.yaml" in filenames or "App.yml" in filenames:
            app_folders.append(dirpath)
            # Don't recurse further into this app as a separate app
            dirnames[:] = []
    return app_folders
