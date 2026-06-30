from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class SkillResult(BaseModel):
    skill_name: str
    status: Literal["success", "blocked", "error"]
    summary: str
    data: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)


class RiskClassification(BaseModel):
    intent: str
    risk_level: Literal["low", "medium", "high"]
    flags: List[str] = Field(default_factory=list)
    reasoning: str
    auto_resolution_allowed: bool


class SecurityIntentAssessment(BaseModel):
    attack_intent_detected: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    categories: List[str] = Field(default_factory=list)
    matched_intents: List[str] = Field(default_factory=list)
    reasoning: str
    safe_to_continue: bool


class PolicyAssessment(BaseModel):
    grounded: bool
    manual_review_required: bool
    standard_auto_refund_possible: bool
    policy_conflict: bool = False
    policy_reasons: List[str] = Field(default_factory=list)
    cited_policy_sources: List[str] = Field(default_factory=list)
    reasoning: str


class OutputVerification(BaseModel):
    approved_to_send: bool
    issues: List[str] = Field(default_factory=list)
    safer_rewrite: Optional[str] = None
    reasoning: str


class AgentState(TypedDict, total=False):
    customer_id: str
    booking_id: str
    customer_name: str
    user_message: str
    requested_action: str
    use_case: Dict[str, Any]
    agent_card: Dict[str, Any]
    skill_registry: Dict[str, Any]
    risk: Dict[str, Any]
    security_decision: Dict[str, Any]
    facts: Dict[str, Any]
    policy_chunks: List[Dict[str, str]]
    policy_assessment: Dict[str, Any]
    refund_calculation: Dict[str, Any]
    policy_decision: Dict[str, Any]
    firewall_decision: Dict[str, Any]
    handoff_decision: Dict[str, Any]
    action_result: Dict[str, Any]
    final_response: str
    output_verification: Dict[str, Any]
    audit: List[Dict[str, Any]]
    audit_sequence: int
    context: Dict[str, Any]
    runtime: Dict[str, Any]
    checkpoints: List[Dict[str, Any]]
    idempotency: Dict[str, Any]
    last_audit_event: Dict[str, Any]
