# === yaml_agent/identifier_extractor.py ===

import os
from typing import Optional, Dict, Any, List
import yaml

def _get_qinfo(yaml_dict: Dict[str, Any]) -> Dict[str, Any]:
    # … (no change here – same as before) …
    if isinstance(yaml_dict.get("qInfo"), dict):
        return yaml_dict["qInfo"]
    props = yaml_dict.get("Properties", {}) or {}
    if isinstance(props.get("qInfo"), dict):
        return props["qInfo"]
    return {}


def is_dimension(yaml_dict: Dict[str, Any]) -> bool:
    """
    A dimension object has qInfo.qType == 'dimension' and a 'qDim' block under Properties.
    """
    qi = _get_qinfo(yaml_dict)
    if qi.get("qType") != "dimension":
        return False

    props = yaml_dict.get("Properties", {}) or {}
    return isinstance(props.get("qDim"), dict)


def is_master_measure(yaml_dict: Dict[str, Any]) -> bool:
    """
    A master measure (or masterobject) has qInfo.qType in ('measure','mastermeasure','masterobject')
    and qHyperCubeDef.qMeasures is a list (possibly empty for a masterobject container).
    """
    qi = _get_qinfo(yaml_dict)
    if qi.get("qType") not in ("measure", "mastermeasure", "masterobject"):
        return False

    hcd = yaml_dict.get("qHyperCubeDef", {}) or {}
    return isinstance(hcd.get("qMeasures"), list)


def is_variable(yaml_dict: Dict[str, Any], file_path: str) -> bool:
    """
    A variable object either:
      • Explicitly has qInfo.qType == 'variable' and a qDefinition/definition, or
      • Lives under a folder named 'Variables' and has a top‐level Name + qDefinition.
    """
    qi = _get_qinfo(yaml_dict)
    props = yaml_dict.get("Properties", {}) or {}

    # Case A: explicit qInfo.qType="variable"
    if qi.get("qType") == "variable":
        return ("qDefinition" in props) or ("Definition" in yaml_dict)

    # Case B: no qInfo, but file sits under "Variables" folder
    parent_folder = os.path.basename(os.path.dirname(file_path)).lower()
    if parent_folder == "variables" and (("qDefinition" in props) or ("Definition" in yaml_dict)):
        return True

    return False


def is_sheet(yaml_dict: Dict[str, Any]) -> bool:
    """
    A sheet/page object has qInfo.qType == 'sheet'.  We do not assume an inline sheetObjects dict,
    because QOps exports a separate "Widgets" folder instead.
    """
    qi = _get_qinfo(yaml_dict)
    return qi.get("qType") == "sheet"


def is_widget_instance(yaml_dict: Dict[str, Any]) -> bool:
    """
    A widget instance (chart) has either:
      • qInfo.qType in ('visualization','object'), or
      • qType that starts with "Vizlib", or
      • a 'visualization' or 'template' key under Properties.
    """
    qi = _get_qinfo(yaml_dict)
    qtype = qi.get("qType", "")
    props = yaml_dict.get("Properties", {}) or {}

    if qtype in ("visualization", "object"):
        return True
    if qtype.startswith("Vizlib"):
        return True
    return ("visualization" in props) or ("template" in props)


def extract_dimension_info(yaml_dict: Dict[str, Any], file_path: str) -> Dict[str, Any]:
    """
    Return a dict describing a Dimension node:
      - obj_id: from qInfo.qId
      - type_name: "YAML_Dimension"
      - fields: keys under Properties.qDim
      - file_path: path to this YAML
    """
    qi = _get_qinfo(yaml_dict)
    props = yaml_dict.get("Properties", {}) or {}
    qdim = props.get("qDim", {}) or {}

    return {
        "obj_id": qi.get("qId"),
        "type_name": "YAML_Dimension",
        "fields": list(qdim.keys()),
        "file_path": file_path
    }


def extract_master_measure_info(yaml_dict: Dict[str, Any], file_path: str) -> List[Dict[str, Any]]:
    """
    If qHyperCubeDef.qMeasures is empty but qType="masterobject", return a single container entry.
    Otherwise, return one dict per measure under qHyperCubeDef.qMeasures.
    Each dict contains:
      - obj_id: measure.qInfo.qId (or fallback to qLibraryId or qMetaDef.title)
      - type_name: "YAML_MasterMeasure"
      - fields: []  (we parse the expression later if needed)
      - file_path
    """
    hcd = yaml_dict.get("qHyperCubeDef", {}) or {}
    measures = hcd.get("qMeasures", []) or []
    result: List[Dict[str, Any]] = []

    if not measures:
        qi = _get_qinfo(yaml_dict)
        return [{
            "obj_id": qi.get("qId"),
            "type_name": "YAML_MasterObject",
            "fields": list((yaml_dict.get("Properties") or {}).keys()),
            "file_path": file_path
        }]

    for m in measures:
        mi_qi = m.get("qInfo", {}) or {}
        mi_meta = m.get("qMetaDef", {}) or {}
        mid = mi_qi.get("qId") or m.get("qLibraryId") or mi_meta.get("title")

        result.append({
            "obj_id": mid,
            "type_name": "YAML_MasterMeasure",
            "definition": m.get("qDef", ""),
            "fields": [],
            "file_path": file_path
        })

    return result


def extract_variable_info(yaml_dict: Dict[str, Any], file_path: str) -> Dict[str, Any]:
    """
    Return a dict describing a Variable:
      - obj_id: from top-level "Name" or "<UnnamedVariable>"
      - type_name: "YAML_Variable"
      - expression: either Properties.qDefinition or top-level Definition
      - fields: []
      - file_path
    """
    name = yaml_dict.get("Name") or "<UnnamedVariable>"
    expr = yaml_dict.get("Definition") or (yaml_dict.get("Properties", {}) or {}).get("qDefinition", "")
    return {
        "obj_id": name,
        "type_name": "YAML_Variable",
        "expression": expr,
        "fields": [],
        "file_path": file_path
    }


def extract_sheet_info(yaml_dict: Dict[str, Any], file_path: str) -> Dict[str, Any]:
    """
    Return a dict describing a Sheet:
      - obj_id: from SheetProperties.Properties.qInfo.qId (or fallback to SheetProperties.Id)
      - type_name: "YAML_Sheet"
      - fields: ["sheetObjects"]
      - sheet_objects: list of each widget’s qId found under the “Widgets” subfolder
      - file_path
    """
    sp = yaml_dict.get("SheetProperties", {}) or {}
    props = sp.get("Properties", {}) or {}
    qi = props.get("qInfo", {}) or {}
    pid = qi.get("qId") or sp.get("Id") or "<UnnamedSheet>"

    # Look for a “Widgets” folder in the same directory as sheet.yaml
    sheet_folder = os.path.dirname(file_path)
    widgets_dir = os.path.join(sheet_folder, "Widgets")
    sheet_objs: List[str] = []

    if os.path.isdir(widgets_dir):
        for child in os.listdir(widgets_dir):
            widget_yaml = os.path.join(widgets_dir, child, "widget.yaml")
            if os.path.isfile(widget_yaml):
                try:
                    wdata = yaml.safe_load(open(widget_yaml, "r", encoding="utf-8"))
                    wqi = _get_qinfo(wdata)
                    wid = wqi.get("qId") or wdata.get("Id") or child
                    sheet_objs.append(wid)
                except Exception:
                    continue

    return {
        "obj_id": pid,
        "type_name": "YAML_Sheet",
        "fields": ["sheetObjects"],
        "sheet_objects": sheet_objs,
        "file_path": file_path
    }

def extract_widget_info(yaml_dict: Dict[str, Any], file_path: str) -> Dict[str, Any]:
    """
    Return a dict describing a Widget:
      - obj_id: from qInfo.qId (or fallback to Id or Name)
      - type_name: "YAML_Widget"
      - fields: top-level keys under Properties
      - visualization: Properties.visualization (extension name)
      - master_ref: Properties.template or templateName or qExtendsId
      - file_path
      - depends_on: [any qLibraryId found under qHyperCubeDef.qMeasures]
    """
    qi = _get_qinfo(yaml_dict)
    wid = qi.get("qId") or yaml_dict.get("Id") or yaml_dict.get("Name") or "<UnnamedWidget>"
    props = yaml_dict.get("Properties", {}) or {}

    # existing fields + visualization + master_ref
    base = {
        "obj_id": wid,
        "type_name": "YAML_Widget",
        "fields": list(props.keys()),
        "visualization": props.get("visualization", ""),
        "master_ref": props.get("template") or props.get("templateName") or props.get("qExtendsId"),
        "file_path": file_path
    }

    # NEW: dig into qHyperCubeDef.qMeasures to catch any qLibraryId → this widget depends on that measure
    depends = []
    qh = props.get("qHyperCubeDef", {}) or {}
    measures = qh.get("qMeasures", [])
    if isinstance(measures, list):
        for m in measures:
            lib = m.get("qLibraryId")
            if isinstance(lib, str) and lib:
                depends.append(lib)

    base["depends_on"] = depends
    return base

def classify_and_extract(
    yaml_dict: Dict[str, Any],
    file_path: str,
    is_root: bool
) -> Optional[Dict[str, Any] or List[Dict[str, Any]]]:
    """
    Only attempt classification if is_root=True.  Otherwise immediately return None.

    Classification rules (in order):
      1)  If not is_root or yaml_dict is not a dict, return None.
      2)  If parent_folder == "variables" and is_variable(...), return extract_variable_info(...).
      3)  If filename == "dimension.yaml" and is_dimension(...), return extract_dimension_info(...).
      4)  If filename == "measure.yaml" and is_master_measure(...), return extract_master_measure_info(...).
      5)  If filename == "masterobject.yaml" and is_master_measure(...), register a YAML_MasterObject.
      6)  If filename == "widget.yaml" and is_widget_instance(...), return extract_widget_info(...).
      7)  If filename == "sheet.yaml" and is_sheet(...), return extract_sheet_info(...).
      8)  Otherwise (fallback at root only): return a generic object, type="YAML_<ParentFolderCapitalized>",
          with obj_id from qInfo.qId or Id or filename (without extension), and fields from top-level Properties.
    """
    # 1) Must be the very top‐level dict in the file
    if not is_root:
        return None

    if not isinstance(yaml_dict, dict):
        return None

    fname = os.path.basename(file_path).lower()
    parent_folder = os.path.basename(os.path.dirname(file_path)).lower()

    # 2) Variables folder → YAML_Variable
    if parent_folder == "variables" and is_variable(yaml_dict, file_path):
        return extract_variable_info(yaml_dict, file_path)

    # 3) dimension.yaml
    if fname == "dimension.yaml" and is_dimension(yaml_dict):
        return extract_dimension_info(yaml_dict, file_path)

    # 4) measure.yaml
    if fname == "measure.yaml" and is_master_measure(yaml_dict):
        return extract_master_measure_info(yaml_dict, file_path)

    # 5) masterobject.yaml
    if fname == "masterobject.yaml" and is_master_measure(yaml_dict):
        qi = _get_qinfo(yaml_dict)
        return {
            "obj_id": qi.get("qId"),
            "type_name": "YAML_MasterObject",
            "fields": list((yaml_dict.get("Properties") or {}).keys()),
            "file_path": file_path
        }

    # 6) widget.yaml
    if fname == "widget.yaml" and is_widget_instance(yaml_dict):
        return extract_widget_info(yaml_dict, file_path)

    # 7) sheet.yaml
    if fname == "sheet.yaml" and is_sheet(yaml_dict):
        return extract_sheet_info(yaml_dict, file_path)

    # 8) FALLBACK at root only: generic "YAML_<ParentFolder>"
    raw_qi = _get_qinfo(yaml_dict)
    fallback_id = (
        raw_qi.get("qId")
        or yaml_dict.get("Id")
        or os.path.splitext(os.path.basename(file_path))[0]
    )
    type_name = f"YAML_{parent_folder.capitalize()}"
    props = yaml_dict.get("Properties", {}) or {}
    fields = list(props.keys()) if isinstance(props, dict) else []

    return {
        "obj_id": fallback_id,
        "type_name": type_name,
        "fields": fields,
        "file_path": file_path
    }
