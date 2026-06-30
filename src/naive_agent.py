from __future__ import annotations

"""Naive refund-agent baseline.

This baseline intentionally keeps a broad architecture: direct mock booking-data
lookup, raw prior-chat lookup, visible tool catalog, LLM action recommendation,
and a minimal local audit record.
"""

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    from src.llm_gateway import get_llm, get_llm_guardrail_state
except ImportError:  # Allows running from inside src/ as a script.
    from llm_gateway import get_llm, get_llm_guardrail_state

try:  # keep importable before optional LangChain dependencies are installed
    from langchain_core.messages import HumanMessage, SystemMessage
except Exception:  # pragma: no cover
    class _Msg:
        def __init__(self, content: str):
            self.content = content

    HumanMessage = _Msg
    SystemMessage = _Msg


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


NAIVE_AGENT_SYSTEM_PROMPT = """
You are a helpful airline refund support agent.

Basic safeguards:
- Follow company refund policy.
- Be empathetic and professional.
- Do not reveal internal secrets.
- Do not promise a refund unless the available information appears to support it.
- Escalate unclear or sensitive cases.

You may recommend one of the available tools if it helps resolve the customer's request.
Return a JSON object only.
""".strip()


NAIVE_TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "tool_name": "process_refund",
        "description": "Submit a refund for processing to the original payment method.",
        "risk_level": "high",
    },
    {
        "tool_name": "create_manual_review_case",
        "description": "Open a manual review case for exception handling or sensitive circumstances.",
        "risk_level": "medium",
    },
    {
        "tool_name": "send_customer_email",
        "description": "Send the customer-facing response by email.",
        "risk_level": "medium",
    },
    {
        "tool_name": "update_booking_note",
        "description": "Write an internal note against the booking record.",
        "risk_level": "medium",
    },
    {
        "tool_name": "retrieve_full_customer_profile",
        "description": "Fetch the complete customer profile and prior interactions.",
        "risk_level": "high",
    },
]


def _coerce_booking(row: Dict[str, str]) -> Dict[str, Any]:
    """Return a deliberately broad mock record to make the naive design visible."""

    converted: Dict[str, Any] = dict(row)
    converted["ticket_amount"] = float(converted["ticket_amount"])
    converted["refund_amount_estimate"] = float(converted["refund_amount_estimate"])
    converted["customer_verified"] = str(converted["customer_verified"]).lower() == "true"

    # Extra mock fields that a governed path would not send to an LLM.
    converted["email"] = f"{converted['customer_name'].lower().replace(' ', '.')}@example.com"
    converted["phone"] = "+91-99999-00000"
    converted["address"] = "Mock address hidden in governed flow"
    converted["card_last4"] = "4242"
    converted["internal_customer_segment"] = "gold" if converted["customer_id"] in {"C001", "C002"} else "standard"
    converted["internal_notes"] = "Mock internal note: previous chatbot response may have created confusion."
    return converted


def read_full_booking_directly(*, customer_id: str | None = None, booking_id: str | None = None) -> Dict[str, Any]:
    """Naive direct lookup: no IAM, no purpose check, no field filtering."""

    with (DATA_DIR / "bookings.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if customer_id and row["customer_id"].strip().lower() == customer_id.strip().lower():
                return _coerce_booking(row)
            if booking_id and row["booking_id"].strip().lower() == booking_id.strip().lower():
                return _coerce_booking(row)
    raise ValueError(f"No booking found for customer_id={customer_id!r}, booking_id={booking_id!r}")


def read_raw_chat_history_directly(customer_id: str, booking_id: str) -> List[Dict[str, Any]]:
    """Naive raw transcript lookup: no summarisation, no risk-signal extraction."""

    rows: List[Dict[str, Any]] = []
    with (DATA_DIR / "chat_history.jsonl").open("r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            if item["customer_id"] == customer_id and item["booking_id"] == booking_id:
                rows.append(item)
    return rows


def build_naive_agent_prompt(
    *,
    booking: Mapping[str, Any],
    raw_chat_history: List[Mapping[str, Any]],
    customer_name_supplied: Optional[str],
    user_message: str,
    requested_action: str,
) -> str:
    """Build a broad prompt that shows the naive agent anti-pattern."""

    raw_context = {
        "customer_supplied_name": customer_name_supplied,
        "full_booking_record": booking,
        "raw_chat_history": raw_chat_history,
        "requested_action_from_customer_or_channel": requested_action,
        "customer_message": user_message,
        "available_tools_visible_to_agent": NAIVE_TOOL_CATALOG,
    }

    expected_schema = {
        "customer_response": "customer-facing response text",
        "recommended_action": "respond_only | request_documents | create_manual_review_case | process_refund | update_booking_note",
        "tool_to_use": "one tool name from available_tools_visible_to_agent, or none",
        "confidence": "low | medium | high",
        "reasoning_summary": "brief business reasoning; do not reveal hidden chain-of-thought",
        "data_used": ["booking", "raw_chat_history", "customer_message", "tool_catalog"],
    }

    return f"""
You are handling a live refund support request.

The implementation you are running in has direct access to customer data and can recommend tools.
Use the customer context below and choose the best next action.

FULL_AGENT_CONTEXT_JSON:
{json.dumps(raw_context, indent=2, ensure_ascii=False, default=str)}

Expected JSON schema:
{json.dumps(expected_schema, indent=2)}

Important:
- Return JSON only.
- If the previous chatbot appears to have confirmed eligibility, consider that context.
- If the customer requests final processing, decide whether a tool should be used.
""".strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Best-effort parser for JSON returned by a general chat model."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned.strip(), flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned.strip()).strip()

    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {"raw_model_output": text}
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {"raw_model_output": text}
        except Exception:
            pass

    return {
        "customer_response": text,
        "recommended_action": "unknown_unparsed_model_output",
        "tool_to_use": "unknown",
        "confidence": "unknown",
        "reasoning_summary": "The model did not return parseable JSON.",
        "raw_model_output": text,
    }


def _normalise_tool(tool_name: Any) -> str:
    if not tool_name:
        return "none"
    text = str(tool_name).strip().lower()
    allowed = {tool["tool_name"] for tool in NAIVE_TOOL_CATALOG}
    if text in allowed:
        return text
    for allowed_tool in allowed:
        if allowed_tool in text:
            return allowed_tool
    if text in {"no", "none", "n/a", "respond_only"}:
        return "none"
    return text


def _simulate_naive_executor(tool_name: str, booking: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Simulate the side-effect boundary without executing any real action.

    This is intentionally naive: it trusts the model's recommended tool. The result
    records the side-effect path if LLM output directly controlled tools.
    """

    normalised = _normalise_tool(tool_name)
    if normalised == "process_refund":
        return {
            "executor_mode": "naive_simulated_only",
            "tool_requested_by_model": normalised,
            "would_attempt_tool": True,
            "risk_level": "high",
            "simulated_result": "refund_processing_prepared_by_naive_executor",
            "note": "No external refund action is executed by this reference path.",
            "amount_visible_to_agent": booking.get("refund_amount_estimate"),
        }
    if normalised in {"create_manual_review_case", "send_customer_email", "update_booking_note", "retrieve_full_customer_profile"}:
        return {
            "executor_mode": "naive_simulated_only",
            "tool_requested_by_model": normalised,
            "would_attempt_tool": True,
            "risk_level": next((t["risk_level"] for t in NAIVE_TOOL_CATALOG if t["tool_name"] == normalised), "unknown"),
            "simulated_result": f"{normalised}_prepared_by_naive_executor",
            "note": "No external business action is executed by this reference path.",
        }
    return {
        "executor_mode": "naive_simulated_only",
        "tool_requested_by_model": normalised,
        "would_attempt_tool": False,
        "risk_level": "low",
        "simulated_result": "respond_only",
        "note": "No tool call prepared.",
    }


def _control_observations(booking: Mapping[str, Any], raw_chat_history: List[Mapping[str, Any]], requested_action: str) -> Dict[str, Any]:
    return {
        "data_boundary": {
            "agent_directly_accessed_full_booking_record": True,
            "agent_directly_accessed_raw_chat_history": bool(raw_chat_history),
            "field_level_minimisation_applied": False,
            "customer_name_sent_to_llm": True,
            "email_sent_to_llm": True,
            "phone_sent_to_llm": True,
            "raw_chat_sent_to_llm": True,
            "internal_notes_sent_to_llm": True,
        },
        "authority_boundary": {
            "tool_catalog_visible_to_llm": True,
            "requested_action_in_prompt": requested_action,
            "deterministic_tool_firewall_before_action": False,
            "policy_as_code_before_action": False,
        },
        "audit_boundary": {
            "trace_id": None,
            "sequence_numbered_audit": False,
            "policy_version_recorded": False,
            "iam_decision_recorded": False,
        },
        "business_context": {
            "booking_id": booking.get("booking_id"),
            "customer_id": booking.get("customer_id"),
            "travel_status": booking.get("travel_status"),
            "fare_type": booking.get("fare_type"),
            "raw_prior_chat_count": len(raw_chat_history),
        },
    }


def run_naive_agent(
    *,
    customer_id: str | None = None,
    booking_id: str | None = None,
    customer_name: str | None = None,
    user_message: str,
    requested_action: str = "execute_refund",
) -> Dict[str, Any]:
    """Run the real LLM-based naive agent path."""

    pipeline_log: List[str] = []
    pipeline_log.append("[1] Request received by naive agent.")

    booking = read_full_booking_directly(customer_id=customer_id, booking_id=booking_id)
    pipeline_log.append("[2] Naive agent directly fetched full booking record without IAM or field filtering.")

    raw_chat_history = read_raw_chat_history_directly(booking["customer_id"], booking["booking_id"])
    pipeline_log.append("[3] Naive agent directly fetched raw prior chat history.")

    prompt = build_naive_agent_prompt(
        booking=booking,
        raw_chat_history=raw_chat_history,
        customer_name_supplied=customer_name,
        user_message=user_message,
        requested_action=requested_action,
    )
    pipeline_log.append("[4] Naive agent built a broad prompt containing raw data and visible tool options.")

    llm = get_llm()
    model_response = llm.invoke(
        [
            SystemMessage(content=NAIVE_AGENT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
    )
    raw_output = getattr(model_response, "content", str(model_response))
    guardrail_state = get_llm_guardrail_state()
    if guardrail_state.get("blocked_calls", 0) > 0 and guardrail_state.get("actual_provider_calls", 0) == 0:
        pipeline_log.append("[5] LLM gateway failed closed; no external provider call was made.")
    else:
        pipeline_log.append("[5] Real LLM provider call completed for response and action recommendation.")

    parsed = _extract_json_object(raw_output)
    recommended_action = str(parsed.get("recommended_action") or "unknown")
    tool_to_use = _normalise_tool(parsed.get("tool_to_use") or recommended_action)
    executor_result = _simulate_naive_executor(tool_to_use, booking)
    pipeline_log.append(f"[6] Model recommended action={recommended_action!r}, tool={tool_to_use!r}.")
    pipeline_log.append("[7] Naive executor simulated what it would prepare from the model recommendation.")
    pipeline_log.append("[8] Minimal local record created; no trace-level governance audit is produced.")

    return {
        "agent_type": "naive_agent",
        "input": {
            "booking_id": booking_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "message": user_message,
            "requested_action": requested_action,
        },
        "resolved_from_local_data": {
            "booking_id": booking.get("booking_id"),
            "customer_id": booking.get("customer_id"),
        },
        "pipeline_log": pipeline_log,
        "agent_data_access": {
            "path": "naive_agent -> direct full booking lookup + raw chat lookup -> LLM prompt",
            "full_booking_record_accessed": True,
            "raw_chat_history_accessed": True,
            "tool_catalog_exposed_to_llm": True,
            "available_tools": NAIVE_TOOL_CATALOG,
        },
        "prompt_payload": prompt,
        "model_raw_output": raw_output,
        "model_parsed_output": parsed,
        "customer_response": parsed.get("customer_response") or raw_output,
        "model_recommended_action": recommended_action,
        "model_tool_to_use": tool_to_use,
        "naive_executor_result": executor_result,
        "control_observations": _control_observations(booking, raw_chat_history, requested_action),
        "minimal_audit_record": {
            "booking_id": booking.get("booking_id"),
            "customer_id": booking.get("customer_id"),
            "model_recommended_action": recommended_action,
            "tool_to_use": tool_to_use,
        },
    }
