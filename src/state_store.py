from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Protocol
import json


class WorkflowStateStore(Protocol):
    def save_checkpoint(self, state: Dict[str, Any], checkpoint_name: str) -> Dict[str, Any]:
        ...


class InMemoryStateStore:
    """In-memory checkpoint store for local execution."""

    backend_name = "in_memory"

    def save_checkpoint(self, state: Dict[str, Any], checkpoint_name: str) -> Dict[str, Any]:
        checkpoint = _build_checkpoint_record(state, checkpoint_name, self.backend_name)
        state["checkpoints"] = state.get("checkpoints", []) + [checkpoint]
        return state


class JsonFileStateStore:
    """Local checkpoint writer. Not intended as production state storage."""

    backend_name = "json_file"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, state: Dict[str, Any], checkpoint_name: str) -> Dict[str, Any]:
        checkpoint = _build_checkpoint_record(state, checkpoint_name, self.backend_name)
        trace_id = checkpoint["trace_id"]
        seq = checkpoint["audit_sequence_number"]
        path = self.directory / f"{trace_id}_{seq:03d}_{checkpoint_name}.json"
        path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")
        checkpoint["checkpoint_path"] = str(path)
        state["checkpoints"] = state.get("checkpoints", []) + [checkpoint]
        return state


def _safe_state_hash(state: Dict[str, Any]) -> str:
    """
    Hash only non-sensitive workflow metadata.

    We deliberately avoid serialising full user/customer payloads into the hash
    material for this helper.
    """

    context = state.get("context", {})
    keys = [
        "risk",
        "security_decision",
        "policy_decision",
        "firewall_decision",
        "handoff_decision",
        "action_result",
        "output_verification",
    ]
    material = {"context": context, "workflow_keys_present": sorted(k for k in keys if k in state)}
    return sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _build_checkpoint_record(state: Dict[str, Any], checkpoint_name: str, backend_name: str) -> Dict[str, Any]:
    context = state.get("context", {})
    return {
        "checkpoint_name": checkpoint_name,
        "checkpoint_backend": backend_name,
        "trace_id": context.get("trace_id"),
        "request_id": context.get("request_id"),
        "case_id": context.get("case_id") or state.get("action_result", {}).get("case_id"),
        "workflow_name": context.get("workflow_name"),
        "workflow_version": context.get("workflow_version"),
        "audit_sequence_number": state.get("audit_sequence", 0),
        "state_hash": _safe_state_hash(state),
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def get_state_store(state: Dict[str, Any]) -> WorkflowStateStore:
    runtime = state.get("runtime", {})
    backend = runtime.get("state_backend", "in_memory")
    if backend == "json_file":
        return JsonFileStateStore(runtime.get("checkpoint_dir", "outputs/checkpoints"))
    return InMemoryStateStore()


def checkpoint(state: Dict[str, Any], checkpoint_name: str) -> Dict[str, Any]:
    store = get_state_store(state)
    return store.save_checkpoint(state, checkpoint_name)
