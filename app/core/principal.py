"""Server-derived identity and authorization context for API operations."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

PortfolioAccess = Literal["read", "write", "admin"]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    # Keep permission_groups as the second argument for compatibility with
    # existing internal callers. Groups govern document visibility only.
    permission_groups: frozenset[str] = frozenset({"public"})
    authenticated: bool = True
    tenant_id: str = "default"
    roles: frozenset[str] = frozenset()

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

    def has_role(self, role: str) -> bool:
        return role.strip().lower() in self.roles

    def require_authenticated(self) -> None:
        if not self.authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

    def require_role(self, *roles: str) -> None:
        normalized = tuple(item.strip().lower() for item in roles if item.strip())
        self.require_authenticated()
        if not any(self.has_role(role) for role in normalized):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal lacks one of required roles: {', '.join(normalized)}",
            )

    @property
    def is_platform_admin(self) -> bool:
        return self.authenticated and self.has_role("platform_admin")


def require_tenant_access(principal: Principal, tenant_id: str) -> None:
    """Require tenant affinity unless a platform administrator is operating."""
    if principal.is_platform_admin:
        return
    if not principal.authenticated or principal.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")


async def require_portfolio_access(
    session: AsyncSession,
    principal: Principal,
    portfolio_id: uuid.UUID,
    access: PortfolioAccess = "read",
):
    """Resolve an authorized portfolio without revealing inaccessible UUIDs."""
    from app.db.repositories.identity import PortfolioRepository

    if access in {"write", "admin"}:
        principal.require_authenticated()
    portfolio = await PortfolioRepository(session).get_accessible(
        portfolio_id=portfolio_id,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        access=access,
        platform_admin=principal.is_platform_admin,
    )
    if portfolio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


def require_writable() -> None:
    """Dependency-level guard for write operations outside HTTP middleware."""
    if settings.read_only_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This deployment is a read-only demo",
        )


async def get_principal(request: Request) -> Principal:
    """Resolve identity only from server-owned request state and configuration."""
    local_mode = settings.ENVIRONMENT in {"development", "test"}
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
    raw_roles = getattr(request.state, "principal_roles", None)
    if raw_roles is None:
        raw_roles = settings.LOCAL_PRINCIPAL_ROLES.split(",") if local_mode else []
    roles = frozenset(str(item).strip().lower() for item in raw_roles if str(item).strip())
    authenticated = bool(
        getattr(request.state, "principal_authenticated", local_mode)
    )
    tenant_id = str(
        getattr(request.state, "principal_tenant_id", "")
        or (settings.LOCAL_PRINCIPAL_TENANT if local_mode else "public")
    ).strip()
    return Principal(
        user_id=user_id or "anonymous",
        permission_groups=groups or frozenset({"public"}),
        authenticated=authenticated,
        tenant_id=tenant_id or "public",
        roles=roles,
    )
