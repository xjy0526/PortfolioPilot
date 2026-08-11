"""Deprecated import path for the pre-Qwen AI client.

Use :mod:`services.llm_client` for new code. Existing business modules may keep
using these compatibility exports during the incremental migration.
"""

from services.llm.compat import (
    Candidate,
    Content,
    FunctionCall,
    FunctionDeclaration,
    FunctionResponse,
    GenerateContentResponse,
    LegacyClientAdapter,
    Part,
    Tool,
    cache_portfolio_context,
    get_cached_content,
    get_client,
    get_daily_usage,
    get_grounded_config,
)

__all__ = [
    "Candidate",
    "Content",
    "FunctionCall",
    "FunctionDeclaration",
    "FunctionResponse",
    "GenerateContentResponse",
    "LegacyClientAdapter",
    "Part",
    "Tool",
    "cache_portfolio_context",
    "get_cached_content",
    "get_client",
    "get_daily_usage",
    "get_grounded_config",
]
