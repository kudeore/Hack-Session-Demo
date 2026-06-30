from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
import socket
from typing import Any, Dict, Optional
from uuid import uuid4


@dataclass(frozen=True)
class RequestContext:
    """
    Per-request context that travels through the workflow.

    This keeps concurrent requests separated even when the same container handles
    many requests at the same time. In production, several fields would be
    supplied by the API gateway, identity platform, or orchestration layer.
    """

    trace_id: str
    request_id: str
    case_id: Optional[str]
    idempotency_key: str
    workflow_name: str
    workflow_version: str
    channel: str
    tenant_id: Optional[str]
    received_at_utc: str
    container_id: str

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


class RequestContextFactory:
    """Create a production-shaped context for each incoming request."""

    @staticmethod
    def create(
        *,
        customer_id: str,
        user_message: str,
        requested_action: str,
        booking_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        workflow_name: str = "governed_refund_agent",
        workflow_version: str = "runtime-guardrails-2026.06",
        channel: str = "cli",
        tenant_id: Optional[str] = "reference_airline",
        case_id: Optional[str] = None,
    ) -> RequestContext:
        trace_id = f"TRC-{uuid4().hex[:12].upper()}"
        request_id = f"REQ-{uuid4().hex[:12].upper()}"
        received_at = datetime.now(timezone.utc).isoformat()
        idempotency_material = "|".join(
            [
                workflow_name,
                workflow_version,
                tenant_id or "",
                customer_id or "UNKNOWN",
                booking_id or "UNKNOWN_BOOKING",
                requested_action,
                user_message.strip().lower(),
            ]
        )
        idempotency_key = "IDEM-" + sha256(idempotency_material.encode("utf-8")).hexdigest()[:16].upper()
        container_id = (
            os.getenv("HOSTNAME")
            or os.getenv("POD_NAME")
            or os.getenv("CONTAINER_ID")
            or socket.gethostname()
            or "local"
        )
        return RequestContext(
            trace_id=trace_id,
            request_id=request_id,
            case_id=case_id,
            idempotency_key=idempotency_key,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            channel=channel,
            tenant_id=tenant_id,
            received_at_utc=received_at,
            container_id=container_id,
        )


def ensure_context(state: Dict[str, Any]) -> Dict[str, Any]:
    """Backwards-compatible helper for older callers that do not pass context."""

    context = state.get("context")
    if context:
        return context
    created = RequestContextFactory.create(
        customer_id=state.get("customer_id", "UNKNOWN"),
        user_message=state.get("user_message", ""),
        requested_action=state.get("requested_action", "execute_refund"),
    )
    state["context"] = created.model_dump()
    return state["context"]
