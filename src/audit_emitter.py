from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Protocol
import json


class AuditEmitter(Protocol):
    """Boundary for sending audit events somewhere outside the agent logic."""

    def emit(self, event: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        ...


class InMemoryAuditEmitter:
    """In-memory emitter that keeps audit events inside request state."""

    backend_name = "in_memory"

    def emit(self, event: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        state["audit"] = state.get("audit", []) + [event]
        return state


class JsonlAuditEmitter:
    """
    Local JSONL audit emitter.

    This is not production-grade immutable storage, but it makes it clear how the
    same audit event shape could be emitted to Kafka, EventHub, SIEM, or GRC.
    """

    backend_name = "jsonl_file"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        line = json.dumps(event, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        state["audit"] = state.get("audit", []) + [event]
        return state


def get_audit_emitter(state: Dict[str, Any]) -> AuditEmitter:
    """
    Resolve the audit backend.

    Avoid global mutable emitters. Each request can choose its own emitter through
    state['runtime']['audit_backend'].
    """

    runtime = state.get("runtime", {})
    backend = runtime.get("audit_backend", "in_memory")
    if backend == "jsonl_file":
        return JsonlAuditEmitter(runtime.get("audit_path", "outputs/audit_events.jsonl"))
    return InMemoryAuditEmitter()


def to_jsonl(audit_records: List[Dict[str, Any]]) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in audit_records)
