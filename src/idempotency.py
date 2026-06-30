from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict


def build_idempotency_key(*, context: Dict[str, Any], customer_id: str, booking_id: str | None, action: str) -> str:
    """Create stable key for high-risk side effects such as handoff/case creation."""

    material = "|".join([
        context.get("tenant_id") or "",
        context.get("workflow_name") or "",
        context.get("workflow_version") or "",
        customer_id or "UNKNOWN",
        booking_id or "NO_BOOKING",
        action,
    ])
    return "IDEM-" + sha256(material.encode("utf-8")).hexdigest()[:16].upper()


def reserve_or_reuse_case(state: Dict[str, Any], *, case_id: str, action: str, booking_id: str | None = None) -> Dict[str, Any]:
    """
    Demo idempotency registry.

    In production this should be a central database table with a unique index on
    idempotency_key. It records the key in request state so audit evidence includes the
    idempotency control point.
    """

    context = state.get("context", {})
    key = build_idempotency_key(
        context=context,
        customer_id=state.get("customer_id", "UNKNOWN"),
        booking_id=booking_id,
        action=action,
    )
    registry = state.get("idempotency", {})
    existing = registry.get(key)
    if existing:
        return {
            "idempotency_key": key,
            "decision": "existing_case_reused",
            "case_id": existing["case_id"],
            "action": action,
        }

    registry[key] = {"case_id": case_id, "action": action}
    state["idempotency"] = registry
    return {
        "idempotency_key": key,
        "decision": "new_case_reserved",
        "case_id": case_id,
        "action": action,
    }
