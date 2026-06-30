from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from src.schemas import SkillResult

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "policies"


class PolicyAsCodeEvaluatorSkill:
    """
    Converts approved policy into deterministic runtime controls.

    Markdown policy and retrieval provide context. Enforcement is handled by a
    versioned, tested, deterministic policy package that returns policy version,
    rule IDs, and evidence.
    """

    name = "policy_as_code_evaluator"
    skill_type = "deterministic_policy"

    def run(self, state):
        manifest = self._load_policy_manifest()
        rules = self._load_policy_rules()

        booking = state["facts"]["booking"]
        risk_flags = state.get("risk", {}).get("flags", [])
        prior_misinfo = state["facts"].get("prior_misinformation_flag_from_logs", False)
        policy_assessment = state.get("policy_assessment", {})
        calc = state.get("refund_calculation", {})
        security_decision = state.get("security_decision", {})

        manual_triggers: List[str] = []
        decision_trace: List[Dict[str, str]] = []

        def trigger_when(condition: bool, trigger: str, rule_id: str, evidence: str) -> None:
            if condition:
                manual_triggers.append(trigger)
                decision_trace.append(
                    {
                        "rule_id": rule_id,
                        "decision": "manual_review_required",
                        "trigger": trigger,
                        "evidence": evidence,
                    }
                )

        trigger_when(
            "bereavement_or_vulnerability" in risk_flags,
            "bereavement_or_vulnerability",
            "PAC-MR-001",
            "risk classifier flag",
        )
        trigger_when(
            "prior_misinformation_claim" in risk_flags or prior_misinfo,
            "prior_misinformation_flag",
            "PAC-MR-002",
            "customer claim or official-channel chat summary",
        )
        trigger_when(
            "prompt_injection_detected" in risk_flags,
            "prompt_injection_detected",
            "PAC-MR-003",
            "risk classifier flag",
        )
        trigger_when(
            "complaint_or_legal_signal" in risk_flags,
            "complaint_or_legal_signal",
            "PAC-MR-004",
            "risk classifier flag",
        )
        trigger_when(
            booking["travel_status"] == "completed",
            "travel_status_completed",
            "PAC-MR-005",
            "booking.travel_status=completed",
        )
        trigger_when(
            booking["travel_status"] == "partially_flown",
            "travel_status_partially_flown",
            "PAC-MR-006",
            "booking.travel_status=partially_flown",
        )
        trigger_when(
            booking["booking_channel"] == "third_party",
            "third_party_booking",
            "PAC-MR-007",
            "booking.booking_channel=third_party",
        )
        trigger_when(
            calc.get("estimated_refund_amount", 0) >= 500 or booking["ticket_amount"] >= 500,
            "refund_amount_threshold",
            "PAC-MR-008",
            "refund or ticket amount >= 500",
        )
        trigger_when(
            policy_assessment.get("policy_conflict", False),
            "policy_conflict",
            "PAC-MR-009",
            "LLM/SLM policy reasoner conflict flag",
        )
        trigger_when(
            policy_assessment.get("manual_review_required", False),
            "llm_policy_reasoner_manual_review",
            "PAC-MR-010",
            "LLM/SLM policy reasoner recommended manual review",
        )
        trigger_when(
            security_decision.get("manual_review_required", False),
            "adversarial_guard_manual_review",
            "PAC-MR-011",
            f"security_decision={security_decision.get('decision')}",
        )


        trigger_when(
            booking.get("customer_name_match") is False,
            "customer_name_mismatch",
            "PAC-MR-012",
            "customer-provided name did not match booking verification signal",
        )

        auto_refund_prepare_allowed = self._auto_refund_prepare_allowed(
            booking=booking,
            calc=calc,
            manual_triggers=manual_triggers,
        )

        decision = {
            "policy_package_id": manifest["id"],
            "policy_package_version": manifest["version"],
            "policy_package_status": manifest["status"],
            "source_documents": manifest["source_documents"],
            "rules_loaded_from": "policies/policy_rules.yaml",
            "rego_reference": "policies/refund_policy.rego",
            "auto_refund_prepare_allowed": auto_refund_prepare_allowed,
            "manual_review_required": bool(manual_triggers),
            "manual_review_triggers": sorted(set(manual_triggers)),
            "decision_trace": decision_trace,
            "security_decision": {
                "decision": security_decision.get("decision"),
                "severity": security_decision.get("severity"),
                "flags": security_decision.get("flags", []),
            },
            "prohibited_actions": rules.get("prohibited_actions", []),
            "safe_next_action": (
                "prepare_refund_pending_confirmation"
                if auto_refund_prepare_allowed
                else "create_manual_review_case"
            ),
        }

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary=f"Evaluated policy-as-code package {manifest['version']} with rule-level traceability.",
            data=decision,
            evidence=[d["rule_id"] for d in decision_trace] + [manifest["version"]],
            risk_flags=decision["manual_review_triggers"],
        )

    @staticmethod
    def _load_policy_manifest() -> Dict[str, Any]:
        return yaml.safe_load((POLICY_DIR / "policy_manifest.yaml").read_text(encoding="utf-8"))["policy_package"]

    @staticmethod
    def _load_policy_rules() -> Dict[str, Any]:
        return yaml.safe_load((POLICY_DIR / "policy_rules.yaml").read_text(encoding="utf-8"))["policy_as_code"]

    @staticmethod
    def _auto_refund_prepare_allowed(booking: Dict[str, Any], calc: Dict[str, Any], manual_triggers: List[str]) -> bool:
        return (
            booking["customer_verified"]
            and booking["booking_channel"] == "direct"
            and booking["travel_status"] == "not_started"
            and booking["fare_type"] == "flexible"
            and booking["payment_method"] == "card"
            and calc.get("estimated_refund_amount", 0) > 0
            and calc.get("estimated_refund_amount", 0) < 500
            and not manual_triggers
        )
