"""PortfolioPilot - Telegram Webhook Route.

Empfängt eingehende Nachrichten von Telegram via Webhook
und leitet sie an den Command Handler weiter.

Webhook ist über ein Secret-Token in der URL geschützt:
  /api/telegram/webhook/<secret>

WICHTIG: handle_update wird direkt awaited. Cloud Run hält die
HTTP-Verbindung offen solange der Handler läuft. Telegram gibt
dem Webhook 60s Zeit — genug für Voice-Processing (~20-30s).
"""
import secrets
import logging

from fastapi import APIRouter, Request
from starlette.responses import Response

from config import settings

router = APIRouter(tags=["telegram"])

logger = logging.getLogger(__name__)


@router.post("/api/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    """Empfängt Telegram-Updates via Webhook.

    Verarbeitet das Update synchron (await) bevor 200 zurückgegeben wird.
    Cloud Run hält die Instance am Leben solange der Request offen ist.
    Telegram-Webhook-Timeout: 60 Sekunden.
    """
    if not settings.TELEGRAM_WEBHOOK_SECRET or \
       not secrets.compare_digest(secret, settings.TELEGRAM_WEBHOOK_SECRET):
        logger.warning("Telegram-Webhook: Ungueltiges Secret")
        return Response(status_code=403)

    try:
        update = await request.json()
        logger.info(f"Telegram-Update empfangen: {update.get('update_id', '?')}")

        from services.telegram_bot import handle_update
        await handle_update(update)

    except Exception as e:
        logger.error(f"Telegram-Webhook-Fehler: {e}", exc_info=True)

    return Response(status_code=200)

