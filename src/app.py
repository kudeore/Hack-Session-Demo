from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Allow running as: python src/app.py
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.graph import run_case
from baseline_agent import run_naive_agent
from src.audit import AuditLogger
from src.llm_gateway import get_llm_guardrail_state, reset_llm_guardrail_state


DEFAULT_MESSAGE = (
    "My father passed away. Your chatbot told me I can claim bereavement refund "
    "within 90 days. Please process it."
)


def summarize(result):
    facts = result.get("facts") or {}
    booking = facts.get("booking") or {}
    iam_decisions = facts.get("iam_decisions", [])
    security = result.get("security_decision", {})
    customer_data_accessed = bool(facts and not facts.get("blocked_before_data_access"))

    if customer_data_accessed:
        data_boundary_summary = {
            "customer_data_access_path": "agent -> registered skill -> secure customer data API",
            "agent_direct_data_access": "denied by IAM policy",
            "skill_principal_used": iam_decisions[0]["principal"] if iam_decisions else None,
            "iam_policy_version": iam_decisions[0]["iam_policy_version"] if iam_decisions else None,
            "runtime_attested_skill_identity": iam_decisions[0].get("runtime_attested") if iam_decisions else None,
            "llm_receives_raw_chat": False,
            "llm_receives_full_customer_profile": False,
        }
        facts_summary = {
            "booking_id": booking["booking_id"],
            "resolved_customer_id": booking.get("customer_id"),
            "customer_name_match_checked_by_api": booking.get("customer_name_match"),
            "customer_name_returned_to_llm": False,
            "travel_status": booking["travel_status"],
            "booking_channel": booking["booking_channel"],
            "prior_misinformation_flag_from_logs": facts["prior_misinformation_flag_from_logs"],
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
            "reason": "Suspicious input was handed to AI Security Review before facts retrieval.",
        }

    context = result.get("context", {})
    checkpoint_summary = {
        "checkpoint_count": len(result.get("checkpoints", [])),
        "latest_checkpoint": (result.get("checkpoints") or [{}])[-1],
    }

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
        "checkpoint_summary": checkpoint_summary,
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
        "llm_guardrails": get_llm_guardrail_state(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", default="C002", help="Known customer ID for CLI runs. Use UNKNOWN when resolving from booking ID.")
    parser.add_argument("--booking-id", default=None, help="Booking ID supplied by customer; resolved after adversarial guard.")
    parser.add_argument("--customer-name", default=None, help="Optional customer-provided name for verification; never passed to the LLM.")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--requested-action", default="execute_refund")
    parser.add_argument("--naive", action="store_true", help="Also run the real LLM naive agent path before the governed harness.")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--save-audit", default=None)
    parser.add_argument("--audit-backend", default="in_memory", choices=["in_memory", "jsonl_file"])
    parser.add_argument("--state-backend", default="in_memory", choices=["in_memory", "json_file"])
    parser.add_argument("--checkpoint-dir", default="outputs/checkpoints")
    parser.add_argument("--no-checkpoints", action="store_true")
    parser.add_argument("--max-llm-calls", type=int, default=None)
    parser.add_argument("--max-estimated-cost-usd", type=float, default=None)
    parser.add_argument("--llm-kill-switch", action="store_true")
    args = parser.parse_args()

    if args.max_llm_calls is not None:
        os.environ["LLM_MAX_CALLS_PER_RUN"] = str(args.max_llm_calls)
    if args.max_estimated_cost_usd is not None:
        os.environ["LLM_MAX_ESTIMATED_COST_USD_PER_RUN"] = str(args.max_estimated_cost_usd)
    if args.llm_kill_switch:
        os.environ["LLM_KILL_SWITCH"] = "true"

    print(f"LLM_PROVIDER={os.getenv('LLM_PROVIDER', 'gemini')}")
    print("=" * 100)

    reset_llm_guardrail_state()

    if args.naive:
        print("NAIVE AGENT RUN")
        print(json.dumps(run_naive_agent(
            customer_id=None if args.customer == "UNKNOWN" else args.customer,
            booking_id=args.booking_id,
            customer_name=args.customer_name,
            user_message=args.message,
            requested_action=args.requested_action,
            provider=os.getenv("LLM_PROVIDER", "gemini"),
            model=os.getenv("GEMINI_MODEL"),
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0") or 0),
        ), indent=2, ensure_ascii=False))
        print("=" * 100)

    result = run_case(
        args.customer,
        args.message,
        args.requested_action,
        booking_id=args.booking_id,
        customer_name=args.customer_name,
        audit_backend=args.audit_backend,
        state_backend=args.state_backend,
        audit_path=args.save_audit,
        checkpoint_dir=args.checkpoint_dir,
        checkpoint_enabled=not args.no_checkpoints,
    )
    print("GOVERNED AGENT RUN")
    print(json.dumps(summarize(result), indent=2, ensure_ascii=False))

    if args.audit:
        print("=" * 100)
        print("AUDIT TRAIL")
        print(json.dumps(result["audit"], indent=2, ensure_ascii=False))

    if args.save_audit:
        Path(args.save_audit).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_audit).write_text(AuditLogger.to_jsonl(result["audit"]), encoding="utf-8")
        print(f"Audit saved to {args.save_audit}")


if __name__ == "__main__":
    main()
