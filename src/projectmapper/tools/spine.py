"""Single-file append-only spine: schema and entry records in one .jsonl stream.

Schema record:
    {"kind": "schema", "id": "x", "version": 1, "definition": {...}, "ts": "..."}

Entry record:
    {"kind": "entry", "schema": "x@1", "ts": "...", "data": {...}}

Opening a spine reads the stream and rebuilds the in-memory schema registry.
The stream is authoritative; the in-memory registry is derived from it.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class SpineError(Exception):
    pass


class Spine:
    """A named .jsonl file containing interleaved schema and entry records."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._schemas: dict = {}  # "id@version" → definition dict

    # ── constructors ──────────────────────────────────────────────────────

    @classmethod
    def create(cls, path: Path) -> "Spine":
        """Create a new empty spine file at *path*.

        Creates parent directories as needed.
        Raises SpineError if the file already exists.
        """
        path = Path(path)
        if path.exists():
            raise SpineError(f"File already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return cls(path)

    @classmethod
    def open(cls, path: Path) -> "Spine":
        """Open an existing spine, rebuilding its in-memory schema registry.

        Raises SpineError if the file does not exist.
        """
        path = Path(path)
        if not path.is_file():
            raise SpineError(f"No spine file found at: {path}")
        spine = cls(path)
        spine._reload()
        return spine

    @classmethod
    def exists(cls, path: Path) -> bool:
        """Return True if a spine file exists at *path*."""
        return Path(path).is_file()

    # ── identity ──────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    @property
    def name(self) -> str:
        """Human-readable name — the file stem (e.g. 'dev-log' from 'dev-log.jsonl')."""
        return self._path.stem

    def rename(self, new_name: str) -> None:
        """Rename the spine file on disk; updates this object's path.

        *new_name* is the stem only; '.jsonl' is appended automatically.
        """
        new_name = new_name.strip()
        if not new_name:
            raise SpineError("Name must not be empty")
        if new_name.endswith(".jsonl"):
            new_name = new_name[:-6]
        filename = new_name + ".jsonl"
        new_path = self._path.parent / filename
        if new_path.exists() and new_path.resolve() != self._path.resolve():
            raise SpineError(f"A file already exists at: {new_path}")
        self._path.rename(new_path)
        self._path = new_path

    # ── internal ──────────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._schemas = {}
        for record in self._iter_records():
            if record.get("kind") == "schema":
                sid = record.get("id", "")
                ver = record.get("version", 0)
                if sid and isinstance(ver, int):
                    self._schemas[f"{sid}@{ver}"] = record.get("definition", {})

    def _iter_records(self):
        if not self._path.exists():
            return
        try:
            text = self._path.read_text("utf-8")
        except OSError as exc:
            log.warning("Cannot read spine %s: %s", self._path, exc)
            return
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("Skipping malformed line %d in %s: %s", i, self._path, exc)

    def _append_record(self, record: dict) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── schema registry ───────────────────────────────────────────────────

    def register_schema(self, schema_def: dict) -> str:
        """Append a schema record and return its 'id@version' key.

        Raises SpineError if that exact id@version is already registered
        (schema versions are immutable once written).
        """
        if not isinstance(schema_def, dict):
            raise SpineError("schema_def must be a dict")
        sid = schema_def.get("id")
        ver = schema_def.get("version")
        if not sid or not isinstance(sid, str):
            raise SpineError("schema_def must have a non-empty string 'id'")
        if not isinstance(ver, int) or ver < 1:
            raise SpineError("schema_def 'version' must be a positive integer")
        key = f"{sid}@{ver}"
        if key in self._schemas:
            raise SpineError(f"Schema '{key}' is already registered and immutable")
        self._append_record({
            "kind": "schema",
            "id": sid,
            "version": ver,
            "definition": schema_def,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        self._schemas[key] = schema_def
        return key

    def list_schemas(self) -> list:
        """Return all registered schema keys in registration order."""
        return list(self._schemas.keys())

    def load_schema(self, key: str) -> dict:
        """Return the definition for a registered schema key."""
        if key not in self._schemas:
            raise SpineError(f"Schema '{key}' not found")
        return self._schemas[key]

    # ── entries ───────────────────────────────────────────────────────────

    def append_entry(self, schema_key: str, data: dict) -> None:
        """Append an entry record. Raises SpineError if schema is not registered."""
        if schema_key not in self._schemas:
            raise SpineError(f"Schema '{schema_key}' not registered")
        self._append_record({
            "kind": "entry",
            "schema": schema_key,
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })

    def read_entries(self) -> list:
        """Return all entry records in stream order."""
        return [r for r in self._iter_records() if r.get("kind") == "entry"]

    def read_all(self) -> list:
        """Return every record in stream order (schema + entry)."""
        return list(self._iter_records())
