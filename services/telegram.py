"""PortfolioPilot - Telegram Bot Service.

Sendet Nachrichten über die Telegram Bot API.
Unterstützt Markdown-Formatierung und automatisches Splitting
bei langen Nachrichten (>4096 Zeichen Telegram-Limit).
"""
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


async def send_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "Markdown",
) -> bool:
    """Sendet eine Nachricht über Telegram.

    Args:
        text: Nachrichtentext (Markdown-formatiert)
        chat_id: Telegram Chat-ID (Default aus Config)
        parse_mode: "Markdown" oder "HTML"

    Returns:
        True bei Erfolg, False bei Fehler
    """
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram nicht konfiguriert (TELEGRAM_BOT_TOKEN oder TELEGRAM_CHAT_ID fehlt)")
        return False

    url = TELEGRAM_API_URL.format(token=token)

    # Splitten bei langen Nachrichten
    chunks = _split_message(text)

    success = True
    async with httpx.AsyncClient(timeout=30.0) as client:
        for chunk in chunks:
            try:
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                }
                response = await client.post(url, json=payload)

                if response.status_code != 200:
                    # Fallback: ohne parse_mode senden (falls Markdown-Fehler)
                    logger.warning(f"Telegram Markdown fehlgeschlagen ({response.status_code}), sende als Plain Text")
                    payload["parse_mode"] = ""
                    response = await client.post(url, json=payload)

                if response.status_code == 200:
                    logger.debug("Telegram-Nachricht gesendet")
                else:
                    logger.error(f"Telegram-Fehler: {response.status_code} - {response.text}")
                    success = False

            except Exception as e:
                logger.error(f"Telegram-Senden fehlgeschlagen: {e}")
                success = False

    return success


async def send_report(title: str, sections: list[tuple[str, str]]) -> bool:
    """Sendet einen strukturierten Report mit Titel und Abschnitten.

    Args:
        title: Report-Titel (z.B. "📊 PortfolioPilot Daily Report")
        sections: Liste von (emoji_header, content) Tupeln

    Returns:
        True bei Erfolg
    """
    parts = [f"*{title}*", ""]

    for header, content in sections:
        if content.strip():
            parts.append(f"*{header}*")
            parts.append(content)
            parts.append("")

    full_text = "\n".join(parts)
    return await send_message(full_text)


async def download_telegram_file(file_id: str) -> bytes:
    """Lädt eine Datei von Telegram herunter (z.B. Sprachnachricht).

    Args:
        file_id: Telegram file_id (aus voice/document/photo Objekten)

    Returns:
        Datei-Inhalt als Bytes

    Raises:
        RuntimeError: Wenn Download fehlschlägt
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nicht konfiguriert")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Schritt 1: File-Path von Telegram holen
        get_file_url = f"https://api.telegram.org/bot{token}/getFile"
        resp = await client.get(get_file_url, params={"file_id": file_id})

        if resp.status_code != 200:
            raise RuntimeError(f"getFile fehlgeschlagen: {resp.status_code}")

        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile Fehler: {data}")

        file_path = data["result"]["file_path"]

        # Schritt 2: Datei herunterladen
        download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        file_resp = await client.get(download_url)

        if file_resp.status_code != 200:
            raise RuntimeError(f"File-Download fehlgeschlagen: {file_resp.status_code}")

        logger.info(f"📥 Telegram-Datei geladen: {file_path} ({len(file_resp.content)} Bytes)")
        return file_resp.content


def _split_message(text: str) -> list[str]:
    """Splittet lange Nachrichten an Zeilenumbrüchen.

    Telegram erlaubt max 4096 Zeichen pro Nachricht.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks = []
    current = ""

    for line in text.split("\n"):
        # +1 für den Zeilenumbruch
        if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
            if current:
                chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"

    if current.strip():
        chunks.append(current.rstrip())

    return chunks if chunks else [text[:MAX_MESSAGE_LENGTH]]
