from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from src.audit_emitter import get_audit_emitter, to_jsonl as _to_jsonl
from src.context import ensure_context


class AuditLogger:
    """
    Structured audit logger with trace and sequence metadata.

    Demo mode still stores audit events in state['audit'], but the record shape is
    production-shaped: each event has trace_id, request_id, sequence_number,
    workflow version, actor/step, and details. This makes concurrent requests
    reconstructable even if events are physically interleaved.
    """

    @staticmethod
    def append(state: Dict[str, Any], step: str, event_type: str, details: Dict[str, Any]) -> Dict[str, Any]:
        context = ensure_context(state)
        sequence_number = int(state.get("audit_sequence", 0)) + 1
        state["audit_sequence"] = sequence_number

        record = {
            "event_id": f"EVT-{uuid4().hex[:12].upper()}",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "trace_id": context.get("trace_id"),
            "request_id": context.get("request_id"),
            "case_id": context.get("case_id") or state.get("action_result", {}).get("case_id"),
            "sequence_number": sequence_number,
            "workflow_name": context.get("workflow_name"),
            "workflow_version": context.get("workflow_version"),
            "tenant_id": context.get("tenant_id"),
            "channel": context.get("channel"),
            "container_id": context.get("container_id"),
            "step": step,
            "event_type": event_type,
            "details": details,
        }
        emitter = get_audit_emitter(state)
        state = emitter.emit(record, state)
        state["last_audit_event"] = record
        return state

    @staticmethod
    def to_jsonl(audit_records) -> str:
        return _to_jsonl(audit_records)
