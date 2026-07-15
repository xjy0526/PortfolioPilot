"""Provider-neutral import surface for legacy rich-content/tool callers.

New structured business flows should use ``LLMProvider``. This module keeps the
old content/tool types behind a neutral filename while those flows are migrated.
"""
from services.vertex_ai import Content, FunctionDeclaration, Part, Tool


def get_client():
    from services import vertex_ai
    return vertex_ai.get_client()


def get_grounded_config():
    from services import vertex_ai
    return vertex_ai.get_grounded_config()


def get_cached_content():
    from services import vertex_ai
    return vertex_ai.get_cached_content()


async def cache_portfolio_context(summary):
    from services import vertex_ai
    return await vertex_ai.cache_portfolio_context(summary)
