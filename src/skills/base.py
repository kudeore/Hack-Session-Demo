from __future__ import annotations

from typing import Any, Dict
from src.schemas import SkillResult


class Skill:
    name: str = "base_skill"
    skill_type: str = "abstract"

    def run(self, state: Dict[str, Any]) -> SkillResult:
        raise NotImplementedError
