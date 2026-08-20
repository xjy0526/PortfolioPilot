"""Server-owned HTTP identity and production read-only safeguards."""
from __future__ import annotations

import base64
import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from config import settings

logger = logging.getLogger(__name__)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Resolve Basic Auth or a development identity into request state."""

    EXEMPT_PATHS = {"/health", "/api/telegram/webhook"}

    async def dispatch(self, request, call_next):
        if not settings.auth_configured:
            self._set_unconfigured_identity(request)
            return await call_next(request)

        if any(request.url.path.startswith(path) for path in self.EXEMPT_PATHS):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
                user_ok = secrets.compare_digest(username, settings.DASHBOARD_USER)
                pass_ok = secrets.compare_digest(password, settings.DASHBOARD_PASSWORD)
                if user_ok and pass_ok:
                    request.state.principal_user_id = username
                    request.state.principal_authenticated = True
                    request.state.principal_tenant_id = settings.DASHBOARD_TENANT_ID
                    request.state.principal_roles = settings.DASHBOARD_ROLES.split(",")
                    request.state.principal_groups = (
                        settings.DASHBOARD_PERMISSION_GROUPS.split(",")
                    )
                    return await call_next(request)
            except (ValueError, UnicodeDecodeError):
                pass

        return Response(
            content="Zugang verweigert",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{settings.APP_NAME}"'},
        )

    @staticmethod
    def _set_unconfigured_identity(request) -> None:
        local_mode = settings.ENVIRONMENT in {"development", "test"}
        if local_mode:
            request.state.principal_user_id = settings.LOCAL_PRINCIPAL_USER
            request.state.principal_authenticated = True
            request.state.principal_tenant_id = settings.LOCAL_PRINCIPAL_TENANT
            request.state.principal_roles = settings.LOCAL_PRINCIPAL_ROLES.split(",")
            request.state.principal_groups = settings.LOCAL_PRINCIPAL_GROUPS.split(",")
            if settings.ALLOW_DEV_IDENTITY_HEADERS:
                request.state.principal_user_id = request.headers.get(
                    "X-Dev-User-Id", request.state.principal_user_id
                )
                request.state.principal_tenant_id = request.headers.get(
                    "X-Dev-Tenant-Id", request.state.principal_tenant_id
                )
                request.state.principal_roles = request.headers.get(
                    "X-Dev-Roles", ",".join(request.state.principal_roles)
                ).split(",")
                request.state.principal_groups = request.headers.get(
                    "X-Dev-Permission-Groups",
                    ",".join(request.state.principal_groups),
                ).split(",")
            return

        request.state.principal_user_id = "anonymous"
        request.state.principal_authenticated = False
        request.state.principal_tenant_id = "public"
        request.state.principal_roles = []
        request.state.principal_groups = ["public"]


class ReadOnlyDemoMiddleware(BaseHTTPMiddleware):
    """Reject every HTTP mutation when the deployment is a read-only demo."""

    async def dispatch(self, request, call_next):
        is_write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        if settings.read_only_demo and is_write:
            return JSONResponse(
                {"detail": "This deployment is a read-only demo"},
                status_code=403,
            )
        return await call_next(request)
