from __future__ import annotations

import re, json
from pathlib import Path
from src.schemas import SkillResult

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "policies"

""" In production, this retrieval could absolutely be backed by a vector database. But the vector index must be built only from approved, versioned policy sources. """


class PolicyRetrieverSkill:
    name = "policy_retriever"
    skill_type = "deterministic_retrieval"

    def _chunks(self):
        chunks = []
        for path in sorted(POLICY_DIR.glob("*")):
            if path.suffix not in [".md", ".yaml"]:
                continue
            text = path.read_text(encoding="utf-8")
            parts = re.split(r"(?=^## )", text, flags=re.MULTILINE)
            for i, part in enumerate(parts):
                if part.strip():
                    chunks.append({
                        "source": path.name,
                        "chunk_id": f"{path.name}::{i}",
                        "text": part.strip(),
                    })
        return chunks

    def run(self, state):
        facts = state.get("facts", {})
        booking = facts.get("booking", {})
        query = " ".join([
            state["user_message"],
            json.dumps(state.get("risk", {}), ensure_ascii=False),
            json.dumps(booking, ensure_ascii=False),
            "prior_misinformation" if facts.get("prior_misinformation_flag_from_logs") else "",
        ])
        q_terms = set(re.findall(r"[a-z_]+", query.lower()))
        scored = []
        for chunk in self._chunks():
            terms = set(re.findall(r"[a-z_]+", chunk["text"].lower()))
            score = len(q_terms & terms)
            for boost in ["bereavement", "misinformation", "manual", "completed", "refund", "third", "prohibited"]:
                if boost in query.lower() and boost in chunk["text"].lower():
                    score += 4
            scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [c for s, c in scored[:6] if s > 0] or [c for _, c in scored[:6]]
        return SkillResult(
            skill_name=self.name,
            status="success",
            summary=f"Retrieved {len(selected)} approved policy chunks.",
            data={"policy_chunks": selected},
            evidence=[c["chunk_id"] for c in selected],
        )
