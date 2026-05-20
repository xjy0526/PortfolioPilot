"""
CSV Portfolio Reader — Alternative data source to Parqet.

Reads portfolio positions from a CSV file or uploaded JSON data.
Expected CSV columns: ticker, shares, buy_price, buy_date (optional), currency (optional), sector (optional), name (optional)
"""

import csv
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger("portfoliopilot.csv_reader")

CSV_FIELDS = [
    "ticker",
    "shares",
    "buy_price",
    "current_price",
    "buy_date",
    "currency",
    "sector",
    "name",
    "asset_type",
    "market",
    "exchange",
    "country",
]


def resolve_csv_path(file_path: str | Path | None = None) -> Path:
    """Resolve the configured portfolio CSV path relative to the project root."""
    if file_path is None:
        from config import BASE_DIR, settings

        file_path = settings.PARQET_PORTFOLIO_CSV
        base_dir = BASE_DIR
    else:
        from config import BASE_DIR

        base_dir = BASE_DIR

    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def saved_csv_portfolio_exists(file_path: str | Path | None = None) -> bool:
    """Return True when a persisted CSV portfolio is available."""
    return resolve_csv_path(file_path).is_file()


def save_csv_positions(positions: list[dict], file_path: str | Path | None = None) -> Path:
    """Persist normalized CSV positions so they survive app restarts."""
    path = resolve_csv_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for pos in positions:
            writer.writerow({
                "ticker": pos.get("ticker", ""),
                "shares": pos.get("shares", ""),
                "buy_price": pos.get("buy_price", ""),
                "current_price": pos.get("current_price") if pos.get("current_price") is not None else "",
                "buy_date": pos.get("buy_date") or "",
                "currency": pos.get("currency", ""),
                "sector": pos.get("sector") or "",
                "name": pos.get("name", ""),
                "asset_type": pos.get("asset_type", ""),
                "market": pos.get("market", ""),
                "exchange": pos.get("exchange", ""),
                "country": pos.get("country", ""),
            })

    logger.info("Persisted %s CSV positions to %s", len(positions), path)
    return path


def _normalize_asset_type(raw_value: str, ticker: str, market: str) -> str:
    value = (raw_value or "").strip().lower()
    market_value = (market or "").strip().lower()
    ticker_upper = (ticker or "").upper()

    aliases = {
        "stock": "equity",
        "equity": "equity",
        "etf": "etf",
        "fund": "etf",
        "cn_equity": "cn_equity",
        "china_a": "cn_equity",
        "a_share": "cn_equity",
        "ashare": "cn_equity",
        "polymarket": "prediction_market",
        "prediction_market": "prediction_market",
        "prediction": "prediction_market",
    }

    if value in aliases:
        return aliases[value]
    if ticker_upper.endswith((".SS", ".SZ")) or market_value in {"cn", "cn-a", "china", "china-a"}:
        return "cn_equity"
    if market_value == "polymarket":
        return "prediction_market"
    return "equity"


def _normalize_market(asset_type: str, market: str, ticker: str) -> str:
    market_value = (market or "").strip()
    if market_value:
        return market_value
    ticker_upper = (ticker or "").upper()
    if asset_type == "prediction_market":
        return "Polymarket"
    if asset_type == "cn_equity" or ticker_upper.endswith((".SS", ".SZ")):
        return "CN-A"
    return "Global"


def _normalize_country(asset_type: str, country: str) -> str:
    if country and str(country).strip():
        return str(country).strip().upper()
    if asset_type == "cn_equity":
        return "CN"
    if asset_type == "prediction_market":
        return "WEB3"
    return ""


def parse_csv_file(file_path: str) -> list[dict]:
    """Parse a CSV file into a list of position dicts."""
    if not os.path.exists(file_path):
        logger.error(f"CSV file not found: {file_path}")
        return []

    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        return _normalize_rows(list(reader))


def parse_csv_json(positions: list[dict]) -> list[dict]:
    """Parse uploaded JSON positions (from frontend CSV upload)."""
    return _normalize_rows(positions)


def load_saved_csv_positions(file_path: str | Path | None = None) -> list[dict]:
    """Load normalized positions from the persisted portfolio CSV."""
    return parse_csv_file(str(resolve_csv_path(file_path)))


def upsert_csv_position(
    position: dict,
    file_path: str | Path | None = None,
    original_ticker: str | None = None,
) -> tuple[list[dict], dict, bool]:
    """Create or update one position in the persisted CSV portfolio."""
    normalized = parse_csv_json([position])
    if not normalized:
        raise ValueError("Invalid position")

    new_position = normalized[0]
    path = resolve_csv_path(file_path)
    positions = parse_csv_file(str(path)) if path.exists() else []
    original_key = (original_ticker or new_position["ticker"]).upper()
    new_key = new_position["ticker"].upper()
    replaced = False
    updated_positions = []

    for existing in positions:
        key = existing.get("ticker", "").upper()
        if key in {original_key, new_key}:
            replaced = True
            continue
        updated_positions.append(existing)

    updated_positions.append(new_position)
    save_csv_positions(updated_positions, path)
    return updated_positions, new_position, replaced


def delete_csv_position(
    ticker: str,
    file_path: str | Path | None = None,
) -> tuple[list[dict], bool]:
    """Delete one position from the persisted CSV portfolio."""
    path = resolve_csv_path(file_path)
    positions = parse_csv_file(str(path)) if path.exists() else []
    ticker_key = (ticker or "").upper()
    updated_positions = [
        pos for pos in positions
        if pos.get("ticker", "").upper() != ticker_key
    ]
    deleted = len(updated_positions) != len(positions)
    if deleted:
        save_csv_positions(updated_positions, path)
    return updated_positions, deleted


def _normalize_rows(rows: list[dict]) -> list[dict]:
    """Normalize CSV rows into standard portfolio position format."""
    positions = []
    for row in rows:
        # Normalize keys to lowercase
        row = {k.lower().strip(): v for k, v in row.items()}

        ticker = row.get('ticker', '').strip().upper()
        if not ticker:
            continue

        # Skip cash rows
        if ticker in ('CASH', 'cash', ''):
            continue

        try:
            shares = float(row.get('shares', 0))
            buy_price = float(row.get('buy_price', 0))
        except (ValueError, TypeError):
            logger.warning(f"Skipping invalid row for ticker {ticker}: shares/buy_price not numeric")
            continue

        if shares <= 0:
            continue

        market = row.get('market', '').strip()
        asset_type = _normalize_asset_type(row.get('asset_type', ''), ticker, market)

        currency = row.get('currency', 'USD').strip().upper()
        if asset_type == "cn_equity" and not row.get('currency'):
            currency = 'CNY'
        elif asset_type == "prediction_market" and not row.get('currency'):
            currency = 'USD'

        if currency not in ('USD', 'EUR', 'GBP', 'CHF', 'CAD', 'JPY', 'CNY'):
            currency = 'USD'

        buy_date = _parse_date(row.get('buy_date', ''))
        sector = row.get('sector', '').strip() or None
        name = row.get('name', '').strip() or ticker
        current_price = row.get('current_price', '')
        try:
            current_price = float(current_price) if str(current_price).strip() else None
        except (TypeError, ValueError):
            current_price = None

        positions.append({
            'ticker': ticker,
            'name': name,
            'shares': shares,
            'buy_price': buy_price,
            'current_price': current_price,
            'buy_date': buy_date,
            'currency': currency,
            'sector': sector,
            'asset_type': asset_type,
            'market': _normalize_market(asset_type, market, ticker),
            'exchange': row.get('exchange', '').strip(),
            'country': _normalize_country(asset_type, row.get('country', '')),
            'source': 'csv',
        })

    logger.info(f"Parsed {len(positions)} positions from CSV")
    return positions


def _parse_date(date_str: str) -> Optional[str]:
    """Try to parse a date string into ISO format."""
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue

    return None


def csv_positions_to_portfolio_format(positions: list[dict], prices: dict = None) -> list[dict]:
    """
    Convert CSV positions to the internal portfolio format expected by the scoring engine.

    This produces the same structure as Parqet positions so the rest of
    the pipeline (scoring, rebalancing, analytics) works unchanged.
    """
    portfolio = []
    for pos in positions:
        ticker = pos['ticker']
        current_price = pos.get('current_price')
        if current_price is None:
            current_price = prices.get(ticker, pos['buy_price']) if prices else pos['buy_price']
        value = current_price * pos['shares']
        cost_basis = pos['buy_price'] * pos['shares']
        pnl = value - cost_basis
        pnl_pct = ((current_price / pos['buy_price']) - 1) * 100 if pos['buy_price'] > 0 else 0

        portfolio.append({
            'ticker': ticker,
            'name': pos.get('name', ticker),
            'shares': pos['shares'],
            'currentPrice': current_price,
            'buyPrice': pos['buy_price'],
            'totalValue': value,
            'pnl': pnl,
            'pnlPercent': pnl_pct,
            'currency': pos.get('currency', 'USD'),
            'sector': pos.get('sector'),
            'asset_type': pos.get('asset_type', 'equity'),
            'market': pos.get('market', 'Global'),
            'exchange': pos.get('exchange', ''),
            'country': pos.get('country', ''),
            'buy_date': pos.get('buy_date'),
            'source': 'csv',
        })

    return portfolio
