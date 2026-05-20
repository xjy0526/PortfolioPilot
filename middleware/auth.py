"""PortfolioPilot - Authentifizierungs-Middleware.

Basic Auth Schutz für das Dashboard und API-Endpoints.
Der Telegram-Webhook ist davon ausgenommen (hat eigenes Secret).
"""
import secrets
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from config import settings

logger = logging.getLogger(__name__)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth Middleware für FastAPI.

    Schützt alle Routen außer:
    - /api/telegram/webhook (hat eigenes Secret-Token)
    - /health (für Cloud Run Health Checks)
    """

    EXEMPT_PATHS = {"/health", "/api/telegram/webhook"}

    async def dispatch(self, request, call_next):
        # Prüfe ob Auth konfiguriert ist
        if not settings.auth_configured:
            return await call_next(request)

        # Prüfe ob Pfad ausgenommen ist
        path = request.url.path
        for exempt in self.EXEMPT_PATHS:
            if path.startswith(exempt):
                return await call_next(request)

        # Basic Auth Header prüfen
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)

                # Timing-safe Vergleich (verhindert Timing-Attacks)
                user_ok = secrets.compare_digest(username, settings.DASHBOARD_USER)
                pass_ok = secrets.compare_digest(password, settings.DASHBOARD_PASSWORD)

                if user_ok and pass_ok:
                    return await call_next(request)
            except Exception:
                pass

        # Nicht authentifiziert → Login-Dialog
        return Response(
            content="Zugang verweigert",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{settings.APP_NAME}"'},
        )
