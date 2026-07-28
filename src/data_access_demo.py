from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.iam_gateway import AccessDenied, SecureCustomerDataAPI, create_llm_safe_facts


def print_section(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def print_denied(message: str, exc: AccessDenied) -> None:
    print(json.dumps(
        {
            "result": "DENIED",
            "message": message,
            "iam_decision": json.loads(str(exc)),
        },
        indent=2,
        ensure_ascii=False,
    ))


def main() -> None:
    api = SecureCustomerDataAPI()
    customer_id = "C002"
    purpose = "refund_case_assessment"

    print_section("1) Agent tries to access customer data API directly")
    try:
        api.read_booking_minimum(
            principal="agent:AGENT-REFUND-CASE-MANAGER-001",
            customer_id=customer_id,
            purpose=purpose,
            runtime_attested=True,
        )
    except AccessDenied as exc:
        print_denied("Agent has no direct customer-data API entitlement.", exc)

    print_section("2) Prompt-controlled code claims to be the skill but lacks runtime attestation")
    try:
        api.read_booking_minimum(
            principal="skill:customer_facts_retriever",
            customer_id=customer_id,
            purpose=purpose,
            runtime_attested=False,
        )
    except AccessDenied as exc:
        print_denied(
            "A string saying 'skill:customer_facts_retriever' is not enough. "
            "The API requires runtime-attested service identity.",
            exc,
        )

    print_section("3) Approved skill service account accesses minimum required data")
    booking, booking_iam = api.read_booking_minimum(
        principal="skill:customer_facts_retriever",
        customer_id=customer_id,
        purpose=purpose,
        runtime_attested=True,
    )
    chat_summary, chat_iam = api.read_relevant_chat_summary(
        principal="skill:customer_facts_retriever",
        customer_id=customer_id,
        booking_id=booking["booking_id"],
        purpose=purpose,
        runtime_attested=True,
    )

    facts = {
        "booking": booking,
        "relevant_chat_history_summary": chat_summary,
        "prior_misinformation_flag_from_logs": any(
            c["risk_signal"] == "prior_misinformation" for c in chat_summary
        ),
    }

    print(json.dumps(
        {
            "result": "ALLOWED_WITH_FIELD_FILTERING",
            "why_this_is_safer_than_agent_access": [
                "The service identity is issued by the platform/runtime, not by the LLM or user prompt.",
                "The skill has one narrow purpose: refund_case_assessment.",
                "The API allows only specific actions and data contracts.",
                "The API filters fields before returning data.",
                "The agent still receives only LLM-safe facts, not raw records.",
            ],
            "booking_returned_by_api": booking,
            "chat_summary_returned_by_api": chat_summary,
            "booking_iam_decision": booking_iam,
            "chat_iam_decision": chat_iam,
            "not_returned_to_skill_or_llm": [
                "customer_name",
                "email",
                "phone",
                "card_number",
                "raw_chat_message",
                "full_customer_profile",
            ],
        },
        indent=2,
        ensure_ascii=False,
    ))

    print_section("4) Final LLM-safe fact view")
    print(json.dumps(create_llm_safe_facts(facts), indent=2, ensure_ascii=False))


# if __name__ == "__main__":
#     main()
