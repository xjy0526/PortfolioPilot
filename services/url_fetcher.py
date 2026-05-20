"""PortfolioPilot - URL Content Fetcher.

Shared Helper zum Abrufen und Konvertieren von externen Webseiten:
  - HTML → Plain Text Konvertierung (ohne externe Dependencies)
  - Content-Length-Limit und Timeout
  - Wird von trade_advisor.py (Function Calling) und telegram_bot.py (Chat) genutzt
"""
import re
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Limits
_DEFAULT_MAX_CHARS = 8000
_DEFAULT_TIMEOUT = 15  # Sekunden
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB max download


def _html_to_text(html: str) -> str:
    """Konvertiert HTML zu lesbarem Plain Text.

    Einfache Implementierung ohne externe Dependencies (kein BeautifulSoup nötig).
    """
    # Script und Style Tags komplett entfernen
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<header[^>]*>.*?</header>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Kommentare entfernen
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # Block-Tags zu Newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|article|section)>", "\n", text, flags=re.IGNORECASE)

    # Alle verbleibenden Tags entfernen
    text = re.sub(r"<[^>]+>", " ", text)

    # HTML Entities dekodieren
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&apos;", "'")
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)

    # Whitespace normalisieren
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = text.strip()

    return text


def extract_urls(text: str) -> list[str]:
    """Extrahiert URLs aus einem Text-String.

    Returns:
        Liste der gefundenen URLs (max 5)
    """
    url_pattern = r'https?://[^\s<>"\')\]]+[^\s<>"\')\].,;:!?]'
    urls = re.findall(url_pattern, text)
    # Deduplizieren, Reihenfolge beibehalten, max 5
    seen = set()
    unique = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
        if len(unique) >= 5:
            break
    return unique


async def fetch_url_text(
    url: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Ruft eine URL ab und gibt den Inhalt als Plain Text zurück.

    Args:
        url: Die abzurufende URL (muss http:// oder https:// sein)
        max_chars: Maximale Zeichenzahl des zurückgegebenen Textes
        timeout: Timeout in Sekunden

    Returns:
        Text-Inhalt der Seite (ggf. gekürzt)
    """
    if not url or not url.startswith(("http://", "https://")):
        return f"[Ungültige URL: {url}]"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; PortfolioPilot/1.0)",
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            # Content-Size prüfen
            content_length = len(response.content)
            if content_length > _MAX_RESPONSE_BYTES:
                return f"[URL-Inhalt zu groß: {content_length / 1024 / 1024:.1f} MB, max {_MAX_RESPONSE_BYTES / 1024 / 1024:.0f} MB]"

            content_type = response.headers.get("content-type", "")
            raw_text = response.text

            # HTML → Text konvertieren
            if "html" in content_type.lower():
                text = _html_to_text(raw_text)
            elif "json" in content_type.lower():
                text = raw_text[:max_chars]
            else:
                # Plain Text oder unbekannt
                text = raw_text

            # Auf max_chars kürzen
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n[... gekürzt, Originalseite enthält mehr Inhalt]"

            if not text.strip():
                return f"[Kein lesbarer Text auf {url} gefunden — Seite benötigt möglicherweise JavaScript]"

            logger.info(f"🌐 URL abgerufen: {url} ({len(text)} Zeichen)")
            return text

    except httpx.TimeoutException:
        logger.warning(f"URL-Timeout: {url}")
        return f"[Timeout beim Abrufen von {url} — Seite nicht erreichbar]"
    except httpx.HTTPStatusError as e:
        logger.warning(f"URL HTTP-Fehler: {url} → {e.response.status_code}")
        return f"[HTTP-Fehler {e.response.status_code} beim Abrufen von {url}]"
    except Exception as e:
        logger.warning(f"URL-Fetch fehlgeschlagen: {url} → {e}")
        return f"[Fehler beim Abrufen von {url}: {type(e).__name__}]"


async def fetch_multiple_urls(
    urls: list[str],
    max_chars_per_url: int = 4000,
) -> dict[str, str]:
    """Ruft mehrere URLs parallel ab.

    Args:
        urls: Liste von URLs (max 5)
        max_chars_per_url: Maximale Zeichenzahl pro URL

    Returns:
        Dict {url: text_content}
    """
    import asyncio

    urls = urls[:5]  # Sicherheitslimit
    tasks = [fetch_url_text(url, max_chars=max_chars_per_url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = {}
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            output[url] = f"[Fehler: {type(result).__name__}]"
        else:
            output[url] = result

    return output
