"""PortfolioPilot - Parqet OAuth2 Routes

Endpoints fuer Parqet Connect API OAuth2 PKCE-Autorisierung.
Einmaliger Setup: /api/parqet/authorize → Login bei Parqet → Callback mit Tokens.
Danach funktioniert der automatische Token-Refresh auf Cloud Run.
"""
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from fetchers.parqet_auth import (
    generate_oauth_url,
    exchange_code_for_tokens,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_redirect_uri(request: Request) -> str:
    """Baut die Redirect URI mit korrektem Schema (https auf Cloud Run)."""
    base = str(request.base_url).rstrip("/")
    # Cloud Run terminiert TLS am Load Balancer → App sieht http://
    # Aber die registrierte Redirect URI ist https://
    if os.getenv("ENVIRONMENT") == "production" and base.startswith("http://"):
        base = "https://" + base[7:]
    return f"{base}/api/parqet/callback"


@router.get("/api/parqet/authorize")
async def parqet_authorize(request: Request):
    """Startet den OAuth2-Login bei Parqet.

    Oeffne diese URL im Browser → du wirst zu Parqet weitergeleitet.
    Nach dem Login kommt der Callback mit den Tokens.
    """
    redirect_uri = _get_redirect_uri(request)
    auth_url, _ = generate_oauth_url(redirect_uri)
    return RedirectResponse(url=auth_url)


@router.get("/api/parqet/callback")
async def parqet_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """OAuth2-Callback von Parqet.

    Empfaengt den Authorization Code und tauscht ihn gegen Tokens.
    """
    if error:
        return HTMLResponse(
            f"<h2>❌ Parqet Autorisierung fehlgeschlagen</h2><p>{error}</p>",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            "<h2>❌ Kein Authorization Code erhalten</h2>",
            status_code=400,
        )

    redirect_uri = _get_redirect_uri(request)
    token = await exchange_code_for_tokens(code, state, redirect_uri)

    if token:
        # Portfolio neu laden und WebSocket-Streamer subscriben (Background)
        import asyncio
        from main import reload_portfolio_and_subscribe
        asyncio.create_task(reload_portfolio_and_subscribe())

        return HTMLResponse(
            "<h2>✅ Parqet verbunden!</h2>"
            "<p>Tokens gespeichert. Portfolio wird automatisch geladen...</p>"
            "<p><a href='/'>→ Zum Dashboard</a></p>"
        )
    else:
        return HTMLResponse(
            "<h2>❌ Token-Tausch fehlgeschlagen</h2>"
            "<p>Bitte erneut versuchen: <a href='/api/parqet/authorize'>/api/parqet/authorize</a></p>",
            status_code=500,
        )

