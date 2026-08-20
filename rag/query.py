"""Query normalization and conservative metadata-intent extraction."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TypedDict


_TICKER_STOPWORDS = {
    "AI", "API", "BM25", "CSV", "ETF", "FAQ", "FUND", "LLM", "MD", "PDF", "RAG", "RISK", "RRF", "SQL", "TXT",
}
_SOURCE_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "announcement": ("announcement", "notice", "公告", "通知"),
    "research_report": ("research report", "research note", "研报", "研究报告", "研究摘要"),
    "policy": ("policy", "procedure", "制度", "政策", "流程"),
    "faq": ("faq", "常见问题", "问答"),
}


class QueryMetadataFilters(TypedDict):
    tickers: list[str]
    fund_codes: list[str]
    source_types: list[str]
    publish_date_from: date | None
    publish_date_to: date | None


@dataclass(frozen=True)
class QueryIntent:
    normalized_query: str
    tickers: list[str] = field(default_factory=list)
    fund_codes: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    publish_date_from: date | None = None
    publish_date_to: date | None = None

    @property
    def retrieval_query(self) -> str:
        """Return semantic search text with metadata control prefixes removed."""
        value = re.sub(
            r"(?i)(?:ticker|股票代码|证券代码)\s*[:：=]\s*([A-Z0-9.-]{1,16})",
            r"\1",
            self.normalized_query,
        )
        value = re.sub(
            r"(?i)(?:fund(?:\s+code)?|基金(?:代码)?)\s*[:：=]?\s*([A-Z0-9.-]{3,20})",
            r"\1",
            value,
        )
        if self.publish_date_from or self.publish_date_to:
            value = re.sub(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", " ", value)
            value = re.sub(r"\b20\d{2}\b", " ", value)
            value = re.sub(
                r"(?i)(?:最近|近|last|past)\s*\d{1,4}\s*(?:天|days?)",
                " ",
                value,
            )
            value = re.sub(r"(?:至|到|截至|之前|以前|之后|以来)|(?i:\b(?:to|until|before|after|since|from)\b)", " ", value)
        if self.source_types:
            for source_type in self.source_types:
                for term in _SOURCE_TYPE_TERMS.get(source_type, ()):
                    value = re.sub(re.escape(term), " ", value, flags=re.IGNORECASE)
        cleaned = normalize_query(value)
        return cleaned or self.normalized_query

    def metadata_filters(self) -> QueryMetadataFilters:
        return {
            "tickers": self.tickers,
            "fund_codes": self.fund_codes,
            "source_types": self.source_types,
            "publish_date_from": self.publish_date_from,
            "publish_date_to": self.publish_date_to,
        }


def normalize_query(query: str) -> str:
    value = unicodedata.normalize("NFKC", str(query or ""))
    value = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_query_intent(query: str, *, as_of: date | None = None) -> QueryIntent:
    normalized = normalize_query(query)
    reference_date = as_of or date.today()
    tickers = _extract_tickers(normalized)
    fund_codes = _extract_fund_codes(normalized)
    source_types = _extract_source_types(normalized)
    start_date, end_date = _extract_date_range(normalized, reference_date)
    return QueryIntent(
        normalized_query=normalized,
        tickers=tickers,
        fund_codes=fund_codes,
        source_types=source_types,
        publish_date_from=start_date,
        publish_date_to=end_date,
    )


def _extract_tickers(query: str) -> list[str]:
    values: set[str] = set()
    for match in re.finditer(r"(?i)(?:ticker|股票代码|证券代码)\s*[:：=]\s*([A-Z0-9.-]{1,16})", query):
        values.add(match.group(1).upper())
    for match in re.finditer(r"(?<!\w)\$([A-Za-z][A-Za-z0-9.-]{0,15})\b", query):
        values.add(match.group(1).upper())
    for match in re.finditer(r"\b\d{6}\.(?:SS|SZ)\b", query, flags=re.IGNORECASE):
        values.add(match.group(0).upper())
    # Natural uppercase symbols are useful but kept conservative to avoid
    # interpreting common RAG/finance acronyms as metadata constraints.
    for match in re.finditer(r"(?<![\w$])([A-Z][A-Z0-9.-]{1,4})(?!\w)", query):
        value = match.group(1).upper()
        if value not in _TICKER_STOPWORDS and not value.isdigit():
            values.add(value)
    return sorted(values)


def _extract_fund_codes(query: str) -> list[str]:
    values = {
        match.group(1).upper()
        for match in re.finditer(
            r"(?i)(?:fund(?:\s+code)?|基金(?:代码)?)\s*[:：=]?\s*([A-Z0-9.-]{3,20})",
            query,
        )
    }
    return sorted(values)


def _extract_source_types(query: str) -> list[str]:
    lowered = query.lower()
    return sorted(
        source_type
        for source_type, terms in _SOURCE_TYPE_TERMS.items()
        if any(term in lowered for term in terms)
    )


def _extract_date_range(query: str, reference_date: date) -> tuple[date | None, date | None]:
    recent = re.search(r"(?:最近|近|last|past)\s*(\d{1,4})\s*(?:天|days?)", query, flags=re.IGNORECASE)
    if recent:
        days = max(1, int(recent.group(1)))
        return reference_date - timedelta(days=days), reference_date

    iso_dates = []
    for raw in re.findall(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", query):
        try:
            iso_dates.append(date.fromisoformat(raw))
        except ValueError:
            continue
    if len(iso_dates) >= 2:
        return min(iso_dates), max(iso_dates)
    if len(iso_dates) == 1:
        if re.search(r"(?:before|until|截至|之前|以前)", query, flags=re.IGNORECASE):
            return None, iso_dates[0]
        if re.search(r"(?:after|since|from|自|之后|以来)", query, flags=re.IGNORECASE):
            return iso_dates[0], None
        return iso_dates[0], iso_dates[0]

    years = sorted({int(value) for value in re.findall(r"\b(20\d{2})\b", query)})
    if len(years) >= 2:
        return date(years[0], 1, 1), date(years[-1], 12, 31)
    if len(years) == 1:
        return date(years[0], 1, 1), date(years[0], 12, 31)
    return None, None
