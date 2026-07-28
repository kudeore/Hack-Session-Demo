from __future__ import annotations

"""Callable entry points for the naive and governed refund-agent flows."""

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, Optional

from dotenv import load_dotenv

load_dotenv()

from src.llm_gateway import get_llm_guardrail_state, reset_llm_guardrail_state


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


@contextmanager
def _temporary_llm_env(
    *,
    provider: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    max_llm_calls: Optional[int] = None,
    max_estimated_cost_usd: Optional[float] = None,
    llm_kill_switch: Optional[bool] = None,
) -> Iterator[None]:
    """Temporarily set LLM environment variables for one function call."""

    keys = [
        "LLM_PROVIDER",
        "LLM_TEMPERATURE",
        "GEMINI_MODEL",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "LLM_MAX_CALLS_PER_RUN",
        "LLM_MAX_ESTIMATED_COST_USD_PER_RUN",
        "LLM_KILL_SWITCH",
    ]
    old = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["LLM_PROVIDER"] = provider
        os.environ["LLM_TEMPERATURE"] = str(temperature)
        if provider in {"gemini", "google", "google_genai"}:
            os.environ["GEMINI_MODEL"] = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
            if api_key:
                os.environ["GOOGLE_API_KEY"] = api_key
        elif provider == "openai" and api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        if max_llm_calls is not None:
            os.environ["LLM_MAX_CALLS_PER_RUN"] = str(max_llm_calls)
        if max_estimated_cost_usd is not None:
            os.environ["LLM_MAX_ESTIMATED_COST_USD_PER_RUN"] = str(max_estimated_cost_usd)
        if llm_kill_switch is not None:
            os.environ["LLM_KILL_SWITCH"] = "true" if llm_kill_switch else "false"
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _normalise_input(input_case: Mapping[str, Any]) -> Dict[str, Any]:
    """Accept common key names so the caller can paste simple use-case dictionaries."""

    message = input_case.get("message") or input_case.get("user_message") or input_case.get("customer_message") or ""
    booking_id = input_case.get("booking_id") or input_case.get("booking")
    customer_id = input_case.get("customer_id") or "UNKNOWN"
    customer_name = input_case.get("customer_name") or input_case.get("name")
    requested_action = input_case.get("requested_action") or input_case.get("action") or "execute_refund"

    if not booking_id and customer_id == "UNKNOWN":
        raise ValueError("Input dictionary must contain booking_id or customer_id.")
    if not message:
        raise ValueError("Input dictionary must contain message, user_message, or customer_message.")

    return {
        "booking_id": str(booking_id) if booking_id else None,
        "customer_id": str(customer_id),
        "customer_name": str(customer_name) if customer_name else None,
        "message": str(message),
        "requested_action": str(requested_action),
    }


def _resolve_llm_settings(
    input_case: Mapping[str, Any],
    *,
    provider: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    resolved_provider = str(input_case.get("llm_provider") or provider).lower()
    if resolved_provider == "mock":
        raise ValueError(
            "Mock LLM mode is disabled. Use provider='gemini', 'openai', or 'ollama'."
        )
    return {
        "provider": resolved_provider,
        "model": str(input_case.get("gemini_model") or input_case.get("model") or model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL),
        "api_key": api_key or input_case.get("api_key") or input_case.get("google_api_key") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
        "temperature": float(input_case.get("temperature") if input_case.get("temperature") is not None else (temperature if temperature is not None else os.getenv("LLM_TEMPERATURE", "0"))),
    }


def _require_real_llm(
    llm_settings: Mapping[str, Any],
    *,
    llm_kill_switch: Optional[bool] = None,
    max_llm_calls: Optional[int] = None,
    max_estimated_cost_usd: Optional[float] = None,
) -> None:
    kill_switch_enabled = bool(llm_kill_switch) or str(os.getenv("LLM_KILL_SWITCH", "")).lower() in {"1", "true", "yes", "on", "enabled", "kill", "stop"}
    hard_budget_stop = (max_llm_calls is not None and max_llm_calls <= 0) or (max_estimated_cost_usd is not None and max_estimated_cost_usd <= 0)
    if kill_switch_enabled or hard_budget_stop:
        return
    if llm_settings["provider"] in {"gemini", "google", "google_genai"} and not llm_settings["api_key"]:
        raise ValueError("Gemini mode requires GOOGLE_API_KEY/GEMINI_API_KEY or api_key=.")
    if llm_settings["provider"] == "openai" and not os.getenv("OPENAI_API_KEY") and not llm_settings["api_key"]:
        raise ValueError("OpenAI mode requires OPENAI_API_KEY.")


def run_naive_agent(
    input_case: Mapping[str, Any],
    *,
    provider: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    include_prompt_payload: bool = True,
    max_llm_calls: Optional[int] = None,
    max_estimated_cost_usd: Optional[float] = None,
    llm_kill_switch: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Version 1: Naive Agent.

    This uses a real LLM call. It directly reads the local booking record,
    prior communication notes, and approved Markdown policy files. No mock
    fallback is used, because the aim is to observe real model behaviour in a
    baseline agent architecture without the governed control harness.
    """

    case = _normalise_input(input_case)
    llm_settings = _resolve_llm_settings(
        input_case,
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=temperature,
    )
    _require_real_llm(llm_settings)

    from baseline_agent import run_naive_agent as _run_naive_agent_impl

    # Baseline is intentionally not routed through src.llm_gateway.
    # max_llm_calls, max_estimated_cost_usd, and llm_kill_switch are governed-agent
    # controls, so this wrapper does not apply them to the naive/baseline path.
    with _temporary_llm_env(
        **llm_settings,
        max_llm_calls=None,
        max_estimated_cost_usd=None,
        llm_kill_switch=None,
    ):
        result = _run_naive_agent_impl(
            customer_id=None if case["customer_id"] == "UNKNOWN" else case["customer_id"],
            booking_id=case["booking_id"],
            customer_name=case["customer_name"],
            user_message=case["message"],
            requested_action=case["requested_action"],
            provider=llm_settings["provider"],
            model=llm_settings["model"],
            api_key=llm_settings["api_key"],
            temperature=llm_settings["temperature"],
        )

    if not include_prompt_payload:
        result["prompt_payload"] = None

    result["llm"] = {
        **result.get("llm", {}),
        "provider": llm_settings["provider"],
        "model": llm_settings["model"],
        "temperature": llm_settings["temperature"],
        "actual_llm_called": True,
        "guardrail_gateway_used": False,
        "runtime_guardrails_applied": False,
        "note": "Baseline agent uses a hardened system prompt and calls the LLM directly from baseline_agent/llm_client.py. src.llm_gateway is not used.",
    }
    result["version"] = "naive_agent"
    return result


def _summarize_governed_result(result: Mapping[str, Any]) -> Dict[str, Any]:
    facts = result.get("facts") or {}
    booking = facts.get("booking") or {}
    iam_decisions = facts.get("iam_decisions", [])
    security = result.get("security_decision", {})
    customer_data_accessed = bool(facts and not facts.get("blocked_before_data_access"))

    if customer_data_accessed:
        first_iam = iam_decisions[0] if iam_decisions else {}
        data_boundary_summary = {
            "customer_data_access_path": "agent -> registered skill -> secure customer data API",
            "agent_direct_data_access": "denied by IAM policy",
            "skill_principal_used": first_iam.get("principal"),
            "iam_policy_version": first_iam.get("iam_policy_version"),
            "runtime_attested_skill_identity": first_iam.get("runtime_attested"),
            "llm_receives_raw_chat": False,
            "llm_receives_full_customer_profile": False,
        }
        facts_summary = {
            "booking_id": booking.get("booking_id"),
            "resolved_customer_id": booking.get("customer_id"),
            "customer_name_match_checked_by_api": booking.get("customer_name_match"),
            "customer_name_returned_to_llm": False,
            "travel_status": booking.get("travel_status"),
            "booking_channel": booking.get("booking_channel"),
            "prior_misinformation_flag_from_logs": facts.get("prior_misinformation_flag_from_logs"),
        }
    else:
        data_boundary_summary = {
            "customer_data_access_path": "blocked before customer data access",
            "customer_data_accessed": False,
            "blocked_by": "adversarial_input_guard",
            "security_decision": security.get("decision"),
            "security_severity": security.get("severity"),
            "llm_receives_raw_chat": False,
            "llm_receives_full_customer_profile": False,
        }
        facts_summary = {
            "customer_data_accessed": False,
            "reason": "Request was stopped before facts retrieval.",
        }

    context = result.get("context", {})
    return {
        "trace_context": {
            "trace_id": context.get("trace_id"),
            "request_id": context.get("request_id"),
            "case_id": context.get("case_id") or result.get("action_result", {}).get("case_id"),
            "idempotency_key": context.get("idempotency_key"),
            "workflow_version": context.get("workflow_version"),
            "container_id": context.get("container_id"),
            "resolved_customer_id": context.get("resolved_customer_id"),
            "resolved_customer_id_source": context.get("resolved_customer_id_source"),
        },
        "runtime": result.get("runtime"),
        "risk": result.get("risk"),
        "security_decision": security,
        "data_boundary_summary": data_boundary_summary,
        "facts_summary": facts_summary,
        "policy_assessment": result.get("policy_assessment"),
        "refund_calculation": result.get("refund_calculation"),
        "policy_decision": result.get("policy_decision"),
        "firewall_decision": result.get("firewall_decision"),
        "handoff_decision": result.get("handoff_decision"),
        "action_result": result.get("action_result"),
        "final_response": result.get("final_response"),
        "output_verification": result.get("output_verification"),
        "audit_event_count": len(result.get("audit", [])),
    }


def run_governed_agent(
    input_case: Mapping[str, Any],
    *,
    provider: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    audit_backend: str = "in_memory",
    state_backend: str = "in_memory",
    checkpoint_enabled: bool = True,
    include_full_state: bool = False,
    max_llm_calls: Optional[int] = None,
    max_estimated_cost_usd: Optional[float] = None,
    llm_kill_switch: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Version 2: Governed Agent.

    This runs the hardened workflow using the configured real LLM provider.
    It does not fall back to mock mode. Customer ID is resolved only after the
    adversarial guard through the secure data-access skill when booking_id is supplied.
    """

    case = _normalise_input(input_case)
    llm_settings = _resolve_llm_settings(
        input_case,
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=temperature,
    )
    _require_real_llm(
        llm_settings,
        llm_kill_switch=llm_kill_switch,
        max_llm_calls=max_llm_calls,
        max_estimated_cost_usd=max_estimated_cost_usd,
    )

    try:
        from src.graph import run_case
    except ModuleNotFoundError as exc:  # Allows running from inside src/ as a script.
        if exc.name != "src":
            raise
        from graph import run_case

    reset_llm_guardrail_state()
    with _temporary_llm_env(
        **llm_settings,
        max_llm_calls=max_llm_calls,
        max_estimated_cost_usd=max_estimated_cost_usd,
        llm_kill_switch=llm_kill_switch,
    ):
        result = run_case(
            customer_id=case["customer_id"],
            booking_id=case["booking_id"],
            customer_name=case["customer_name"],
            user_message=case["message"],
            requested_action=case["requested_action"],
            audit_backend=audit_backend,
            state_backend=state_backend,
            checkpoint_enabled=checkpoint_enabled,
        )

    summary = _summarize_governed_result(result)
    output: Dict[str, Any] = {
        "version": "governed_agent",
        "input": case,
        "llm": {
            "provider": llm_settings["provider"],
            "model": llm_settings["model"],
            "temperature": llm_settings["temperature"],
            "actual_llm_called": get_llm_guardrail_state().get("actual_provider_calls", 0) > 0,
            "guardrails": get_llm_guardrail_state(),
        },
        "pipeline_log": [
            "[1] Request received and trace context created.",
            "[2] Risk classifier and adversarial guard run before customer data access.",
            "[3] Approved skill identity retrieves minimum data through secure API only if safe.",
            "[4] LLM receives LLM-safe facts, not raw customer records or raw chat.",
            "[5] Policy retrieval and policy-as-code determine manual review / action boundary.",
            "[6] Tool firewall controls whether requested action is allowed.",
            "[7] Handoff and output verifier complete the governed response.",
            "[8] Trace-aware audit events are available for reconstruction.",
        ],
        "final_response": result.get("final_response"),
        "summary": summary,
        "audit": result.get("audit", []),
    }
    if include_full_state:
        output["full_state"] = result
    return output


def run_naive_vs_governed(
    input_case: Mapping[str, Any],
    *,
    provider: str = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    max_llm_calls: Optional[int] = None,
    max_estimated_cost_usd: Optional[float] = None,
    llm_kill_switch: Optional[bool] = None,
) -> Dict[str, Any]:
    """Run both agents using the same input dictionary."""

    naive = run_naive_agent(
        input_case,
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_llm_calls=max_llm_calls,
        max_estimated_cost_usd=max_estimated_cost_usd,
        llm_kill_switch=llm_kill_switch,
    )
    governed = run_governed_agent(
        input_case,
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_llm_calls=max_llm_calls,
        max_estimated_cost_usd=max_estimated_cost_usd,
        llm_kill_switch=llm_kill_switch,
    )
    return {
        "input": dict(input_case),
        "naive_agent": naive,
        "governed_agent": governed,
    }


def print_compact_result(result: Mapping[str, Any]) -> None:
    """Helper for notebooks/CLI."""

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
