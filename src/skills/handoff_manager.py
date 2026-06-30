from __future__ import annotations

from src.schemas import SkillResult


class HandoffManagerSkill:
    name = "handoff_manager"
    skill_type = "deterministic_control"

    HANDOFF_RULES = [
        ("prompt_injection_detected", "AI Security Review", "PROMPT_INJECTION_ATTEMPT", "high"),
        ("system_prompt_extraction_attempt", "AI Security Review", "SYSTEM_PROMPT_EXTRACTION_ATTEMPT", "high"),
        ("tool_escalation_attempt", "AI Security Review", "TOOL_ESCALATION_ATTEMPT", "high"),
        ("data_exfiltration_attempt", "AI Security Review", "DATA_EXFILTRATION_ATTEMPT", "high"),
        ("encoded_or_obfuscated_payload", "AI Security Review", "OBFUSCATED_PAYLOAD_REVIEW", "medium"),
        ("adversarial_guard_manual_review", "AI Security Review", "ADVERSARIAL_GUARD_REVIEW", "high"),
        ("tool_access_blocked_by_adversarial_guard", "AI Security Review", "TOOL_ACCESS_BLOCKED_BY_SECURITY_GUARD", "high"),
        ("prior_misinformation_flag", "Customer Remediation Review", "AI_PRIOR_MISINFO_REVIEW", "high"),
        ("bereavement_or_vulnerability", "Bereavement Support Desk", "SENSITIVE_CUSTOMER_CONTEXT", "high"),
        ("travel_status_completed", "Refund Exceptions Team", "COMPLETED_TRAVEL_EXCEPTION", "medium"),
        ("travel_status_partially_flown", "Refund Exceptions Team", "PARTIAL_TRAVEL_EXCEPTION", "medium"),
        ("third_party_booking", "Third Party Booking Support", "THIRD_PARTY_BOOKING_LIMITATION", "medium"),
        ("refund_amount_threshold", "Refund Approval Team", "HIGH_VALUE_REFUND_REVIEW", "medium"),
        ("customer_name_mismatch", "Identity Verification Review", "CUSTOMER_NAME_MISMATCH", "high"),
    ]

    def run(self, state):
        firewall = state["firewall_decision"]
        booking = state["facts"]["booking"]
        reasons = set(firewall.get("blocked_reasons", []))

        matched = []
        for condition, team, reason_code, priority in self.HANDOFF_RULES:
            if condition in reasons:
                matched.append({
                    "condition": condition,
                    "team": team,
                    "reason_code": reason_code,
                    "priority": priority,
                })

        if not matched and firewall["allowed"]:
            handoff = {
                "handoff_required": False,
                "team": None,
                "priority": None,
                "reason_codes": [],
                "case_id": None,
            }
        else:
            team = matched[0]["team"] if matched else "General Refund Review"
            priority = "high" if any(m["priority"] == "high" for m in matched) else "medium"
            handoff = {
                "handoff_required": True,
                "team": team,
                "priority": priority,
                "reason_codes": [m["reason_code"] for m in matched] or ["GENERAL_MANUAL_REVIEW"],
                "case_id": f"RF-{booking['booking_id']}-{booking['customer_id']}",
                "evidence": {
                    "booking_id": booking["booking_id"],
                    "blocked_reasons": sorted(reasons),
                }
            }

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary="Handoff decision created.",
            data=handoff,
            evidence=handoff.get("reason_codes", []),
            risk_flags=list(reasons),
        )
