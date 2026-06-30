from __future__ import annotations

from typing import Any, Dict

from src.audit import AuditLogger
from src.state_store import checkpoint


def run_skill(state: Dict[str, Any], skill, output_key: str | None = None, data_key: str | None = None):
    """
    Central runtime wrapper.

    The agent does not call raw functions directly. It calls registered skills
    through this wrapper so that every skill invocation is traced, audited, and
    optionally checkpointed consistently.
    """
    result = skill.run(state)
    state = AuditLogger.append(
        state,
        step=result.skill_name,
        event_type="skill_invocation",
        details=result.model_dump(),
    )

    if output_key:
        if data_key:
            state[output_key] = result.data[data_key]
        else:
            state[output_key] = result.data

    if state.get("runtime", {}).get("checkpoint_enabled", True):
        state = checkpoint(state, checkpoint_name=result.skill_name)

    return state
