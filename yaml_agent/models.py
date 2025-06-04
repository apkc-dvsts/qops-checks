# === yaml_dependency_agent/yaml_agent/models.py ===

from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class BaseObject:
    """
    Represents any YAML-derived object (Dimension, MasterMeasure, Variable, Sheet, Widget, etc.).
    """
    obj_id: str                   # e.g. qInfo.qId or generated ID
    node_type: str                # e.g. “YAML_Dimension” or “YAML_MasterMeasure”
    file_path: str                # path to the YAML
    fields: List[str]             # top-level field names (for schema inference)
    depends_on: List[str] = field(default_factory=list)
    raw_yaml: Optional[Dict] = None   # <<< add this line
    # (list of other obj_ids this object depends on)

@dataclass
class Repository:
    """
    Holds all parsed BaseObjects across YAMLs, keyed by obj_id.
    """
    objects: Dict[str, BaseObject] = field(default_factory=dict)

    def add_object(self, obj: BaseObject):
        self.objects[obj.obj_id] = obj

    def find_by_id(self, obj_id: str) -> Optional[BaseObject]:
        return self.objects.get(obj_id)
