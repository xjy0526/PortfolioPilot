# 📡 PortfolioPilot API Reference

Alle Endpoints erfordern Basic Auth (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`), sofern nicht anders angegeben.

## Portfolio (`routes/portfolio.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/` | Dashboard (HTML) |
| GET | `/api/portfolio` | Portfolio-Daten (JSON) |
| GET | `/api/stock/{ticker}` | Einzelaktie Details |
| GET | `/api/stock/{ticker}/history` | Kurs-History einer Einzelaktie |
| GET | `/api/portfolio/history` | Portfolio-Wert-Entwicklung |
| GET | `/api/portfolio/activities` | Kauf-/Verkaufs-Aktivitäten |
| GET | `/api/rebalancing` | Rebalancing-Empfehlungen |
| GET | `/api/tech-picks` | Tech-Aktien Screening (yFinance Screener) |
| GET | `/api/sectors` | Sektor-Allokation |
| GET | `/api/fear-greed` | Fear & Greed Index |
| GET | `/api/status` | System-Status |
| POST | `/api/portfolio/csv` | CSV Portfolio Import (Upload) |

## Demo Mode (`routes/demo.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/demo/activate` | Demo-Portfolio laden (12 fiktive Positionen) |
| POST | `/api/demo/deactivate` | Demo deaktivieren, echter Refresh |
| GET | `/api/demo/status` | Demo-Modus aktiv? |

## Refresh (`routes/refresh.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/refresh` | Kompletter Refresh |
| POST | `/api/refresh/prices` | Nur Kurse updaten |
| POST | `/api/refresh/portfolio` | Nur Portfolio-Positionen updaten |
| POST | `/api/refresh/parqet` | Nur Parqet-Positionen |
| POST | `/api/refresh/scores` | Nur Scores neuberechnen |
| POST | `/api/trigger-report` | AI-Report manuell auslösen |
| POST | `/api/trigger-weekly-digest` | Weekly Digest manuell auslösen |
| GET | `/api/refresh/status` | Refresh-Fortschritt |

## AI Advisor & Analysis (`routes/analysis.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/analysis/run` | Analyse starten |
| GET | `/api/analysis/latest` | Letzte Analyse abrufen |
| GET | `/api/analysis/history` | Analyse-Historie |
| GET | `/api/analysis/trend/{ticker}` | Score-Trend einer Aktie |
| GET | `/api/backtest` | Score-Backtest |
| GET | `/api/sectors/rotation` | Sektor-Rotation-Analyse |
| POST | `/api/advisor/evaluate` | Trade-Bewertung (Kauf/Verkauf/Aufstocken) |
| POST | `/api/advisor/chat` | Freie Portfolio-Diskussion (Multi-Turn) |

## Analytics (`routes/analytics.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/market-indices` | S&P 500, Nasdaq, DAX |
| GET | `/api/movers` | Top Gewinner/Verlierer |
| GET | `/api/heatmap` | Portfolio-Treemap |
| GET | `/api/dividends` | Dividenden-Übersicht |
| GET | `/api/benchmark` | Benchmark-Vergleich |
| GET | `/api/correlation` | Korrelationsmatrix |
| GET | `/api/earnings-calendar` | Earnings-Kalender (Portfolio-Positionen) |
| GET | `/api/stock/{ticker}/news` | Aktien-News |
| GET | `/api/risk` | Beta, VaR, Max Drawdown |
| GET | `/api/stock/{ticker}/score-history` | Score-Entwicklung einer Aktie |
| GET | `/api/attribution` | P&L Attribution |
| GET | `/api/portfolio/history-detail` | Detaillierte Portfolio-Historie (Einzelaktien) |
| GET | `/api/performance` | Performance-Kennzahlen |

## Shadow Portfolio Agent (`routes/shadow_portfolio.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/shadow-portfolio` | Aktueller Shadow-Portfolio-Stand |
| POST | `/api/shadow-portfolio/run` | Agent-Zyklus manuell auslösen (30-90s) |
| GET | `/api/shadow-portfolio/transactions` | Transaktionshistorie (limit: 50) |
| GET | `/api/shadow-portfolio/performance` | Performance-Verlauf (days: 90) |
| GET | `/api/shadow-portfolio/decision-log` | AI-Entscheidungslog |
| POST | `/api/shadow-portfolio/reset` | Portfolio zurücksetzen (Config bleibt) |
| GET | `/api/shadow-portfolio/config` | Agenten-Konfiguration lesen |
| POST | `/api/shadow-portfolio/config` | Agenten-Konfiguration speichern |

## Streaming (`routes/streaming.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/prices/stream` | SSE-Stream für Echtzeit-Kursänderungen |

## Telegram Webhook (`routes/telegram.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/telegram/webhook/{secret}` | Telegram Bot Webhook (Secret-Token im Pfad) |
