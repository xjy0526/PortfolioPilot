"""PortfolioPilot - URL Fetcher Tests.

Tests für:
  - HTML→Text Konvertierung
  - URL Extraktion aus Text
  - Content-Truncation (max_chars)
  - Error-Handling (Timeout, HTTP-Fehler, ungültige URLs)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from services.url_fetcher import (
    _html_to_text,
    extract_urls,
    fetch_url_text,
    fetch_multiple_urls,
)


# ─────────────────────────────────────────────────────────────
# Tests: HTML → Text
# ─────────────────────────────────────────────────────────────

class TestHtmlToText:
    def test_strips_tags(self):
        html = "<p>Hello <strong>World</strong></p>"
        text = _html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "<" not in text

    def test_removes_script_and_style(self):
        html = """
        <html>
            <script>var x = 1;</script>
            <style>.foo { color: red; }</style>
            <p>Visible content</p>
        </html>
        """
        text = _html_to_text(html)
        assert "Visible content" in text
        assert "var x" not in text
        assert "color" not in text

    def test_removes_nav_footer_header(self):
        html = """
        <nav>Navigation</nav>
        <article>Main Content</article>
        <footer>Footer stuff</footer>
        """
        text = _html_to_text(html)
        assert "Main Content" in text
        assert "Navigation" not in text
        assert "Footer stuff" not in text

    def test_decodes_html_entities(self):
        html = "<p>Price: &lt;€100&gt; &amp; 20% off</p>"
        text = _html_to_text(html)
        assert "<€100>" in text
        assert "& 20%" in text

    def test_handles_br_tags(self):
        html = "Line 1<br/>Line 2<br>Line 3"
        text = _html_to_text(html)
        assert "Line 1" in text
        assert "Line 2" in text

    def test_handles_empty_html(self):
        assert _html_to_text("") == ""

    def test_handles_plain_text(self):
        text = _html_to_text("Just plain text")
        assert text == "Just plain text"


# ─────────────────────────────────────────────────────────────
# Tests: URL Extraktion
# ─────────────────────────────────────────────────────────────

class TestExtractUrls:
    def test_extracts_single_url(self):
        text = "Schau dir das an: https://finance.yahoo.com/news/nvidia"
        urls = extract_urls(text)
        assert len(urls) == 1
        assert urls[0] == "https://finance.yahoo.com/news/nvidia"

    def test_extracts_multiple_urls(self):
        text = "https://example.com/a und https://example.com/b"
        urls = extract_urls(text)
        assert len(urls) == 2

    def test_deduplicates_urls(self):
        text = "https://example.com/a https://example.com/a"
        urls = extract_urls(text)
        assert len(urls) == 1

    def test_max_5_urls(self):
        text = " ".join(f"https://example.com/{i}" for i in range(10))
        urls = extract_urls(text)
        assert len(urls) == 5

    def test_no_urls(self):
        text = "Keine URLs hier, nur Text."
        urls = extract_urls(text)
        assert len(urls) == 0

    def test_ignores_non_http(self):
        text = "ftp://server.com und file:///local"
        urls = extract_urls(text)
        assert len(urls) == 0

    def test_handles_urls_with_params(self):
        text = "https://api.example.com/data?key=123&format=json"
        urls = extract_urls(text)
        assert len(urls) == 1
        assert "key=123" in urls[0]


# ─────────────────────────────────────────────────────────────
# Tests: fetch_url_text
# ─────────────────────────────────────────────────────────────

class TestFetchUrlText:
    @pytest.mark.asyncio
    async def test_invalid_url(self):
        result = await fetch_url_text("not-a-url")
        assert "[Ungültige URL" in result

    @pytest.mark.asyncio
    async def test_empty_url(self):
        result = await fetch_url_text("")
        assert "[Ungültige URL" in result

    @pytest.mark.asyncio
    async def test_successful_html_fetch(self):
        """Mocked HTTP-Antwort mit HTML-Inhalt."""
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>Test content from web page</p></body></html>"
        mock_response.content = mock_response.text.encode()
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.url_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_url_text("https://example.com/article")

        assert "Test content from web page" in result

    @pytest.mark.asyncio
    async def test_truncates_to_max_chars(self):
        """Content wird auf max_chars gekürzt."""
        long_content = "A" * 20000
        mock_response = MagicMock()
        mock_response.text = long_content
        mock_response.content = long_content.encode()
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.url_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_url_text("https://example.com", max_chars=100)

        assert len(result) < 200  # 100 + truncation notice
        assert "gekürzt" in result

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Timeout gibt freundliche Fehlermeldung."""
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_httpx.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.url_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_url_text("https://slow-site.com")

        assert "[Timeout" in result

    @pytest.mark.asyncio
    async def test_http_error_handling(self):
        """HTTP-Fehler gibt Status-Code zurück."""
        import httpx as _httpx

        mock_response = MagicMock()
        mock_response.status_code = 403
        error = _httpx.HTTPStatusError(
            "Forbidden",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=error)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("services.url_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_url_text("https://forbidden.com")

        assert "[HTTP-Fehler 403" in result


# ─────────────────────────────────────────────────────────────
# Tests: fetch_multiple_urls
# ─────────────────────────────────────────────────────────────

class TestFetchMultipleUrls:
    @pytest.mark.asyncio
    async def test_fetches_multiple(self):
        """Ruft mehrere URLs parallel ab."""
        with patch("services.url_fetcher.fetch_url_text") as mock_fetch:
            mock_fetch.side_effect = [
                "Content A",
                "Content B",
            ]
            result = await fetch_multiple_urls(
                ["https://a.com", "https://b.com"],
                max_chars_per_url=1000,
            )

        assert len(result) == 2
        assert "Content A" in result["https://a.com"]
        assert "Content B" in result["https://b.com"]

    @pytest.mark.asyncio
    async def test_limits_to_5_urls(self):
        """Max 5 URLs werden verarbeitet."""
        urls = [f"https://example.com/{i}" for i in range(10)]
        with patch("services.url_fetcher.fetch_url_text") as mock_fetch:
            mock_fetch.return_value = "Content"
            result = await fetch_multiple_urls(urls)

        assert len(result) == 5
