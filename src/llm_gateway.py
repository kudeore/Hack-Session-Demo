from __future__ import annotations

"""Central LLM gateway.

This module routes to real model providers by default. Runtime guardrails live
here so every LLM call, whether it comes from the naive agent or a governed
skill, passes through the same call budget, cost budget, and kill-switch checks.
"""

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Make direct CLI/module usage pick up .env without leaking it.
    from dotenv import load_dotenv

    ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(ROOT / ".env", override=False)
except Exception:  # pragma: no cover - dotenv is a convenience, not a dependency gate.
    pass


_TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled", "enable", "kill", "stop"}
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_OLLAMA_MODEL = "llama3.2:3b"


class LLMRuntimeGuardrailError(RuntimeError):
    """Base class for LLM runtime guardrail stops."""


class LLMKillSwitchError(LLMRuntimeGuardrailError):
    """Raised or converted to a safe response when the LLM kill switch is enabled."""


class LLMBudgetExceededError(LLMRuntimeGuardrailError):
    """Raised or converted to a safe response when call/cost budget is exceeded."""


class SafeBlockedMessage:
    """Minimal LangChain-like message returned when the gateway fails closed."""

    def __init__(self, content: str):
        self.content = content


@dataclass
class LLMGuardrailState:
    provider: str = ""
    model: str = ""
    attempted_calls: int = 0
    actual_provider_calls: int = 0
    blocked_calls: int = 0
    estimated_input_tokens: int = 0
    reserved_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    events: List[Dict[str, Any]] = field(default_factory=list)


_STATE = LLMGuardrailState()
_STATE_LOCK = threading.Lock()


def reset_llm_guardrail_state() -> None:
    """Reset per-run LLM call/cost counters.

    Callable entry points invoke this at the start of each Naive/Governed run.
    Direct callers can invoke it before running a batch of cases.
    """

    global _STATE
    with _STATE_LOCK:
        _STATE = LLMGuardrailState()


def get_llm_guardrail_state() -> Dict[str, Any]:
    """Return a serialisable snapshot of LLM runtime usage and guardrail events."""

    with _STATE_LOCK:
        return {
            "provider": _STATE.provider,
            "model": _STATE.model,
            "attempted_calls": _STATE.attempted_calls,
            "actual_provider_calls": _STATE.actual_provider_calls,
            "blocked_calls": _STATE.blocked_calls,
            "estimated_input_tokens": _STATE.estimated_input_tokens,
            "reserved_output_tokens": _STATE.reserved_output_tokens,
            "estimated_cost_usd": round(_STATE.estimated_cost_usd, 8),
            "events": list(_STATE.events),
            "limits": current_llm_limits(),
            "kill_switch_enabled": is_llm_kill_switch_enabled(),
        }


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def is_llm_kill_switch_enabled() -> bool:
    """Support both env-var and file-based kill switch.

    Env var:  LLM_KILL_SWITCH=true
    File:     touch runtime/llm_kill_switch.on
              or set LLM_KILL_SWITCH_FILE=/path/to/file and create it
    """

    if _env_bool("LLM_KILL_SWITCH", False):
        return True
    kill_file = os.getenv("LLM_KILL_SWITCH_FILE", "runtime/llm_kill_switch.on")
    try:
        return Path(kill_file).expanduser().exists()
    except OSError:
        return False


def current_llm_limits() -> Dict[str, Any]:
    """Read the current runtime limits from environment variables.

    Cost is intentionally estimate-based. Override the token prices with the
    pricing for the target environment.
    """

    return {
        "max_calls_per_run": _env_int("LLM_MAX_CALLS_PER_RUN", 6),
        "max_estimated_cost_usd_per_run": _env_float("LLM_MAX_ESTIMATED_COST_USD_PER_RUN", 0.05),
        "reserved_output_tokens_per_call": _env_int("LLM_RESERVED_OUTPUT_TOKENS_PER_CALL", 1024),
        "input_cost_per_1k_tokens_usd": _env_float("LLM_INPUT_COST_PER_1K_TOKENS_USD", 0.0003),
        "output_cost_per_1k_tokens_usd": _env_float("LLM_OUTPUT_COST_PER_1K_TOKENS_USD", 0.0025),
        "fail_closed": True,
    }


def _message_text(messages: List[Any]) -> str:
    return "\n".join(getattr(m, "content", str(m)) for m in messages)


def _estimate_tokens(text: str) -> int:
    # Good enough for runtime budgeting: ~4 chars/token plus a minimum floor.
    return max(1, (len(text) + 3) // 4)


def _provider_and_model() -> tuple[str, str]:
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider in {"google", "google_genai"}:
        provider = "gemini"
    if provider == "gemini":
        return provider, os.getenv("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
    if provider == "openai":
        return provider, os.getenv("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)
    if provider == "ollama":
        return provider, os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)
    return provider, os.getenv("LLM_MODEL", "")


def _register_guardrail_event(event: Dict[str, Any]) -> None:
    with _STATE_LOCK:
        _STATE.events.append(event)


def _reserve_budget(provider: str, model: str, call_type: str, messages: List[Any]) -> Optional[str]:
    """Reserve call/cost budget before the provider call.

    Returns None when allowed. Returns a safe block reason when the call must not
    reach the provider.
    """

    limits = current_llm_limits()
    input_tokens = _estimate_tokens(_message_text(messages))
    reserved_output_tokens = int(limits["reserved_output_tokens_per_call"])
    estimated_cost = (
        (input_tokens / 1000.0) * float(limits["input_cost_per_1k_tokens_usd"])
        + (reserved_output_tokens / 1000.0) * float(limits["output_cost_per_1k_tokens_usd"])
    )

    with _STATE_LOCK:
        projected_calls = _STATE.attempted_calls + 1
        projected_cost = _STATE.estimated_cost_usd + estimated_cost
        _STATE.provider = provider
        _STATE.model = model
        _STATE.attempted_calls = projected_calls
        _STATE.estimated_input_tokens += input_tokens
        _STATE.reserved_output_tokens += reserved_output_tokens
        _STATE.estimated_cost_usd = projected_cost

        base_event = {
            "call_number": projected_calls,
            "provider": provider,
            "model": model,
            "call_type": call_type,
            "estimated_input_tokens": input_tokens,
            "reserved_output_tokens": reserved_output_tokens,
            "estimated_incremental_cost_usd": round(estimated_cost, 8),
            "estimated_total_cost_usd": round(projected_cost, 8),
        }

        if is_llm_kill_switch_enabled():
            _STATE.blocked_calls += 1
            _STATE.events.append({**base_event, "decision": "blocked", "reason": "LLM_KILL_SWITCH_ENABLED"})
            return "LLM_KILL_SWITCH_ENABLED"

        if projected_calls > int(limits["max_calls_per_run"]):
            _STATE.blocked_calls += 1
            _STATE.events.append({**base_event, "decision": "blocked", "reason": "LLM_CALL_LIMIT_EXCEEDED"})
            return "LLM_CALL_LIMIT_EXCEEDED"

        if projected_cost > float(limits["max_estimated_cost_usd_per_run"]):
            _STATE.blocked_calls += 1
            _STATE.events.append({**base_event, "decision": "blocked", "reason": "LLM_COST_BUDGET_EXCEEDED"})
            return "LLM_COST_BUDGET_EXCEEDED"

        _STATE.actual_provider_calls += 1
        _STATE.events.append({**base_event, "decision": "allowed"})
        return None


def _safe_message(reason: str) -> SafeBlockedMessage:
    return SafeBlockedMessage(
        "I’m unable to continue the LLM step because a runtime LLM governance control was triggered "
        f"({reason}). No external LLM call was made for the blocked step. Please route this case to manual review."
    )


def _safe_structured_result(schema: Any, reason: str) -> Any:
    name = getattr(schema, "__name__", str(schema))
    reasoning = f"Runtime LLM governance control triggered: {reason}. No external LLM call was made for this step."

    if name == "RiskClassification":
        return schema(
            intent="runtime_guardrail_blocked",
            risk_level="high",
            flags=["llm_runtime_guardrail_blocked", reason.lower()],
            reasoning=reasoning,
            auto_resolution_allowed=False,
        )

    if name == "SecurityIntentAssessment":
        return schema(
            attack_intent_detected=True,
            severity="high",
            confidence=1.0,
            categories=["llm_runtime_governance"],
            matched_intents=[reason],
            reasoning=reasoning,
            safe_to_continue=False,
        )

    if name == "PolicyAssessment":
        return schema(
            grounded=False,
            manual_review_required=True,
            standard_auto_refund_possible=False,
            policy_conflict=False,
            policy_reasons=["LLM runtime governance control blocked policy reasoning"],
            cited_policy_sources=["runtime_llm_governance"],
            reasoning=reasoning,
        )

    if name == "OutputVerification":
        return schema(
            approved_to_send=False,
            issues=["LLM runtime governance control blocked output verification"],
            safer_rewrite=(
                "I’m unable to complete this response automatically because a runtime LLM governance "
                "control was triggered. I’m routing this for manual review."
            ),
            reasoning=reasoning,
        )

    raise LLMRuntimeGuardrailError(reasoning)


class GuardedStructuredLLM:
    def __init__(self, model: Any, schema: Any, provider: str, model_name: str):
        self.model = model
        self.schema = schema
        self.provider = provider
        self.model_name = model_name

    def invoke(self, messages: List[Any]) -> Any:
        reason = _reserve_budget(
            provider=self.provider,
            model=self.model_name,
            call_type=f"structured:{getattr(self.schema, '__name__', 'unknown')}",
            messages=messages,
        )
        if reason:
            return _safe_structured_result(self.schema, reason)
        try:
            return self.model.invoke(messages)
        except Exception as exc:
            _register_guardrail_event(
                {
                    "provider": self.provider,
                    "model": self.model_name,
                    "call_type": f"structured:{getattr(self.schema, '__name__', 'unknown')}",
                    "decision": "provider_error",
                    "error_type": type(exc).__name__,
                }
            )
            raise


class GuardedLLM:
    def __init__(self, model: Any, provider: str, model_name: str):
        self.model = model
        self.provider = provider
        self.model_name = model_name

    def with_structured_output(self, schema: Any) -> GuardedStructuredLLM:
        if self.model is None:
            return GuardedStructuredLLM(None, schema, self.provider, self.model_name)
        return GuardedStructuredLLM(self.model.with_structured_output(schema), schema, self.provider, self.model_name)

    def invoke(self, messages: List[Any]) -> Any:
        reason = _reserve_budget(
            provider=self.provider,
            model=self.model_name,
            call_type="chat",
            messages=messages,
        )
        if reason:
            return _safe_message(reason)
        if self.model is None:
            return _safe_message("LLM_BACKEND_NOT_INITIALISED")
        try:
            return self.model.invoke(messages)
        except Exception as exc:
            _register_guardrail_event(
                {
                    "provider": self.provider,
                    "model": self.model_name,
                    "call_type": "chat",
                    "decision": "provider_error",
                    "error_type": type(exc).__name__,
                }
            )
            raise


def _build_real_model(provider: str, model_name: str) -> Any:
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "Real Gemini calls require GOOGLE_API_KEY or GEMINI_API_KEY. "
                "Set LLM_PROVIDER=gemini and provide a key, or enable LLM_KILL_SWITCH=true to fail closed without provider calls."
            )
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=temperature,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("Real OpenAI calls require OPENAI_API_KEY.")
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            temperature=temperature,
        )

    if provider == "mock":
        raise ValueError(
            "LLM_PROVIDER=mock is disabled in this version. Use gemini, openai, or ollama for real calls. "
            "Use LLM_KILL_SWITCH=true for fail-closed runs without provider calls."
        )

    raise ValueError(f"Unsupported LLM_PROVIDER={provider}")


def get_llm() -> GuardedLLM:
    """Return a guarded real LLM client.

    Every caller receives the same gateway wrapper, so call limits, estimated cost
    limits, and kill switch apply uniformly to Naive and Governed paths.
    """

    provider, model_name = _provider_and_model()

    # If a hard-stop condition is already true, do not initialise a provider
    # client and do not require an API key. The first invoke will produce a safe
    # blocked result without making an external call.
    limits = current_llm_limits()
    if (
        is_llm_kill_switch_enabled()
        or int(limits["max_calls_per_run"]) <= _STATE.attempted_calls
        or float(limits["max_estimated_cost_usd_per_run"]) <= _STATE.estimated_cost_usd
    ):
        return GuardedLLM(model=None, provider=provider, model_name=model_name)

    model = _build_real_model(provider, model_name)
    return GuardedLLM(model=model, provider=provider, model_name=model_name)
