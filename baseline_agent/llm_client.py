from __future__ import annotations

"""Direct LLM client for the standalone baseline agent.

This module intentionally does not import src.llm_gateway. The baseline path uses
a simple provider call, so runtime guardrails, kill switches, call budgets, and
structured-output wrappers from the governed harness are not applied here.
"""

import os
from pathlib import Path
from typing import Any, List, Optional

try:
    from dotenv import load_dotenv

    ROOT = Path(__file__).resolve().parents[1]
    load_dotenv(ROOT / ".env", override=False)
except Exception:  # pragma: no cover
    pass


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"


class DirectLLMClient:
    """Thin adapter around a chat model provider with no guardrail wrapper."""

    def __init__(
        self,
        *,
        provider: str = "gemini",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
    ) -> None:
        self.provider = (provider or "gemini").strip().lower()
        if self.provider in {"google", "google_genai"}:
            self.provider = "gemini"
        self.model_name = model or self._default_model()
        self.api_key = api_key
        self.temperature = temperature
        self.model = self._build_model()

    def _default_model(self) -> str:
        if self.provider == "gemini":
            return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        if self.provider == "openai":
            return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        if self.provider == "ollama":
            return os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        return os.getenv("LLM_MODEL", "")

    def _build_model(self) -> Any:
        if self.provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            key = self.api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not key:
                raise ValueError("Baseline Gemini call requires GOOGLE_API_KEY/GEMINI_API_KEY or api_key=.")
            return ChatGoogleGenerativeAI(
                model=self.model_name,
                google_api_key=key,
                temperature=self.temperature,
            )

        if self.provider == "openai":
            from langchain_openai import ChatOpenAI

            if self.api_key:
                os.environ["OPENAI_API_KEY"] = self.api_key
            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError("Baseline OpenAI call requires OPENAI_API_KEY or api_key=.")
            return ChatOpenAI(model=self.model_name, temperature=self.temperature)

        if self.provider == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(model=self.model_name, temperature=self.temperature)

        raise ValueError(f"Unsupported baseline LLM provider={self.provider!r}")

    def invoke(self, messages: List[Any]) -> Any:
        return self.model.invoke(messages)

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_name,
            "temperature": self.temperature,
            "call_path": "baseline_agent.llm_client.DirectLLMClient",
            "guardrail_gateway_used": False,
        }
