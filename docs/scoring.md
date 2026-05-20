# PortfolioPilot – Scoring Engine v5

## Übersicht

10-Faktor Multi-Analyse-System mit sektorbasierter Bewertung.
Jeder Faktor wird auf 0-100 normalisiert, gewichtet zusammengeführt.

## Faktoren & Gewichte

| # | Faktor | Gewicht | Datenquelle | Beschreibung |
|---|--------|---------|-------------|--------------|
| 1 | **Quality** | 19% | FMP/yFinance | ROE, Gross Margin, Operating Margin, D/E, Current Ratio |
| 2 | **Analyst** | 15% | FMP/yFinance | Konsens (60%) + Preisziel (40%), merged |
| 3 | **Valuation** | 14% | FMP | P/E, EV/EBITDA, PEG, FCF Yield — **sektorbasiert** |
| 4 | **Technical** | 13% | yFinance | RSI-14, SMA Cross, Momentum 30d, Price vs SMA50 |
| 5 | **Growth** | 11% | FMP + yFinance | Revenue Growth YoY, Earnings Growth YoY, ROIC |
| 6 | **Quantitative** | 10% | yFinance | Altman Z-Score (selbst berechnet), Piotroski F-Score (selbst berechnet) |
| 7 | **Sentiment** | 7% | CNN | Fear & Greed Index (Markt-Level) |
| 8 | **Momentum** | 6% | yFinance | 90d + 180d Kurs-Momentum |
| 9 | **Insider** | 3% | yFinance | Insider Buy/Sell Ratio |
| 10 | **ESG** | 2% | yFinance | ESG Risk Score |

**Summe Gewichte: 100%**

## Dynamische Gewichtung

Faktoren ohne verfügbare Daten werden ausgeblendet. Die übrigen werden
proportional hochskaliert, damit die Summe immer 100% beträgt.

## Sektorbasierte Valuation

Nicht jeder Sektor wird gleich bewertet. Ein P/E von 30 ist für Tech-Aktien normal,
für Financials wäre es teuer:

| Sektor | P/E Fair | P/E Günstig | P/E Teuer |
|--------|----------|-------------|-----------|
| Technology | 30 | 20 | 45 |
| Financials | 14 | 10 | 22 |
| Healthcare | 22 | 14 | 35 |
| Energy | 12 | 8 | 20 |
| Consumer Defensive | 20 | 14 | 30 |
| Default | 20 | 14 | 35 |

## Schwellenwerte

| Rating | Score |
|--------|-------|
| 🟢 **BUY** | ≥ 68 |
| 🟡 **HOLD** | 40 – 67 |
| 🔴 **SELL** | < 40 |

## Confidence

Basiert auf der Anzahl verfügbarer Faktoren:
- 10/10 Faktoren → Confidence 1.0
- 5/10 Faktoren → Confidence 0.5
- 0 Faktoren → Confidence 0.0, automatisch HOLD mit Score 50

## v5 Änderungen (gegenüber v4)

- 9 → **10 Faktoren** (Momentum als separater Faktor)
- Gewichtung angepasst (Quality 19%, Technical 13%, Growth 11%, etc.)
- **Revenue Growth** und **Earnings Growth** jetzt echte YoY-Wachstumsraten
  (FMP `income-statement-growth` statt `revenuePerShareTTM`)
- **PEG Ratio** direkt von FMP (`pegRatioTTM`) statt manueller Berechnung
- `_normalize_pct` Schwellwert verschärft (< 1.0 statt < 5.0)
- D/E Normalisierung: Schwellwert > 50 (statt > 10) für Finanzsektor-Kompatibilität
- Legacy-Models entfernt: `StocknearData`, `AlphaVantageData`

## v5.1 Änderungen (yfinance v1.2.0 Kompatibilität)

- **Recommendations Fix:** yfinance v1.2.0 liefert aggregierte Spalten
  `[strongBuy, buy, hold, sell, strongSell]` statt einzelner `toGrade`-Ratings.
  Fetcher erkennt jetzt beide Formate (v1.2.0 + Legacy).
- **Insider Transactions Fix:** `Transaction`-Spalte ist in v1.2.0 leer,
  Daten stehen in `Text`-Spalte. Erkennt auch "acquisition"/"disposition".
- **ESG Fallback:** `ticker.sustainability` von Yahoo eingestellt →
  Fallback über `ticker.info` (`esgScore`, `totalEsg`, `overallRisk`).
- **Analyst Counts Enrichment:** yFinance Fundamentals-Fallback liefert
  jetzt auch Buy/Hold/Sell-Aufschlüsselung aus `ticker.recommendations`
  (v1.2.0 Format) für Nicht-FMP-Ticker.
- **Datenabdeckung:** 17/19 Positionen mit vollständigen Daten
  (nur ISINs ohne yfinance-Mapping fehlen).

## v5.2 Änderungen (Quantitative Scores + Bugfixes)

- **Altman Z-Score selbst berechnet:** Nutzt `balance_sheet` und `income_stmt`
  von yfinance. Formel: `1.2×(WC/TA) + 1.4×(RE/TA) + 3.3×(EBIT/TA) + 0.6×(MC/TL) + 1.0×(Rev/TA)`.
  Vorher: FMP Free Tier lieferte für 0/19 Ticker Daten.
- **Piotroski F-Score selbst berechnet:** 9 binäre Kriterien aus 2-Jahres-Vergleich
  (Profitabilität, Verschuldung, Effizienz). Nutzt `balance_sheet`, `income_stmt`, `cashflow`.
- **Dividend Yield Fix:** yfinance `dividendYield` ist bereits Prozent (z.B. 0.65 = 0.65%).
  Alte Logik multiplizierte Werte < 1 mit 100 → ASML zeigte 65% statt 0.65%.
- **Earnings-Kalender:** `lxml` als Dependency hinzugefügt — `ticker.earnings_dates`
  benötigt `pd.read_html(lxml)`. Vorher: silent fail → 0 Earnings-Termine.
- **Performance:** `_yfinance_price_fallback()` blockierte den Event-Loop (synchroner
  `ticker.info` Aufruf). Jetzt via `asyncio.to_thread()` — Server bleibt responsiv.
