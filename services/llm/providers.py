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
    usage: dict[str, Any] = field(default_factory=dict)
    usage_source: str = "estimated"


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
        http_client: httpx.AsyncClient | None = None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.provider_name = provider_name
        self.timeout = timeout
        self.http_client = http_client

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
        if self.http_client is None:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await self._post(client, headers, payload)
        else:
            response = await self._post(self.http_client, headers, payload)
        data = response.json()
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResponse(
            text=text,
            provider=self.provider_name,
            model=model,
            usage=usage,
            usage_source="provider" if usage else "estimated",
        )

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400 and "response_format" in payload:
            payload.pop("response_format", None)
            response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response


class QwenProvider(OptionalOpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            provider_name="qwen",
            http_client=http_client,
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


async def get_llm_provider() -> LLMProvider:
    from app.core.resources import get_resources

    resources = await get_resources()
    provider = str(settings.AI_PROVIDER or "qwen").lower()
    if provider == "qwen" and settings.QWEN_API_KEY:
        return QwenProvider(
            api_key=settings.QWEN_API_KEY,
            base_url=settings.QWEN_BASE_URL,
            default_model=settings.QWEN_MODEL,
            http_client=resources.http_client,
        )
    if provider in {"openai", "openai_compatible"} and settings.OPENAI_COMPATIBLE_API_KEY:
        return OptionalOpenAICompatibleProvider(
            api_key=settings.OPENAI_COMPATIBLE_API_KEY,
            base_url=settings.OPENAI_COMPATIBLE_BASE_URL,
            default_model=settings.OPENAI_COMPATIBLE_MODEL,
            http_client=resources.http_client,
        )
    return MockProvider()
