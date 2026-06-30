from __future__ import annotations

import json
from langchain_core.messages import HumanMessage, SystemMessage
from src.llm_gateway import get_llm
from src.schemas import OutputVerification, SkillResult


class OutputVerifierSkill:
    name = "output_verifier"
    skill_type = "llm_or_slm_plus_deterministic"

    FORBIDDEN = [
        "i have processed your refund",
        "guaranteed refund",
        "definitely eligible",
        "definitely not eligible",
        "not our responsibility",
        "there is nothing we can do",
    ]

    def run(self, state):
        llm = get_llm().with_structured_output(OutputVerification)
        result = llm.invoke([
            SystemMessage(content=(
                "You are an output verifier for a regulated refund workflow. "
                "Check for hallucinated policy claims, refund promises, false execution claims, "
                "missing handoff, missing empathy, and unsafe denial. Return structured output only."
            )),
            HumanMessage(content=json.dumps({
                "final_response": state["final_response"],
                "risk": state["risk"],
                "policy_decision": state["policy_decision"],
                "handoff_decision": state["handoff_decision"],
                "action_result": state["action_result"],
            }, ensure_ascii=False)),
        ])
        verification = result.model_dump()

        # Deterministic backstop.
        text = state["final_response"].lower()
        hard_issues = [f"forbidden_phrase:{phrase}" for phrase in self.FORBIDDEN if phrase in text]
        if hard_issues:
            verification["approved_to_send"] = False
            verification["issues"] = verification.get("issues", []) + hard_issues

        if state["handoff_decision"].get("handoff_required") and "case" not in text and "review" not in text:
            verification["approved_to_send"] = False
            verification["issues"] = verification.get("issues", []) + ["missing_manual_review_language"]

        if not verification["approved_to_send"]:
            case_id = state["handoff_decision"].get("case_id")
            rewrite = (
                "I need to verify this before confirming the outcome. "
                "I’m opening a manual review case so the booking, prior communication, and applicable policy can be checked properly."
            )
            if case_id:
                rewrite += f" Your case reference is {case_id}."
            verification["safer_rewrite"] = rewrite

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary="Verified customer response using LLM/SLM and deterministic forbidden-phrase checks.",
            data=verification,
            evidence=verification.get("issues", []),
            risk_flags=verification.get("issues", []),
        )
