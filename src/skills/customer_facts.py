from __future__ import annotations

from src.iam_gateway import SecureCustomerDataAPI, create_llm_safe_facts
from src.schemas import SkillResult


class CustomerFactsRetrieverSkill:
    """
    Retrieves customer facts through the secure data API boundary.

    The agent does not read bookings.csv or chat_history.jsonl directly. This
    skill uses a named service principal. The API checks IAM, purpose, resource,
    action, and field-level data contract before returning data.
    """

    name = "customer_facts_retriever"
    skill_type = "deterministic_tool"

    principal = "skill:customer_facts_retriever"
    purpose = "refund_case_assessment"

    def run(self, state):
        security = state.get("security_decision", {})
        restrictions = security.get("restrictions", {})
        if restrictions.get("customer_data_access_allowed") is False:
            return SkillResult(
                skill_name=self.name,
                status="blocked",
                summary="Customer data access blocked by adversarial input guard.",
                data={
                    "blocked_before_data_access": True,
                    "security_decision": security,
                },
                evidence=["security_decision.customer_data_access_allowed=false"],
                risk_flags=security.get("flags", []),
            )

        api = SecureCustomerDataAPI()

        if state.get("booking_id"):
            booking, booking_iam = api.read_booking_minimum_by_booking_id(
                principal=self.principal,
                booking_id=state["booking_id"],
                purpose=self.purpose,
                runtime_attested=True,
                customer_name_hint=state.get("customer_name") or None,
            )
        else:
            booking, booking_iam = api.read_booking_minimum(
                principal=self.principal,
                customer_id=state["customer_id"],
                purpose=self.purpose,
                runtime_attested=True,
            )
        chat_summary, chat_iam = api.read_relevant_chat_summary(
            principal=self.principal,
            customer_id=booking["customer_id"],
            booking_id=booking["booking_id"],
            purpose=self.purpose,
            runtime_attested=True,
        )

        prior_misinfo = any(c["risk_signal"] == "prior_misinformation" for c in chat_summary)

        facts = {
            "booking": booking,
            "relevant_chat_history_summary": chat_summary,
            "prior_misinformation_flag_from_logs": prior_misinfo,
            "iam_decisions": [booking_iam, chat_iam],
        }
        facts["llm_safe_facts"] = create_llm_safe_facts(facts)

        evidence = [
            f"booking_id={booking['booking_id']}",
            f"resolved_customer_id={booking['customer_id']}",
            f"customer_name_match={booking.get('customer_name_match')}",
            f"travel_status={booking['travel_status']}",
            f"iam_policy_version={booking_iam['iam_policy_version']}",
            "runtime_attested_skill_identity_required",
            "customer_name_not_returned_by_api",
            "raw_chat_message_not_returned_by_api",
        ]
        risk_flags = []
        if prior_misinfo:
            evidence.append("chat_summary contains prior_misinformation risk_signal")
            risk_flags.append("prior_misinformation_flag")
        if booking.get("customer_name_match") is False:
            evidence.append("customer-provided name did not match booking record")
            risk_flags.append("customer_name_mismatch")

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary="Retrieved minimum necessary facts through secure API with IAM and field-level filtering.",
            data=facts,
            evidence=evidence,
            risk_flags=risk_flags,
        )
