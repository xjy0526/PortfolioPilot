"""Provider-neutral chat client for Qwen and OpenAI-compatible endpoints."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence

import httpx

from config import Settings, settings


@dataclass(frozen=True)
class ToolDefinition:
    """One function tool exposed to an OpenAI-compatible model."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    """A model-requested function call."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatMessage:
    """Provider-neutral chat message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""


@dataclass(frozen=True)
class ChatRequest:
    """Request accepted by every configured chat client."""

    messages: Sequence[ChatMessage]
    model: str = ""
    temperature: float = 0.2
    tools: Sequence[ToolDefinition] = ()
    response_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatResponse:
    """Normalized response returned by an LLM endpoint."""

    text: str
    provider: str
    model: str
    tool_calls: tuple[ToolCall, ...] = ()


class ChatClient(Protocol):
    provider_name: str

    async def generate_chat(self, request: ChatRequest) -> ChatResponse:
        """Generate one normalized chat response."""


class OpenAICompatibleClient:
    """Minimal async client for OpenAI-compatible Chat Completions APIs."""

    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_completions_url = f"{self.base_url}/chat/completions"
        self.default_model = default_model
        self.timeout = timeout

    def resolve_model(self, requested_model: str) -> str:
        if not requested_model or requested_model.lower().startswith("gemini-"):
            return self.default_model
        return requested_model

    async def generate_chat(self, request: ChatRequest) -> ChatResponse:
        model = self.resolve_model(request.model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_serialize_message(message) for message in request.messages],
            "temperature": request.temperature,
        }
        if request.tools:
            payload["tools"] = [_serialize_tool(tool) for tool in request.tools]
            payload["tool_choice"] = "auto"
        if request.response_schema:
            payload["response_format"] = {"type": "json_object"}
            _append_schema_instruction(payload["messages"], request.response_schema)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=headers,
        ) as client:
            response = await client.post(self.chat_completions_url, json=payload)
            if response.status_code >= 400 and "response_format" in payload:
                payload.pop("response_format", None)
                response = await client.post(self.chat_completions_url, json=payload)
            response.raise_for_status()

        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return ChatResponse(
            text=str(message.get("content") or ""),
            provider=self.provider_name,
            model=model,
            tool_calls=tuple(_parse_tool_call(item) for item in message.get("tool_calls") or []),
        )


class QwenClient(OpenAICompatibleClient):
    """Qwen client using DashScope's OpenAI-compatible endpoint."""

    provider_name = "qwen"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        reasoning_model: str = "",
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            timeout=timeout,
        )
        self.reasoning_model = reasoning_model

    def resolve_model(self, requested_model: str) -> str:
        requested = requested_model.strip()
        if not requested or requested.lower().startswith("gemini-"):
            if self.reasoning_model and "pro" in requested.lower():
                return self.reasoning_model
            return self.default_model
        return requested


def get_llm_client(config: Settings | None = None) -> ChatClient:
    """Build the explicitly configured real LLM client.

    Mock behavior remains in the evaluation and structured-analysis layers. This
    factory never silently turns a missing credential into a real-model result.
    """
    current = config or settings
    provider = str(current.AI_PROVIDER or "qwen").strip().lower()
    if provider == "qwen":
        if not current.QWEN_API_KEY:
            raise RuntimeError("QWEN_API_KEY is not configured")
        return QwenClient(
            api_key=current.QWEN_API_KEY,
            base_url=current.QWEN_BASE_URL,
            default_model=current.QWEN_MODEL,
            reasoning_model=current.QWEN_REASONING_MODEL,
        )
    if provider in {"openai", "openai_compatible"}:
        if not current.OPENAI_COMPATIBLE_API_KEY:
            raise RuntimeError("OPENAI_COMPATIBLE_API_KEY is not configured")
        return OpenAICompatibleClient(
            api_key=current.OPENAI_COMPATIBLE_API_KEY,
            base_url=current.OPENAI_COMPATIBLE_BASE_URL,
            default_model=current.OPENAI_COMPATIBLE_MODEL,
        )
    raise ValueError(f"Unsupported AI_PROVIDER: {current.AI_PROVIDER}")


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _serialize_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _append_schema_instruction(messages: list[dict[str, Any]], schema: dict[str, Any]) -> None:
    instruction = "Return only JSON matching this schema:\n" + json.dumps(
        schema,
        ensure_ascii=False,
    )
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        if isinstance(existing, str):
            messages[0]["content"] = f"{existing}\n\n{instruction}".strip()
        else:
            messages.insert(0, {"role": "system", "content": instruction})
    else:
        messages.insert(0, {"role": "system", "content": instruction})


def _parse_tool_call(payload: dict[str, Any]) -> ToolCall:
    function = payload.get("function") or {}
    raw_arguments = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return ToolCall(
        id=str(payload.get("id") or ""),
        name=str(function.get("name") or ""),
        arguments=arguments,
    )
