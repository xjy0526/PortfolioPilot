"""Supplier-neutral LLM provider protocol and adapters."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import httpx

from config import settings


@dataclass(frozen=True)
class LLMRequest:
    prompt: str
    system_instruction: str = ""
    model: str = ""
    temperature: float = 0.2
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    provider_name: str

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate one response without exposing provider-specific SDK types."""


class OptionalOpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        provider_name: str = "openai_compatible",
        timeout: float = 60.0,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.provider_name = provider_name
        self.timeout = timeout

    async def generate(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.default_model
        messages = []
        system = request.system_instruction
        if request.output_schema:
            system += (
                "\n\nReturn only JSON matching this schema exactly:\n"
                + json.dumps(request.output_schema, ensure_ascii=False)
            )
        if system.strip():
            messages.append({"role": "system", "content": system.strip()})
        messages.append({"role": "user", "content": request.prompt})
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.output_schema:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, headers=headers) as client:
            response = await client.post("/chat/completions", json=payload)
            if response.status_code >= 400 and "response_format" in payload:
                payload.pop("response_format", None)
                response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
        data = response.json()
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        return LLMResponse(text=text, provider=self.provider_name, model=model)


class QwenProvider(OptionalOpenAICompatibleProvider):
    def __init__(self, *, api_key: str, base_url: str, default_model: str):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            provider_name="qwen",
        )


class MockProvider:
    provider_name = "mock"

    def __init__(self, response: str | Callable[[LLMRequest], str] = "{}", model: str = "mock-structured"):
        self.response = response
        self.model = model
        self.calls: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        text = self.response(request) if callable(self.response) else self.response
        return LLMResponse(text=text, provider=self.provider_name, model=request.model or self.model)


def get_llm_provider() -> LLMProvider:
    provider = str(settings.AI_PROVIDER or "qwen").lower()
    if provider == "qwen" and settings.QWEN_API_KEY:
        return QwenProvider(
            api_key=settings.QWEN_API_KEY,
            base_url=settings.QWEN_BASE_URL,
            default_model=settings.QWEN_MODEL,
        )
    if provider in {"openai", "openai_compatible"} and settings.OPENAI_COMPATIBLE_API_KEY:
        return OptionalOpenAICompatibleProvider(
            api_key=settings.OPENAI_COMPATIBLE_API_KEY,
            base_url=settings.OPENAI_COMPATIBLE_BASE_URL,
            default_model=settings.OPENAI_COMPATIBLE_MODEL,
        )
    return MockProvider()
