"""Deterministic serialisation, strict JSON and exclusive evidence writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _pairs(items: list[tuple[str, Any]]) -> dict:
    value: dict = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f"Invalid JSON number: {s}")),
    )


def load(path: Path) -> Any:
    return loads(path.read_text(encoding="utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    return digest(path.read_bytes())


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def write_text(path: Path, text: str) -> None:
    """Never overwrite evidence, including when resuming a run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, data: Any) -> None:
    """Atomically publish complete JSON without overwriting an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=".pending-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(canonical(data))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def event(root: Path, kind: str, **fields: Any) -> None:
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"at": utcnow(), "kind": kind, **fields}, ensure_ascii=False, allow_nan=False)
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


class RunLock:
    """One process per experiment. A stale lock requires an explicit manual check."""

    def __init__(self, root: Path):
        self.path = root / ".runner.lock"

    def __enter__(self) -> RunLock:
        write_json(self.path, {"pid": os.getpid(), "created_at": utcnow()})
        return self

    def __exit__(self, *_: object) -> None:
        self.path.unlink()


def input_gate(raw: Any) -> tuple[Any, list[dict]]:
    from pydantic import ValidationError

    from .models import Scenario

    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        return None, [{"rule_id": "INPUT_SCHEMA", "message": str(exc)}]
    return scenario, scenario.domain_errors()
