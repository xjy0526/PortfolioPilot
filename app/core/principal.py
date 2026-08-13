"""Server-derived identity and authorization context for API operations."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from config import settings


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    permission_groups: frozenset[str]

    def has_group(self, group: str) -> bool:
        return group.strip().lower() in self.permission_groups

    def require_group(self, group: str) -> None:
        if not self.has_group(group):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal lacks required group: {group}",
            )

    def require_any_group(self, *groups: str) -> None:
        if not any(self.has_group(group) for group in groups):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal lacks one of required groups: {', '.join(groups)}",
            )


async def get_principal(request: Request) -> Principal:
    """Resolve identity only from server-owned request state and configuration."""
    local_mode = settings.ENVIRONMENT == "development"
    user_id = str(
        getattr(request.state, "principal_user_id", "")
        or (settings.LOCAL_PRINCIPAL_USER if local_mode else "anonymous")
    ).strip()
    raw_groups = getattr(request.state, "principal_groups", None)
    if raw_groups is None:
        raw_groups = (
            settings.LOCAL_PRINCIPAL_GROUPS.split(",") if local_mode else ["public"]
        )
    groups = frozenset(str(item).strip().lower() for item in raw_groups if str(item).strip())
    return Principal(user_id=user_id or "anonymous", permission_groups=groups or frozenset({"public"}))
