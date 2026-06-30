from __future__ import annotations

from src.schemas import SkillResult


class RefundCalculatorSkill:
    name = "refund_calculator"
    skill_type = "deterministic_tool"

    def run(self, state):
        booking = state["facts"]["booking"]
        calc = {
            "estimated_refund_amount": booking["refund_amount_estimate"],
            "currency": "DEMO_UNITS",
            "method": "approved_synthetic_refund_calculator_v0",
            "llm_used": False,
        }
        return SkillResult(
            skill_name=self.name,
            status="success",
            summary="Calculated refund using deterministic calculator. No LLM amount estimation used.",
            data=calc,
            evidence=[f"refund_amount_estimate={calc['estimated_refund_amount']}"],
        )
