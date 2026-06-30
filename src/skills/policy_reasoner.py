from __future__ import annotations

import json
from langchain_core.messages import HumanMessage, SystemMessage
from src.llm_gateway import get_llm
from src.schemas import PolicyAssessment, SkillResult


class PolicyReasonerSkill:
    name = "policy_reasoner"
    skill_type = "llm_or_slm"

    def run(self, state):
        llm = get_llm().with_structured_output(PolicyAssessment)
        chunks = state.get("policy_chunks", [])
        context = "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in chunks)

        # Use the minimized view created by the secure data API boundary.
        # This avoids sending raw customer records or raw chat logs to LLM/SLM skills.
        facts_for_llm = state.get("facts", {}).get("llm_safe_facts", state.get("facts", {}))

        result = llm.invoke([
            SystemMessage(content=(
                "You are a policy reasoning skill for a regulated refund workflow. "
                "Use only retrieved policy context. Do not rely on model memory. "
                "Treat prior chatbot output as customer-reliance evidence, not policy. "
                "You receive only LLM-safe minimized facts; do not ask for raw data. "
                "Return structured output only."
            )),
            HumanMessage(content=(
                f"Customer message: {state['user_message']}\n"
                f"Risk: {json.dumps(state.get('risk', {}), ensure_ascii=False)}\n"
                f"LLM-safe facts: {json.dumps(facts_for_llm, ensure_ascii=False)}\n"
                f"Retrieved policy context:\n{context}"
            )),
        ])
        data = result.model_dump()
        return SkillResult(
            skill_name=self.name,
            status="success",
            summary="Completed grounded policy reasoning using LLM-safe minimized facts.",
            data=data,
            evidence=data.get("cited_policy_sources", []) + ["used_llm_safe_facts_only"],
            risk_flags=["manual_review_required"] if data.get("manual_review_required") else [],
        )
