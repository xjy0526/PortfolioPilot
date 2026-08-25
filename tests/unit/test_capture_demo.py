"""Guards for the real-browser deterministic demo capture workflow."""
from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts import capture_demo


ROOT = Path(__file__).resolve().parents[2]


class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "_FakeLocator":
        return self

    def click(self) -> None:
        self.page.clicked.append(self.selector)

    def wait_for(self, **_: object) -> None:
        return None

    def inner_text(self) -> str:
        return self.page.body_text

    def evaluate(self, _: str) -> float:
        return self.page.horizontal_overflow

    def screenshot(self, *, path: str, **_: object) -> None:
        Path(path).write_bytes(b"current-demo-image")

    def scroll_into_view_if_needed(self) -> None:
        self.page.scrolled.append(self.selector)


class _FakePage:
    def __init__(
        self,
        *,
        body_text: str = "Synthetic Demo Mock Model Not Investment Advice",
        horizontal_overflow: float = 0,
        page_error: str = "",
    ) -> None:
        self.body_text = body_text
        self.horizontal_overflow = horizontal_overflow
        self.page_error = page_error
        self.clicked: list[str] = []
        self.scrolled: list[str] = []
        self.viewports: list[dict[str, int]] = []

    def on(self, event: str, callback: Any) -> None:
        if event == "pageerror" and self.page_error:
            callback(self.page_error)

    def goto(self, *_: object, **__: object) -> None:
        return None

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def wait_for_timeout(self, _: int) -> None:
        return None

    def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewports.append(viewport)

    def screenshot(self, *, path: str, **_: object) -> None:
        Path(path).write_bytes(b"current-demo-frame")


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self, **_: object) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.launch_options: dict[str, object] = {}

    def launch(self, **options: object) -> _FakeBrowser:
        self.launch_options = options
        return self.browser


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self.chromium = chromium

    def __enter__(self) -> "_FakePlaywright":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    page: _FakePage,
) -> tuple[_FakeBrowser, _FakeChromium]:
    browser = _FakeBrowser(page)
    chromium = _FakeChromium(browser)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _FakePlaywright(chromium)  # type: ignore[attr-defined]
    package = ModuleType("playwright")
    package.sync_api = sync_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return browser, chromium


def _capture_args(tmp_path: Path) -> Namespace:
    chrome = tmp_path / "chrome"
    chrome.write_text("fixture", encoding="utf-8")
    return Namespace(
        base_url="http://127.0.0.1:8000",
        output_dir=tmp_path / "captures",
        chrome_path=chrome,
        headed=False,
        dry_run=False,
    )


def _mock_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_demo.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    def render(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_bytes(b"current-demo-render")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(capture_demo.subprocess, "run", render)


def test_capture_demo_dry_run_is_side_effect_free(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.capture_demo",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "captures"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.removeprefix("CAPTURE_DEMO="))
    assert payload["source"] == "live deterministic demo page"
    assert payload["screenshots"] == [
        "overview.png",
        "risk-and-evidence.png",
        "trace-and-review.png",
        "published-report.png",
    ]
    assert payload["gif"] == "end-to-end-demo.gif"
    assert payload["viewports"] == ["1366x768", "1440x900"]
    assert not (tmp_path / "captures").exists()


def test_capture_demo_rejects_non_local_urls() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.capture_demo",
            "--dry-run",
            "--base-url",
            "https://example.com",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert "restricted to a local deterministic demo URL" in result.stdout


def test_capture_script_checks_required_disclosures_and_current_selectors() -> None:
    source = (ROOT / "scripts" / "capture_demo.py").read_text(encoding="utf-8")
    assert "Synthetic Demo" in source
    assert "Mock Model" in source
    assert "Not Investment Advice" in source
    assert ".showcase-risk-band" in source
    assert ".showcase-evidence-band" in source
    assert ".showcase-trace-review" in source
    assert ".showcase-report-band" in source


def test_capture_pipeline_uses_current_page_and_writes_all_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _FakePage()
    browser, chromium = _install_fake_playwright(monkeypatch, page)
    _mock_ffmpeg(monkeypatch)

    result = capture_demo._capture(_capture_args(tmp_path))

    assert result["status"] == "captured"
    assert result["files"] == [*capture_demo.CAPTURES, "end-to-end-demo.gif"]
    assert all(
        (tmp_path / "captures" / name).stat().st_size > 0
        for name in result["files"]
    )
    assert '[data-tab="research"]' in page.clicked
    assert page.viewports[:2] == [
        {"width": 1366, "height": 768},
        {"width": 1440, "height": 900},
    ]
    assert set(capture_demo.GIF_SECTIONS).issubset(page.scrolled)
    assert chromium.launch_options["headless"] is True
    assert browser.closed is True


@pytest.mark.parametrize(
    ("page", "message"),
    [
        (_FakePage(body_text="Synthetic Demo Mock Model"), "Required disclosure"),
        (
            _FakePage(body_text="Synthetic Demo Mock Model Not Investment Advice /Users/x"),
            "Sensitive marker",
        ),
        (_FakePage(page_error="render failed"), "Browser page error"),
        (_FakePage(horizontal_overflow=2), "overflows horizontally"),
    ],
)
def test_capture_pipeline_fails_closed_on_invalid_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: _FakePage,
    message: str,
) -> None:
    _install_fake_playwright(monkeypatch, page)
    _mock_ffmpeg(monkeypatch)

    with pytest.raises(RuntimeError, match=message):
        capture_demo._capture(_capture_args(tmp_path))


def test_capture_pipeline_requires_local_browser_and_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_playwright(monkeypatch, _FakePage())
    args = _capture_args(tmp_path)
    args.chrome_path = tmp_path / "missing-chrome"
    with pytest.raises(RuntimeError, match="Chrome executable not found"):
        capture_demo._capture(args)

    args = _capture_args(tmp_path)
    monkeypatch.setattr(capture_demo.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        capture_demo._capture(args)


def test_capture_main_reports_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert capture_demo.main(["--dry-run"]) == 0
    assert '"source": "live deterministic demo page"' in capsys.readouterr().out

    def fail(_: Namespace) -> dict[str, object]:
        raise RuntimeError("capture unavailable")

    monkeypatch.setattr(capture_demo, "_capture", fail)
    assert capture_demo.main([]) == 1
    assert "CAPTURE_FAILED=RuntimeError: capture unavailable" in capsys.readouterr().out
