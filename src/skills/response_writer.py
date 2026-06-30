from __future__ import annotations

import json
from langchain_core.messages import HumanMessage, SystemMessage
from src.llm_gateway import get_llm
from src.schemas import SkillResult


class CustomerResponseWriterSkill:
    name = "customer_response_writer"
    skill_type = "llm_or_slm"

    def run(self, state):
        llm = get_llm()
        msg = llm.invoke([
            SystemMessage(content=(
                "You are a customer-facing banking support agent. "
                "Write a concise, empathetic, safe response. "
                "Do not promise refund unless action_result.refund_executed=true. "
                "If handoff case exists, mention that a review case has been opened and include the case ID. "
                "Do not blame the customer for prior chatbot misinformation."
            )),
            HumanMessage(content=json.dumps({
                "customer_message": state["user_message"],
                "risk": state["risk"],
                "policy_decision": state["policy_decision"],
                "firewall_decision": state["firewall_decision"],
                "handoff_decision": state["handoff_decision"],
                "action_result": state["action_result"],
            }, ensure_ascii=False)),
        ])
        response = msg.content
        case_id = state.get("handoff_decision", {}).get("case_id")
        if case_id and case_id not in response:
            response = response.rstrip() + f" Your case reference is {case_id}."

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary="Drafted safe customer response.",
            data={"final_response": response},
            evidence=[],
        )
