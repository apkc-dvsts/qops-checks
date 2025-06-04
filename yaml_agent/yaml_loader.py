# === yaml_dependency_agent/yaml_agent/yaml_loader.py ===

import yaml
import logging

def load_yaml_file(file_path: str):
    """
    Loads a YAML file into a Python dict. If the file contains tabs,
    they are replaced with two spaces before parsing. Returns the parsed dict, or None on failure.
    """
    logger = logging.getLogger(__name__)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except Exception as e:
        logger.warning(f"Could not open '{file_path}': {e}")
        return None

    # 1) Replace tab characters with spaces (YAML forbids raw tabs)
    if "\t" in raw_text:
        logger.debug(f"Replacing tabs with spaces in '{file_path}'")
        raw_text = raw_text.replace("\t", "  ")

    # 2) Parse YAML
    try:
        data = yaml.safe_load(raw_text)
        return data
    except yaml.YAMLError as ye:
        logger.warning(f"Could not parse YAML '{file_path}': {ye}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error parsing '{file_path}': {e}")
        return None
