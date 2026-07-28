from __future__ import annotations

"""Baseline refund agent.

This version represents a realistic first implementation: it reads the local
booking record, prior conversation notes, and the approved Markdown policy, then
asks the LLM to decide the customer response and next operational action.

It does not use the governed-agent controls such as adversarial screening,
field-level minimisation, IAM-mediated skill access, policy-as-code, tool
firewall checks, human handoff orchestration, output verification, trace-level
audit reconstruction, or the src.llm_gateway runtime guardrail wrapper.
"""

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from baseline_agent.llm_client import DirectLLMClient
from baseline_agent.logger import BaselineLogger

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
POLICY_DIR = ROOT / "policies"


SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"


def read_hardened_system_prompt() -> str:
    """Read the baseline prompt-level governance instructions.

    This is intentionally a prompt-only control. It is not the governed runtime
    harness and does not enforce controls outside the LLM response.
    """

    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


BASELINE_AGENT_SYSTEM_PROMPT = read_hardened_system_prompt()


ACTION_OPTIONS = [
    "respond_only",
    "request_documents",
    "create_manual_review_case",
    "prepare_refund",
    "decline_refund_with_explanation",
]


def _coerce_booking(row: Dict[str, str]) -> Dict[str, Any]:
    """Convert the booking CSV row into typed values without adding synthetic fields."""

    converted: Dict[str, Any] = dict(row)
    converted["ticket_amount"] = float(converted["ticket_amount"])
    converted["refund_amount_estimate"] = float(converted["refund_amount_estimate"])
    converted["customer_verified"] = str(converted["customer_verified"]).lower() == "true"
    return converted


def read_booking_directly(*, customer_id: str | None = None, booking_id: str | None = None) -> Dict[str, Any]:
    """Baseline direct lookup from the local booking CSV."""

    with (DATA_DIR / "bookings.csv").open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if customer_id and row["customer_id"].strip().lower() == customer_id.strip().lower():
                return _coerce_booking(row)
            if booking_id and row["booking_id"].strip().lower() == booking_id.strip().lower():
                return _coerce_booking(row)
    raise ValueError(f"No booking found for customer_id={customer_id!r}, booking_id={booking_id!r}")


# Backwards-compatible alias for notebooks/imports from the previous version.
read_full_booking_directly = read_booking_directly


def read_chat_history_directly(customer_id: str, booking_id: str) -> List[Dict[str, Any]]:
    """Read prior chat rows for the resolved booking from the local JSONL file."""

    rows: List[Dict[str, Any]] = []
    history_path = DATA_DIR / "chat_history.jsonl"
    if not history_path.exists():
        return rows
    with history_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("customer_id") == customer_id and item.get("booking_id") == booking_id:
                rows.append(item)
    return rows


# Backwards-compatible alias for notebooks/imports from the previous version.
read_raw_chat_history_directly = read_chat_history_directly


def read_approved_policy_markdown() -> Dict[str, Any]:
    """Read approved Markdown policy files used by both demo paths."""

    policy_files: List[Dict[str, str]] = []
    for path in sorted(POLICY_DIR.glob("*.md")):
        policy_files.append(
            {
                "source": path.name,
                "text": path.read_text(encoding="utf-8").strip(),
            }
        )
    if not policy_files:
        raise FileNotFoundError(f"No Markdown policy files found in {POLICY_DIR}")
    return {
        "policy_sources": [item["source"] for item in policy_files],
        "policy_text": "\n\n---\n\n".join(
            f"SOURCE: {item['source']}\n{item['text']}" for item in policy_files
        ),
    }


def build_baseline_agent_prompt(
    *,
    booking: Mapping[str, Any],
    chat_history: List[Mapping[str, Any]],
    policy_context: Mapping[str, Any],
    customer_name_supplied: Optional[str],
    user_message: str,
    requested_action: str,
) -> str:
    """Build the baseline LLM prompt using actual booking and policy context."""

    request_context = {
        "customer_supplied_name": customer_name_supplied,
        "booking_record": booking,
        "prior_communications": chat_history,
        "requested_action_from_customer_or_channel": requested_action,
        "customer_message": user_message,
        "available_action_options": ACTION_OPTIONS,
    }

    expected_schema = {
        "customer_response": "customer-facing response text",
        "recommended_action": "one of available_action_options",
        "needs_manual_review": "true | false",
        "refund_amount_to_prepare": "number or null",
        "policy_sources_used": ["policy file names used"],
        "confidence": "low | medium | high",
        "reasoning_summary": "brief business reasoning; do not reveal hidden chain-of-thought",
    }

    return f"""
You are handling a refund support request.

APPROVED_POLICY_TEXT:
{policy_context["policy_text"]}

REQUEST_CONTEXT_JSON:
{json.dumps(request_context, indent=2, ensure_ascii=False, default=str)}

Expected JSON schema:
{json.dumps(expected_schema, indent=2)}

Return JSON only. Base your response on the supplied policy text and booking context.
""".strip()


# Backwards-compatible alias for callers that used the old function name.
def build_naive_agent_prompt(
    *,
    booking: Mapping[str, Any],
    raw_chat_history: List[Mapping[str, Any]],
    customer_name_supplied: Optional[str],
    user_message: str,
    requested_action: str,
) -> str:
    return build_baseline_agent_prompt(
        booking=booking,
        chat_history=raw_chat_history,
        policy_context=read_approved_policy_markdown(),
        customer_name_supplied=customer_name_supplied,
        user_message=user_message,
        requested_action=requested_action,
    )


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
        "needs_manual_review": None,
        "refund_amount_to_prepare": None,
        "policy_sources_used": [],
        "confidence": "unknown",
        "reasoning_summary": "The model did not return parseable JSON.",
        "raw_model_output": text,
    }


def _normalise_action(action: Any) -> str:
    if not action:
        return "respond_only"
    text = str(action).strip().lower()
    for allowed in ACTION_OPTIONS:
        if text == allowed or allowed in text:
            return allowed
    if "manual" in text or "review" in text or "escalat" in text:
        return "create_manual_review_case"
    if "refund" in text and ("prepare" in text or "process" in text or "approve" in text):
        return "prepare_refund"
    if "document" in text:
        return "request_documents"
    if "decline" in text or "deny" in text or "not eligible" in text:
        return "decline_refund_with_explanation"
    return text


def _baseline_action_record(action: str, parsed: Mapping[str, Any], booking: Mapping[str, Any]) -> Dict[str, Any]:
    """Record the baseline model recommendation without executing business tools."""

    normalised = _normalise_action(action)
    refund_amount = parsed.get("refund_amount_to_prepare")
    if refund_amount is None and normalised == "prepare_refund":
        refund_amount = booking.get("refund_amount_estimate")

    return {
        "execution_mode": "baseline_recommendation_only",
        "recommended_action": normalised,
        "would_require_downstream_operation": normalised in {
            "create_manual_review_case",
            "prepare_refund",
            "request_documents",
            "decline_refund_with_explanation",
        },
        "refund_amount_to_prepare": refund_amount,
        "note": "Baseline agent records the LLM recommendation only. No refund, case, note, or email is executed by this reference path.",
    }


def _control_observations(
    booking: Mapping[str, Any],
    chat_history: List[Mapping[str, Any]],
    policy_context: Mapping[str, Any],
    requested_action: str,
) -> Dict[str, Any]:
    return {
        "baseline_capabilities": {
            "reads_actual_booking_csv": True,
            "reads_actual_policy_markdown": True,
            "policy_sources": policy_context.get("policy_sources", []),
            "reads_prior_communications": bool(chat_history),
            "uses_real_llm_call": True,
        },
        "controls_not_applied_in_baseline": {
            "adversarial_input_guard": False,
            "iam_mediated_skill_access": False,
            "field_level_minimisation": False,
            "policy_as_code_decision": False,
            "tool_firewall": False,
            "human_approval_gate": False,
            "output_verifier": False,
            "trace_level_audit": False,
            "src_llm_gateway_runtime_guardrails": False,
        },
        "business_context": {
            "booking_id": booking.get("booking_id"),
            "customer_id": booking.get("customer_id"),
            "travel_status": booking.get("travel_status"),
            "fare_type": booking.get("fare_type"),
            "requested_action": requested_action,
            "prior_communication_count": len(chat_history),
        },
    }


def run_naive_agent(
    *,
    customer_id: str | None = None,
    booking_id: str | None = None,
    customer_name: str | None = None,
    user_message: str,
    requested_action: str = "execute_refund",
    provider: str = "gemini",
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    log_dir: str | Path | None = None,
) -> Dict[str, Any]:
    """Run the real LLM-based baseline agent path."""

    logger = BaselineLogger(log_dir=log_dir)
    pipeline_log: List[str] = []
    pipeline_log.append("[1] Request received by baseline agent.")
    logger.event("request_received", customer_id=customer_id, booking_id=booking_id, requested_action=requested_action)

    booking = read_booking_directly(customer_id=customer_id, booking_id=booking_id)
    logger.event("booking_read", booking_id=booking.get("booking_id"), customer_id=booking.get("customer_id"), source="data/bookings.csv")
    pipeline_log.append("[2] Baseline agent read the booking record from the local CSV.")

    chat_history = read_chat_history_directly(booking["customer_id"], booking["booking_id"])
    logger.event("chat_history_read", record_count=len(chat_history), source="data/chat_history.jsonl")
    pipeline_log.append("[3] Baseline agent read prior communication notes for the booking.")

    policy_context = read_approved_policy_markdown()
    logger.event("policy_read", policy_sources=policy_context.get("policy_sources", []), source="policies/*.md")
    pipeline_log.append("[4] Baseline agent read approved Markdown policy files.")

    system_prompt = read_hardened_system_prompt()
    logger.event(
        "hardened_system_prompt_loaded",
        source="baseline_agent/system_prompt.md",
        prompt_chars=len(system_prompt),
        prompt_level_controls_attempted=True,
    )
    pipeline_log.append("[5] Baseline agent loaded a hardened system prompt with prompt-level governance instructions.")

    prompt = build_baseline_agent_prompt(
        booking=booking,
        chat_history=chat_history,
        policy_context=policy_context,
        customer_name_supplied=customer_name,
        user_message=user_message,
        requested_action=requested_action,
    )
    logger.event("user_prompt_built", prompt_chars=len(prompt), policy_sources=policy_context.get("policy_sources", []))
    pipeline_log.append("[6] Baseline agent built the user prompt with booking, chat, and policy context.")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]
    logger.event("llm_call_started", provider=provider, model=model, prompt_chars=len(prompt))
    llm = DirectLLMClient(provider=provider, model=model, api_key=api_key, temperature=temperature)
    model_response = llm.invoke(messages)
    raw_output = getattr(model_response, "content", str(model_response))
    llm_metadata = llm.metadata()
    logger.event("llm_call_completed", provider=llm_metadata["provider"], model=llm_metadata["model"], output_chars=len(raw_output))
    pipeline_log.append("[7] Direct LLM provider call completed. Prompt-level instructions were used, but no src.llm_gateway guardrails were used.")

    parsed = _extract_json_object(raw_output)
    recommended_action = _normalise_action(parsed.get("recommended_action"))
    action_record = _baseline_action_record(recommended_action, parsed, booking)
    logger.event("model_recommendation_parsed", recommended_action=recommended_action, needs_manual_review=parsed.get("needs_manual_review"))
    pipeline_log.append(f"[8] Model recommended action={recommended_action!r}.")
    pipeline_log.append("[9] Baseline local log record created; governed trace reconstruction is not produced.")
    logger.event("run_completed", recommended_action=recommended_action, log_paths=logger.paths())

    return {
        "agent_type": "baseline_agent",
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
            "path": "baseline_agent -> booking CSV + chat history JSONL + policy Markdown -> LLM prompt",
            "booking_record_accessed": True,
            "prior_communications_accessed": True,
            "policy_markdown_accessed": True,
            "policy_sources": policy_context.get("policy_sources", []),
        },
        "system_prompt_payload": system_prompt,
        "prompt_payload": prompt,
        "prompt_level_governance": {
            "attempted": True,
            "source": "baseline_agent/system_prompt.md",
            "enforcement_type": "instruction_only",
            "runtime_controls_applied": False,
            "note": "The baseline uses a hardened system prompt to attempt governance, but it does not enforce policy, IAM, tool firewall, human approval, output verification, or runtime LLM guardrails outside the model prompt.",
        },
        "model_raw_output": raw_output,
        "model_parsed_output": parsed,
        "customer_response": parsed.get("customer_response") or raw_output,
        "model_recommended_action": recommended_action,
        # Backward-compatible key for existing notebooks/UI that expect this name.
        "model_tool_to_use": recommended_action,
        "baseline_action_record": action_record,
        # Backward-compatible key for existing notebooks/UI that expect this name.
        "naive_executor_result": action_record,
        "control_observations": _control_observations(
            booking, chat_history, policy_context, requested_action
        ),
        "baseline_logs": logger.paths(),
        "llm": {
            **llm_metadata,
            "prompt_level_governance_attempted": True,
            "hardened_system_prompt_used": True,
            "runtime_guardrails_applied": False,
        },
        "minimal_audit_record": {
            "booking_id": booking.get("booking_id"),
            "customer_id": booking.get("customer_id"),
            "policy_sources": policy_context.get("policy_sources", []),
            "model_recommended_action": recommended_action,
            "needs_manual_review": parsed.get("needs_manual_review"),
        },
    }
