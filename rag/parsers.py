"""Structured parsers and chunking for supported knowledge documents."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".txt", ".md", ".csv", ".pdf"}


@dataclass(slots=True)
class ParsedBlock:
    title: str
    section: str
    page_number: int | None
    text: str


def parse_document(
    content: bytes,
    filename: str,
    fallback_title: str,
    *,
    max_pdf_pages: int | None = None,
) -> tuple[str, list[ParsedBlock], str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported document type: {suffix or 'unknown'}")
    if not content:
        raise ValueError("Document content is empty")
    if suffix == ".pdf":
        title, blocks = _parse_pdf(content, fallback_title, max_pages=max_pdf_pages)
        return title, blocks, "pypdf"

    text = content.decode("utf-8-sig", errors="strict")
    if suffix == ".csv":
        title, blocks = _parse_csv(text, fallback_title)
        return title, blocks, "csv"
    title, blocks = _parse_text(text, fallback_title, markdown=suffix == ".md")
    return title, blocks, "markdown" if suffix == ".md" else "text"


def structured_chunks(
    blocks: list[ParsedBlock],
    *,
    chunk_size: int = 900,
    overlap: int = 120,
) -> list[dict]:
    """Keep heading/paragraph/page boundaries; use fixed windows only for oversized blocks."""
    chunks: list[dict] = []
    for block in blocks:
        clean = re.sub(r"[ \t]+", " ", block.text).strip()
        if not clean:
            continue
        pieces = [clean] if len(clean) <= chunk_size else _fixed_length_chunks(clean, chunk_size, overlap)
        for piece in pieces:
            chunks.append({
                "chunk_index": len(chunks),
                "title": block.title,
                "section": block.section,
                "page_number": block.page_number,
                "text": piece,
            })
    return chunks


def _parse_text(text: str, fallback_title: str, *, markdown: bool) -> tuple[str, list[ParsedBlock]]:
    lines = text.replace("\r\n", "\n").split("\n")
    title = fallback_title
    section = ""
    paragraph: list[str] = []
    blocks: list[ParsedBlock] = []

    def flush() -> None:
        if paragraph:
            value = "\n".join(paragraph).strip()
            if value:
                blocks.append(ParsedBlock(title=title, section=section, page_number=None, text=value))
            paragraph.clear()

    for raw_line in lines:
        line = raw_line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+)$", line) if markdown else None
        if heading:
            flush()
            heading_text = heading.group(2).strip()
            if len(heading.group(1)) == 1 and title == fallback_title:
                title = heading_text
            section = heading_text
            continue
        if not markdown and _looks_like_heading(line):
            flush()
            if title == fallback_title and not blocks:
                title = line.rstrip(":")
            section = line.rstrip(":")
            continue
        if not line:
            flush()
        else:
            paragraph.append(line)
    flush()
    return title, blocks


def _parse_csv(text: str, fallback_title: str) -> tuple[str, list[ParsedBlock]]:
    reader = csv.DictReader(io.StringIO(text))
    blocks: list[ParsedBlock] = []
    if reader.fieldnames:
        for row_number, dict_row in enumerate(reader, start=1):
            value = " | ".join(
                f"{key}: {value}"
                for key, value in dict_row.items()
                if value not in (None, "")
            )
            if value:
                blocks.append(ParsedBlock(fallback_title, f"Row {row_number}", None, value))
    else:
        for row_number, list_row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if list_row:
                blocks.append(
                    ParsedBlock(fallback_title, f"Row {row_number}", None, ", ".join(list_row))
                )
    if not blocks:
        raise ValueError("CSV document contains no readable rows")
    return fallback_title, blocks


def _parse_pdf(
    content: bytes,
    fallback_title: str,
    *,
    max_pages: int | None = None,
) -> tuple[str, list[ParsedBlock]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support requires the pypdf package") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"Invalid PDF document: {exc}") from exc

    if max_pages is not None and len(reader.pages) > max_pages:
        raise ValueError(f"PDF page limit exceeded: {len(reader.pages)} > {max_pages}")

    metadata_title = str(getattr(reader.metadata, "title", "") or "").strip()
    title = metadata_title or fallback_title
    blocks: list[ParsedBlock] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        page_title, page_blocks = _parse_text(page_text, title, markdown=False)
        if title == fallback_title and page_title:
            title = page_title
        for block in page_blocks:
            blocks.append(ParsedBlock(title, block.section, page_number, block.text))
    if not blocks:
        raise ValueError("PDF document contains no extractable text")
    return title, blocks


def _looks_like_heading(line: str) -> bool:
    if not line or len(line) > 100:
        return False
    return line.endswith(":") or (len(line.split()) <= 10 and line.isupper())


def _fixed_length_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(text), step):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(text):
            break
    return chunks
