# === yaml_dependency_agent/yaml_agent/knowledge_base.py ===

import sqlite3
import json
import os
import torch
from datetime import datetime
from typing import Optional, List, Dict

from sentence_transformers import SentenceTransformer, util

DB_FILENAME = "kb.sqlite3"

class KnowledgeBase:
    def __init__(self, out_dir: str, logger=None):
        """
        Connect to (or create) kb.sqlite3 under out_dir.
        Attempts to load a SentenceTransformer model in local_files_only mode.
        If that fails, fuzzy matching is disabled.
        """
        self.logger = logger or __import__("logging").getLogger(__name__)
        os.makedirs(out_dir, exist_ok=True)
        self.db_path = os.path.join(out_dir, DB_FILENAME)
        self.conn = sqlite3.connect(self.db_path)
        self._ensure_tables()

        # Try loading sentence-transformers in local-only mode
        try:
            self.embed_model = SentenceTransformer(
                r"c:\Repos\model_cache\sentence-transformers\all-MiniLM-L6-v2",
                local_files_only=True
            )
            self.logger.info("Loaded SentenceTransformer model from local cache.")
            self.fuzzy_enabled = True
        except Exception as e:
            self.logger.warning(
                f"Could not load SentenceTransformer in local-only mode: {e}\n"
                "→ Fuzzy matching disabled. Only exact matching will be used."
            )
            self.embed_model = None
            self.fuzzy_enabled = False

    def _ensure_tables(self):
        c = self.conn.cursor()
        # Table: object_types
        c.execute("""
            CREATE TABLE IF NOT EXISTS object_types (
                type_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                type_name    TEXT UNIQUE,
                fields_json  TEXT,
                emb_json     TEXT,
                created_at   TEXT
            )
        """)
        # Table: type_dependencies
        c.execute("""
            CREATE TABLE IF NOT EXISTS type_dependencies (
                dep_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id_from INTEGER,
                type_id_to   INTEGER,
                fields_json  TEXT,
                created_at   TEXT,
                UNIQUE(type_id_from, type_id_to)
            )
        """)
        self.conn.commit()

    def list_types(self) -> List[Dict]:
        c = self.conn.cursor()
        c.execute("SELECT type_id, type_name, fields_json FROM object_types")
        rows = c.fetchall()
        return [
            {"type_id": r[0], "type_name": r[1], "fields": json.loads(r[2])}
            for r in rows
        ]

    def get_type_by_name(self, type_name: str) -> Optional[Dict]:
        c = self.conn.cursor()
        c.execute(
            "SELECT type_id, fields_json, emb_json FROM object_types WHERE type_name=?",
            (type_name,)
        )
        row = c.fetchone()
        if not row:
            return None
        return {"type_id": row[0], "fields": json.loads(row[1]), "emb_json": row[2]}

    def add_type(self, type_name: str, fields: List[str], embedding: Optional[List[float]] = None):
        """
        Insert a new object_type. Normalize fields (lowercase).
        Compute embedding (if fuzzy_enabled and not provided).
        """
        c = self.conn.cursor()
        now = datetime.utcnow().isoformat()

        normalized = sorted([f.lower() for f in fields])
        emb_json = None
        if embedding is not None:
            emb_json = json.dumps(embedding)
        elif self.fuzzy_enabled and self.embed_model is not None:
            try:
                text = " ".join(normalized)
                emb_tensor = self.embed_model.encode(text, convert_to_tensor=True)
                emb_list = emb_tensor.cpu().tolist()
                emb_json = json.dumps(emb_list)
                self.logger.debug(
                    f"Computed embedding for new type '{type_name}' with fields {normalized}."
                )
            except Exception as e:
                self.logger.warning(f"Failed to compute embedding for '{type_name}': {e}")
                emb_json = None

        try:
            c.execute(
                "INSERT INTO object_types (type_name, fields_json, emb_json, created_at) VALUES (?, ?, ?, ?)",
                (type_name, json.dumps(normalized), emb_json, now)
            )
            self.conn.commit()
            self.logger.info(f"Added new type '{type_name}' with fields {normalized}.")
        except sqlite3.IntegrityError:
            self.logger.debug(f"Type '{type_name}' already exists—skipping insert.")

    def add_dependency(self, type_from: int, type_to: int, fields: List[str]):
        """
        Record that type_from depends on type_to via given field paths.
        """
        c = self.conn.cursor()
        now = datetime.utcnow().isoformat()
        fields_json = json.dumps(fields)
        try:
            c.execute(
                "INSERT INTO type_dependencies (type_id_from, type_id_to, fields_json, created_at) VALUES (?, ?, ?, ?)",
                (type_from, type_to, fields_json, now)
            )
            self.conn.commit()
            self.logger.debug(f"Added dependency: type {type_from} → type {type_to} via fields {fields}.")
        except sqlite3.IntegrityError:
            # Already exists → update
            c.execute(
                "UPDATE type_dependencies SET fields_json=?, created_at=? WHERE type_id_from=? AND type_id_to=?",
                (fields_json, now, type_from, type_to)
            )
            self.conn.commit()
            self.logger.debug(f"Updated dependency: type {type_from} → type {type_to} now via fields {fields}.")

    def find_candidate_type(self, fields: List[str], threshold: float = 0.9) -> Optional[int]:
        """
        Normalize fields, compute embedding (if possible), compare with existing embeddings,
        return best match if similarity ≥ threshold.
        """
        normalized = [f.lower() for f in fields]
        existing_types = self.list_types()
        if not existing_types or not self.fuzzy_enabled or self.embed_model is None:
            self.logger.debug("Fuzzy matching unavailable or KB empty—skipping fuzzy match.")
            return None

        new_text = " ".join(sorted(normalized))
        try:
            new_emb = self.embed_model.encode(new_text, convert_to_tensor=True)
        except Exception as e:
            self.logger.warning(f"Error encoding new fields for fuzzy match: {e}")
            return None

        best_sim, best_type = 0.0, None
        c = self.conn.cursor()
        c.execute("SELECT type_id, emb_json FROM object_types WHERE emb_json IS NOT NULL")
        rows = c.fetchall()
        for type_id, emb_json in rows:
            try:
                existing_emb_list = json.loads(emb_json)
                existing_emb = torch.tensor(existing_emb_list)
                cos_sim = util.cos_sim(new_emb, existing_emb).item()
                self.logger.debug(f"Similarity(new→type_id {type_id}) = {cos_sim:.4f}")
                if cos_sim > best_sim:
                    best_sim = cos_sim
                    best_type = type_id
            except Exception as e:
                self.logger.warning(f"Error computing similarity for type_id {type_id}: {e}")
                continue

        if best_type is not None and best_sim >= threshold:
            self.logger.info(f"Fuzzy-match: fields {normalized} → type_id {best_type} (sim={best_sim:.3f})")
            return best_type

        self.logger.debug(f"No fuzzy-match for fields {normalized} (best sim={best_sim:.3f} < {threshold})")
        return None

    def close(self):
        self.conn.close()
