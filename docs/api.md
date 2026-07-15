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

## Knowledge Base (`routes/knowledge.py`)

Uploads use a JSON body with `filename`, either UTF-8 `content` or PDF-safe
`content_base64`, and a `metadata` object. Read endpoints accept trusted
`X-User-Id` and comma-separated `X-Permission-Groups` headers. Normal RAG
retrieval accepts the same permission context in its JSON body and applies the
ACL in SQLite before chunk text is embedded or scored.

`POST /api/rag/retrieve` returns both `evidence` (compatibility alias) and
`citations`. Each citation contains stable document/chunk identifiers, version,
source metadata, quote, permission level and fused score. `evidence_insufficient`
is true when no candidate reaches `score_threshold`. The query body may contain
`permission_groups`, `top_k`, and an optional `score_threshold`; ticker, fund
code, source type and date constraints are extracted from the query itself.

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/knowledge/documents` | Dokument ingestieren oder identischen Checksum als Duplikat erkennen |
| GET | `/api/knowledge/documents` | Für die Permission-Gruppen sichtbare Dokumente auflisten |
| GET | `/api/knowledge/documents/{document_id}` | Metadaten und Versionen lesen |
| POST | `/api/knowledge/documents/{document_id}/publish` | Aktuelle Version veröffentlichen |
| POST | `/api/knowledge/documents/{document_id}/deactivate` | Dokument deaktivieren und aus Retrieval entfernen |
| GET | `/api/knowledge/ingestion-jobs/{job_id}` | Ingestion-Status oder Parserfehler lesen |

## Prompt Registry (`routes/prompts.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/prompts` | Prompt-Definitionen und aktive Versionen auflisten |
| POST | `/api/prompts` | Prompt mit initialer Draft-Version erstellen |
| POST | `/api/prompts/{prompt_id}/versions` | Neue unveränderliche Version erstellen |
| POST | `/api/prompts/{prompt_id}/versions/{version}/publish` | Version veröffentlichen |
| POST | `/api/prompts/{prompt_id}/rollback` | Auf vorherige oder angegebene Version zurückrollen |
| POST | `/api/prompts/compare` | Zwei Versionen mit demselben Testset vergleichen |

Each provider call is written to `llm_call_traces` with the exact `prompt_id`
and `prompt_version`. Financial-analysis output is validated by one Pydantic
contract before it is returned; unknown portfolio tickers, unreturned citation
IDs, extra JSON fields and financial numbers absent from structured input fail
validation, trigger one retry and then use the safe fallback.

## Controlled Research Workflow (`routes/workflows.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/workflows/research-report` | Allowlisted research-report workflow starten |
| GET | `/api/workflows/{run_id}` | Run, Steps und Review-Status lesen |
| POST | `/api/reviews/{review_id}/approve` | Bericht nach menschlicher Prüfung freigeben |
| POST | `/api/reviews/{review_id}/reject` | Bericht ablehnen |
| POST | `/api/reviews/{review_id}/request-changes` | Änderungen mit Feedback anfordern |
| GET | `/api/reports/{report_id}` | Ausschließlich freigegebenen Bericht lesen |

## Evaluation & Trace (`routes/evaluation.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/evaluation/dashboard` | Trends, Prompt-Vergleiche, Badcases, Kosten und Adoption |
| GET | `/api/evaluation/traces` | Vollständige LLM Call Traces lesen |

## Telegram Webhook (`routes/telegram.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/telegram/webhook/{secret}` | Telegram Bot Webhook (Secret-Token im Pfad) |
