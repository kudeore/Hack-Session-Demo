from __future__ import annotations

try:
    from langgraph.graph import END, StateGraph
except ImportError as exc:
    raise ImportError("Install dependencies first: pip install -r requirements.txt") from exc

from src.audit import AuditLogger
from src.config_loader import load_yaml
from src.context import RequestContext, RequestContextFactory
from src.idempotency import reserve_or_reuse_case
from src.schemas import AgentState
from src.skill_runtime import run_skill
from src.state_store import checkpoint

from src.skills.intent_risk_classifier import IntentRiskClassifierSkill
from src.skills.adversarial_input_guard import AdversarialInputGuardSkill
from src.skills.customer_facts import CustomerFactsRetrieverSkill
from src.skills.policy_retriever import PolicyRetrieverSkill
from src.skills.policy_reasoner import PolicyReasonerSkill
from src.skills.refund_calculator import RefundCalculatorSkill
from src.skills.policy_as_code import PolicyAsCodeEvaluatorSkill
from src.skills.tool_firewall import ToolFirewallSkill
from src.skills.handoff_manager import HandoffManagerSkill
from src.skills.response_writer import CustomerResponseWriterSkill
from src.skills.output_verifier import OutputVerifierSkill


def load_registries_node(state: AgentState) -> AgentState:
    state["use_case"] = load_yaml("use_case_registry.yaml")
    state["agent_card"] = load_yaml("agent_card.yaml")
    state["skill_registry"] = load_yaml("skill_registry.yaml")
    state = AuditLogger.append(
        state,
        "registry_loader",
        "registry_loaded",
        {
            "use_case_id": state["use_case"]["use_case"]["id"],
            "agent_id": state["agent_card"]["agent"]["id"],
            "skills_loaded": list(state["skill_registry"]["skills"].keys()),
        },
    )
    if state.get("runtime", {}).get("checkpoint_enabled", True):
        state = checkpoint(state, "registry_loaded")
    return state


def risk_node(state: AgentState) -> AgentState:
    return run_skill(state, IntentRiskClassifierSkill(), output_key="risk")


def adversarial_guard_node(state: AgentState) -> AgentState:
    return run_skill(state, AdversarialInputGuardSkill(), output_key="security_decision")


def route_after_adversarial_guard(state: AgentState) -> str:
    """Stop suspicious requests before customer data access."""
    decision = state.get("security_decision", {}).get("decision")
    if decision == "block_before_data_access":
        return "security_handoff_skill"
    return "customer_facts_skill"


def facts_node(state: AgentState) -> AgentState:
    state = run_skill(state, CustomerFactsRetrieverSkill(), output_key="facts")

    # After adversarial guard has allowed the request, minimum booking data can
    # resolve operational metadata such as customer_id from the approved data API.
    # This keeps the UI realistic: the user may provide booking_id + message, while
    # the harness resolves customer_id only after the pre-data-access security gate.
    booking = state.get("facts", {}).get("booking", {})
    if booking.get("customer_id"):
        previous_customer_id = state.get("customer_id")
        state["customer_id"] = booking["customer_id"]
        state.setdefault("context", {})["resolved_customer_id"] = booking["customer_id"]
        state.setdefault("context", {})["resolved_customer_id_source"] = "secure_customer_data_api_after_adversarial_guard"
        state = AuditLogger.append(
            state,
            "metadata_resolution",
            "customer_metadata_resolved_after_security_gate",
            {
                "previous_customer_id": previous_customer_id,
                "resolved_customer_id": booking["customer_id"],
                "booking_id": booking.get("booking_id"),
                "source": "SecureCustomerDataAPI.read_booking_minimum_by_booking_id" if state.get("booking_id") else "SecureCustomerDataAPI.read_booking_minimum",
                "customer_name_returned_to_llm": False,
            },
        )
    return state


def policy_retrieval_node(state: AgentState) -> AgentState:
    return run_skill(state, PolicyRetrieverSkill(), output_key="policy_chunks", data_key="policy_chunks")


def policy_reasoning_node(state: AgentState) -> AgentState:
    return run_skill(state, PolicyReasonerSkill(), output_key="policy_assessment")


def refund_calculation_node(state: AgentState) -> AgentState:
    return run_skill(state, RefundCalculatorSkill(), output_key="refund_calculation")


def policy_as_code_node(state: AgentState) -> AgentState:
    return run_skill(state, PolicyAsCodeEvaluatorSkill(), output_key="policy_decision")


def tool_firewall_node(state: AgentState) -> AgentState:
    return run_skill(state, ToolFirewallSkill(), output_key="firewall_decision")


def security_handoff_node(state: AgentState) -> AgentState:
    """Create a safe handoff without reading customer data or invoking tools."""
    security = state.get("security_decision", {})
    case_id = f"SEC-{state.get('customer_id', 'UNKNOWN')}-ADVERSARIAL"
    idempotency = reserve_or_reuse_case(
        state,
        case_id=case_id,
        action="create_ai_security_review_case",
        booking_id=None,
    )
    case_id = idempotency["case_id"]
    state.setdefault("context", {})["case_id"] = case_id

    state["policy_decision"] = {
        "manual_review_required": True,
        "manual_review_triggers": security.get("flags", ["adversarial_input_detected"]),
        "auto_refund_prepare_allowed": False,
        "safe_next_action": "create_ai_security_review_case",
        "decision_source": "adversarial_input_guard",
        "customer_data_accessed": False,
        "policy_retrieval_performed": False,
        "llm_policy_reasoning_performed": False,
    }
    state["firewall_decision"] = {
        "requested_action": state.get("requested_action", "execute_refund"),
        "allowed": False,
        "decision": "block_before_data_access",
        "blocked_reasons": security.get("flags", ["adversarial_input_detected"]),
        "safe_alternative": "create_ai_security_review_case",
    }
    state["handoff_decision"] = {
        "handoff_required": True,
        "team": security.get("handoff_team") or "AI Security Review",
        "priority": "high",
        "reason_codes": [security.get("reason_code") or "ADVERSARIAL_INPUT_BLOCKED"],
        "case_id": case_id,
        "evidence": {
            "security_decision": security.get("decision"),
            "severity": security.get("severity"),
            "detected_categories": security.get("detected_categories", []),
            "customer_data_accessed": False,
            "idempotency": idempotency,
        },
    }
    state["action_result"] = {
        "action": "create_ai_security_review_case",
        "case_id": case_id,
        "team": state["handoff_decision"]["team"],
        "priority": "high",
        "refund_executed": False,
        "customer_data_accessed": False,
        "idempotency": idempotency,
    }
    state = AuditLogger.append(
        state,
        "security_handoff",
        "blocked_before_data_access",
        {
            "case_id": case_id,
            "security_decision": security,
            "customer_data_accessed": False,
            "idempotency": idempotency,
        },
    )
    if state.get("runtime", {}).get("checkpoint_enabled", True):
        state = checkpoint(state, "security_handoff_created")
    return state


def handoff_node(state: AgentState) -> AgentState:
    state = run_skill(state, HandoffManagerSkill(), output_key="handoff_decision")
    handoff = state["handoff_decision"]
    if handoff.get("handoff_required"):
        booking = state.get("facts", {}).get("booking", {})
        idempotency = reserve_or_reuse_case(
            state,
            case_id=handoff["case_id"],
            action="create_manual_review_case",
            booking_id=booking.get("booking_id"),
        )
        handoff["case_id"] = idempotency["case_id"]
        handoff["idempotency"] = idempotency
        state["handoff_decision"] = handoff
        state.setdefault("context", {})["case_id"] = handoff["case_id"]
        state["action_result"] = {
            "action": "create_manual_review_case",
            "case_id": handoff["case_id"],
            "team": handoff["team"],
            "priority": handoff["priority"],
            "refund_executed": False,
            "idempotency": idempotency,
        }
    else:
        state["action_result"] = {
            "action": "prepare_refund_pending_confirmation",
            "refund_executed": False,
            "requires_customer_confirmation": True,
            "estimated_refund_amount": state["refund_calculation"]["estimated_refund_amount"],
        }
    state = AuditLogger.append(state, "action_result_builder", "action_prepared", state["action_result"])
    if state.get("runtime", {}).get("checkpoint_enabled", True):
        state = checkpoint(state, "action_prepared")
    return state


def response_node(state: AgentState) -> AgentState:
    return run_skill(state, CustomerResponseWriterSkill(), output_key="final_response", data_key="final_response")


def verifier_node(state: AgentState) -> AgentState:
    state = run_skill(state, OutputVerifierSkill(), output_key="output_verification")
    if not state["output_verification"]["approved_to_send"]:
        state["final_response"] = state["output_verification"]["safer_rewrite"]
        state = AuditLogger.append(
            state,
            "safe_response_rewrite",
            "response_rewritten",
            {"final_response": state["final_response"]},
        )
    if state.get("runtime", {}).get("checkpoint_enabled", True):
        state = checkpoint(state, "final_response_verified")
    return state


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("load_registries", load_registries_node)
    graph.add_node("risk_classifier_skill", risk_node)
    graph.add_node("adversarial_input_guard", adversarial_guard_node)
    graph.add_node("security_handoff_skill", security_handoff_node)
    graph.add_node("customer_facts_skill", facts_node)
    graph.add_node("policy_retriever_skill", policy_retrieval_node)
    graph.add_node("policy_reasoner_skill", policy_reasoning_node)
    graph.add_node("refund_calculator_skill", refund_calculation_node)
    graph.add_node("policy_as_code_skill", policy_as_code_node)
    graph.add_node("tool_firewall_skill", tool_firewall_node)
    graph.add_node("handoff_skill", handoff_node)
    graph.add_node("response_writer_skill", response_node)
    graph.add_node("output_verifier_skill", verifier_node)

    graph.set_entry_point("load_registries")
    graph.add_edge("load_registries", "risk_classifier_skill")
    graph.add_edge("risk_classifier_skill", "adversarial_input_guard")
    graph.add_conditional_edges(
        "adversarial_input_guard",
        route_after_adversarial_guard,
        {
            "security_handoff_skill": "security_handoff_skill",
            "customer_facts_skill": "customer_facts_skill",
        },
    )
    graph.add_edge("security_handoff_skill", "response_writer_skill")
    graph.add_edge("customer_facts_skill", "policy_retriever_skill")
    graph.add_edge("policy_retriever_skill", "policy_reasoner_skill")
    graph.add_edge("policy_reasoner_skill", "refund_calculator_skill")
    graph.add_edge("refund_calculator_skill", "policy_as_code_skill")
    graph.add_edge("policy_as_code_skill", "tool_firewall_skill")
    graph.add_edge("tool_firewall_skill", "handoff_skill")
    graph.add_edge("handoff_skill", "response_writer_skill")
    graph.add_edge("response_writer_skill", "output_verifier_skill")
    graph.add_edge("output_verifier_skill", END)

    return graph.compile()


def run_case(
    customer_id: str = "UNKNOWN",
    user_message: str = "",
    requested_action: str = "execute_refund",
    *,
    booking_id: str | None = None,
    customer_name: str | None = None,
    context: RequestContext | dict | None = None,
    audit_backend: str = "in_memory",
    state_backend: str = "in_memory",
    audit_path: str | None = None,
    checkpoint_dir: str | None = None,
    checkpoint_enabled: bool = True,
) -> AgentState:
    app = build_graph()

    if context is None:
        context = RequestContextFactory.create(
            customer_id=customer_id,
            user_message=user_message,
            requested_action=requested_action,
            booking_id=booking_id,
            customer_name=customer_name,
        )
    context_dict = context.model_dump() if hasattr(context, "model_dump") else dict(context)

    runtime = {
        "audit_backend": audit_backend,
        "state_backend": state_backend,
        "checkpoint_enabled": checkpoint_enabled,
    }
    if audit_path:
        runtime["audit_path"] = audit_path
    if checkpoint_dir:
        runtime["checkpoint_dir"] = checkpoint_dir

    initial_state: AgentState = {
        "context": context_dict,
        "customer_id": customer_id,
        "booking_id": booking_id or "",
        "customer_name": customer_name or "",
        "user_message": user_message,
        "requested_action": requested_action,
        "runtime": runtime,
        "audit": [],
        "audit_sequence": 0,
        "checkpoints": [],
        "idempotency": {},
    }
    initial_state = AuditLogger.append(
        initial_state,
        "request_context",
        "request_received",
        {
            "trace_id": context_dict["trace_id"],
            "request_id": context_dict["request_id"],
            "idempotency_key": context_dict["idempotency_key"],
            "workflow_version": context_dict["workflow_version"],
            "container_id": context_dict["container_id"],
            "audit_backend": audit_backend,
            "state_backend": state_backend,
        },
    )
    if checkpoint_enabled:
        initial_state = checkpoint(initial_state, "request_received")
    return app.invoke(initial_state)
