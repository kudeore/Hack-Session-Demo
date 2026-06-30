from __future__ import annotations

from src.schemas import SkillResult


class ToolFirewallSkill:
    name = "tool_firewall"
    skill_type = "deterministic_control"

    def run(self, state):
        decision = state["policy_decision"]
        requested_action = state.get("requested_action", "execute_refund")

        blocked_reasons = []
        security = state.get("security_decision", {})
        restrictions = security.get("restrictions", {})

        if requested_action == "execute_refund":
            blocked_reasons.append("execute_refund_blocked_without_required_approval")

        if decision["manual_review_required"]:
            blocked_reasons.append("manual_review_required")

        if restrictions.get("tool_access_allowed") is False:
            blocked_reasons.append("tool_access_blocked_by_adversarial_guard")

        if not decision["auto_refund_prepare_allowed"]:
            blocked_reasons.append("auto_refund_prepare_not_allowed")

        blocked_reasons.extend(decision.get("manual_review_triggers", []))

        allowed = (
            requested_action == "prepare_refund_pending_confirmation"
            and decision["auto_refund_prepare_allowed"]
            and not decision["manual_review_required"]
            and restrictions.get("tool_access_allowed", True) is True
        )

        firewall = {
            "requested_action": requested_action,
            "allowed": allowed,
            "decision": "allow_prepare_refund" if allowed else "block_and_handoff",
            "blocked_reasons": sorted(set(blocked_reasons)),
            "safe_alternative": (
                "prepare_refund_pending_confirmation"
                if allowed
                else "create_manual_review_case"
            ),
        }

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary=f"Tool firewall decision: {firewall['decision']}",
            data=firewall,
            evidence=firewall["blocked_reasons"],
            risk_flags=firewall["blocked_reasons"],
        )
