"""Temporary adapter for callers using the former generate-content interface."""
from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timezone
from typing import Any, Optional

from config import settings
from services.display_currency import format_display_money
from services.llm_client import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ToolCall,
    ToolDefinition,
    get_llm_client,
)
from time_utils import utc_now

logger = logging.getLogger(__name__)

_MAX_DAILY_CALLS = 100
_daily_call_count = 0
_daily_call_date: date | None = None
_active_cache_name: str | None = None
_context_cache_store: dict[str, str] = {}


@dataclass
class FunctionDeclaration:
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    function_declarations: list[FunctionDeclaration] | None = None


@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class FunctionResponse:
    name: str
    response: dict[str, Any] = field(default_factory=dict)


@dataclass
class Part:
    text: str | None = None
    function_call: FunctionCall | None = None
    function_response: FunctionResponse | None = None
    inline_data: dict[str, Any] | None = None

    @classmethod
    def from_function_response(cls, name: str, response: dict[str, Any]) -> "Part":
        return cls(function_response=FunctionResponse(name=name, response=response))

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str) -> "Part":
        return cls(inline_data={"data": data, "mime_type": mime_type})


@dataclass
class Content:
    role: str
    parts: list[Part]


@dataclass
class Candidate:
    content: Content


@dataclass
class GenerateContentResponse:
    text: str
    candidates: list[Candidate]


class _LegacyModels:
    def __init__(self, adapter: "LegacyClientAdapter") -> None:
        self._adapter = adapter

    async def generate_content(
        self,
        model: str,
        contents: str | list[Content],
        config: dict[str, Any] | None = None,
    ) -> GenerateContentResponse:
        return await self._adapter.generate_content(model, contents, config or {})


class _LegacyAsyncSurface:
    def __init__(self, adapter: "LegacyClientAdapter") -> None:
        self.models = _LegacyModels(adapter)


class LegacyClientAdapter:
    """Keep old callers operational while they migrate to ``ChatClient``."""

    def __init__(self) -> None:
        self.client = get_llm_client()
        self.aio = _LegacyAsyncSurface(self)
        self.api_key = getattr(self.client, "api_key", "")
        self.base_url = getattr(self.client, "base_url", "")

    async def generate_content(
        self,
        model: str,
        contents: str | list[Content],
        config: dict[str, Any],
    ) -> GenerateContentResponse:
        request = _legacy_request(model, contents, config)
        response = await self.client.generate_chat(request)
        return _legacy_response(response)


def get_client() -> LegacyClientAdapter:
    """Return a compatibility adapter backed by the configured LLM client."""
    _check_daily_limit()
    return LegacyClientAdapter()


def get_grounded_config() -> dict[str, Any]:
    """Return no search tool because the configured endpoint has no such contract."""
    return {}


def get_daily_usage() -> dict[str, Any]:
    return {
        "calls_today": _daily_call_count,
        "max_daily": _MAX_DAILY_CALLS,
        "remaining": max(0, _MAX_DAILY_CALLS - _daily_call_count),
        "date": str(_daily_call_date or utc_now().date()),
        "provider": settings.AI_PROVIDER,
        "model": settings.configured_ai_model,
    }


async def cache_portfolio_context(summary: Any) -> Optional[str]:
    """Store portfolio context in process memory for legacy callers."""
    global _active_cache_name
    if not settings.ai_configured:
        return None

    lines = [
        "Current portfolio research context:",
        f"Portfolio value: {format_display_money(summary.total_value, summary, digits=0)}",
        f"P&L: {format_display_money(summary.total_pnl, summary, digits=0, signed=True)} "
        f"({summary.total_pnl_percent:+.1f}%)",
        f"Positions: {summary.num_positions}",
        "",
    ]
    for stock in summary.stocks:
        if stock.position.ticker == "CASH":
            continue
        score = stock.score.total_score if stock.score else 0
        lines.append(
            f"{stock.position.ticker} | {stock.position.name} | {stock.position.asset_type} | "
            f"score={score:.0f} | pnl={stock.position.pnl_percent:+.1f}% | "
            f"sector={stock.position.sector}"
        )

    cache_name = f"llm-context-{uuid.uuid4().hex[:12]}"
    _context_cache_store[cache_name] = "\n".join(lines)
    _active_cache_name = cache_name
    return cache_name


def get_cached_content() -> Optional[str]:
    return _active_cache_name


def _check_daily_limit() -> None:
    global _daily_call_count, _daily_call_date
    today = utc_now().astimezone(timezone.utc).date()
    if _daily_call_date != today:
        _daily_call_count = 0
        _daily_call_date = today
    _daily_call_count += 1
    if _daily_call_count > _MAX_DAILY_CALLS:
        raise RuntimeError(f"Daily AI call limit reached ({_MAX_DAILY_CALLS}/day)")


def _legacy_request(
    model: str,
    contents: str | list[Content],
    config: dict[str, Any],
) -> ChatRequest:
    messages: list[ChatMessage] = []
    if config.get("system_instruction"):
        messages.append(ChatMessage(role="system", content=str(config["system_instruction"])))
    cached = _context_cache_store.get(str(config.get("cached_content") or ""))
    if cached:
        messages.append(ChatMessage(role="system", content=cached))

    if isinstance(contents, str):
        messages.append(ChatMessage(role="user", content=contents))
    else:
        messages.extend(_convert_contents(contents))

    tools = tuple(
        ToolDefinition(
            name=declaration.name,
            description=declaration.description,
            parameters=declaration.parameters,
        )
        for tool in config.get("tools", [])
        for declaration in (getattr(tool, "function_declarations", None) or [])
    )
    schema = config.get("response_schema") or {}
    return ChatRequest(messages=messages, model=model, tools=tools, response_schema=schema)


def _convert_contents(contents: list[Content]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    prior_call_ids: list[str] = []
    for content in contents:
        text_parts: list[str] = []
        media_parts: list[dict[str, Any]] = []
        calls: list[ToolCall] = []
        responses: list[FunctionResponse] = []
        for part in content.parts:
            if part.inline_data is not None:
                mime_type = part.inline_data.get("mime_type", "application/octet-stream")
                raw_data = part.inline_data.get("data", b"")
                if not isinstance(raw_data, bytes):
                    raise TypeError("Inline LLM input data must be bytes")
                encoded = base64.b64encode(raw_data).decode("ascii")
                if str(mime_type).startswith("audio/"):
                    media_parts.append(
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": encoded,
                                "format": str(mime_type).split("/", 1)[-1],
                            },
                        }
                    )
                elif str(mime_type).startswith("image/"):
                    media_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        }
                    )
                else:
                    raise RuntimeError(f"Unsupported inline LLM input: {mime_type}")
            if part.text:
                text_parts.append(part.text)
            if part.function_call:
                call_id = part.function_call.id or f"call_{uuid.uuid4().hex[:10]}"
                calls.append(
                    ToolCall(
                        id=call_id,
                        name=part.function_call.name,
                        arguments=part.function_call.args,
                    )
                )
            if part.function_response:
                responses.append(part.function_response)

        if calls:
            messages.append(
                ChatMessage(
                    role="assistant",
                    content="\n".join(text_parts),
                    tool_calls=tuple(calls),
                )
            )
            prior_call_ids = [call.id for call in calls]
            continue
        if responses:
            for index, response in enumerate(responses):
                call_id = prior_call_ids[min(index, len(prior_call_ids) - 1)] if prior_call_ids else ""
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=str(response.response),
                        tool_call_id=call_id,
                    )
                )
            continue
        role = "assistant" if content.role in {"assistant", "model"} else "user"
        if media_parts:
            message_content = [
                *({"type": "text", "text": text} for text in text_parts),
                *media_parts,
            ]
            messages.append(ChatMessage(role=role, content=message_content))
        else:
            messages.append(ChatMessage(role=role, content="\n".join(text_parts)))
    return messages


def _legacy_response(response: ChatResponse) -> GenerateContentResponse:
    parts = [Part(text=response.text)] if response.text else []
    parts.extend(
        Part(function_call=FunctionCall(id=call.id, name=call.name, args=call.arguments))
        for call in response.tool_calls
    )
    if not parts:
        parts.append(Part(text=""))
    return GenerateContentResponse(
        text=response.text,
        candidates=[Candidate(content=Content(role="model", parts=parts))],
    )
