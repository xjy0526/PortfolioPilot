"""Capture the running deterministic Showcase UI with a real browser."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "assets" / "current-demo"
DEFAULT_CHROME = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
CAPTURES = (
    "overview.png",
    "risk-and-evidence.png",
    "trace-and-review.png",
    "published-report.png",
)
GIF_SECTIONS = (
    ".core-snapshot-band",
    ".core-risk-grid",
    ".showcase-evidence-band",
    ".showcase-trace-review",
    ".showcase-validation-band",
    ".showcase-review-band",
    ".showcase-report-band",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chrome-path", type=Path, default=DEFAULT_CHROME)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_local_demo_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Capture is restricted to a local deterministic demo URL")
    return value.rstrip("/")


def _plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "base_url": _validate_local_demo_url(args.base_url),
        "output_dir": str(args.output_dir.resolve()),
        "screenshots": list(CAPTURES),
        "gif": "end-to-end-demo.gif",
        "viewports": ["1366x768", "1440x900"],
        "requires": ["running make demo", "Playwright", "Chrome", "ffmpeg"],
        "source": "live deterministic demo page",
    }


def _capture(args: argparse.Namespace) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional tooling
        raise RuntimeError(
            "Playwright is not installed; use requirements-demo-capture.txt"
        ) from exc

    base_url = _validate_local_demo_url(args.base_url)
    chrome_path = args.chrome_path.resolve()
    if not chrome_path.is_file():
        raise RuntimeError(f"Chrome executable not found: {chrome_path}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to build end-to-end-demo.gif")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed,
            executable_path=str(chrome_path),
        )
        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            color_scheme="dark",
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle", timeout=30_000)
        page.locator('[data-tab="research"]').first.click()
        page.locator(".showcase-report-band").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(600)

        body_text = page.locator("#tab-research").inner_text()
        for disclosure in ("Synthetic Demo", "Mock Model", "Not Investment Advice"):
            if disclosure not in body_text:
                raise RuntimeError(f"Required disclosure is missing from the page: {disclosure}")
        for sensitive_marker in ("/Users/", "QWEN_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "ghp_"):
            if sensitive_marker in body_text:
                raise RuntimeError(f"Sensitive marker is visible in the page: {sensitive_marker}")
        if errors:
            raise RuntimeError(f"Browser page error: {errors[0]}")

        for width, height in ((1366, 768), (1440, 900)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            horizontal_overflow = page.locator("#tab-research").evaluate(
                "element => element.scrollWidth - element.clientWidth"
            )
            if float(horizontal_overflow) > 1:
                raise RuntimeError(
                    f"Showcase overflows horizontally at {width}x{height}: "
                    f"{horizontal_overflow}px"
                )
        page.set_viewport_size({"width": 1440, "height": 900})

        page.locator("#tab-research").screenshot(
            path=str(output_dir / "overview.png"),
            animations="disabled",
        )
        _screenshot_range(
            page,
            ".showcase-risk-band",
            ".showcase-evidence-band",
            output_dir / "risk-and-evidence.png",
        )
        _screenshot_range(
            page,
            ".showcase-trace-review",
            ".showcase-review-band",
            output_dir / "trace-and-review.png",
        )
        page.locator(".showcase-report-band").screenshot(
            path=str(output_dir / "published-report.png"),
            animations="disabled",
        )

        with tempfile.TemporaryDirectory(prefix="portfoliopilot-showcase-") as temporary:
            frame_dir = Path(temporary)
            frame_index = 0
            for selector in GIF_SECTIONS:
                locator = page.locator(selector).first
                locator.scroll_into_view_if_needed()
                page.wait_for_timeout(350)
                for _ in range(2):
                    page.screenshot(path=str(frame_dir / f"frame-{frame_index:03d}.png"))
                    frame_index += 1
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-framerate",
                    "1",
                    "-i",
                    str(frame_dir / "frame-%03d.png"),
                    "-vf",
                    "fps=8,scale=1440:-1:flags=lanczos,split[s0][s1];"
                    "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
                    str(output_dir / "end-to-end-demo.gif"),
                ],
                check=True,
                timeout=120,
            )
        browser.close()

    produced = [*CAPTURES, "end-to-end-demo.gif"]
    empty = [name for name in produced if (output_dir / name).stat().st_size == 0]
    if empty:
        raise RuntimeError(f"Capture produced empty files: {', '.join(empty)}")
    return {
        "status": "captured",
        "base_url": base_url,
        "output_dir": str(output_dir),
        "files": produced,
    }


def _screenshot_range(page: Any, first: str, last: str, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="portfoliopilot-capture-pair-") as temporary:
        temporary_path = Path(temporary)
        first_image = temporary_path / "first.png"
        last_image = temporary_path / "last.png"
        page.locator(first).first.screenshot(
            path=str(first_image),
            animations="disabled",
        )
        page.locator(last).first.screenshot(
            path=str(last_image),
            animations="disabled",
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(first_image),
                "-i",
                str(last_image),
                "-filter_complex",
                "vstack=inputs=2",
                str(output),
            ],
            check=True,
            timeout=60,
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _plan(args) if args.dry_run else _capture(args)
    except Exception as exc:
        print(f"CAPTURE_FAILED={type(exc).__name__}: {exc}")
        return 1
    print("CAPTURE_DEMO=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
