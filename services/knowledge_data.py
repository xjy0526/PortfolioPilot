"""PortfolioPilot - Knowledge Data Module.

Wissens-Datenbank mit Technologie-Fakten und täglichen Lern-Tipps
basierend auf allen Antigravity-Projekten.

Genutzt von:
  - /wissen Telegram-Befehl
  - Täglicher AI-Agent-Report ("Wissen des Tages")
"""
from datetime import date


# ─────────────────────────────────────────────────────────────
# Projekt-Wissen
# ─────────────────────────────────────────────────────────────

PROJECT_KNOWLEDGE: dict[str, dict] = {
    "ai_knowledge": {
        "name": "AI Knowledge Builder",
        "emoji": "🧠",
        "description": "Tägliche Quiz-App zum KI-Wissensaufbau",
        "difficulty": "⭐⭐ Einfach",
        "technologies": [
            "HTML5", "Vanilla CSS", "Vanilla JavaScript",
            "LocalStorage", "Google Fonts (Inter)",
        ],
        "best_practices": [
            "IIFE-Pattern für Scope-Isolation",
            "Separation of Concerns (Daten/Logik/Styling getrennt)",
            "CSS Custom Properties für konsistentes Design",
            "Responsive Design mit @media Queries",
            "Seeded Random für reproduzierbare Quiz-Auswahl",
            "Graceful State Loading (Spread-Operator)",
        ],
        "key_learning": "Frontend-Grundlagen: HTML, CSS, JS ohne Frameworks",
    },
    "pokerpro": {
        "name": "PokerPro Simulator",
        "emoji": "♠️",
        "description": "Poker-Turnier-Simulator mit GTO + Exploitativ + ICM",
        "difficulty": "⭐⭐⭐ Mittel",
        "technologies": [
            "HTML5", "Vanilla CSS", "Vanilla JavaScript",
            "Chart.js (CDN)", "Modulare JS-Architektur (9 Dateien)",
        ],
        "best_practices": [
            "Single Responsibility Principle (eine Datei = eine Aufgabe)",
            "Engine-Pattern (Logik komplett von UI getrennt)",
            "Algorithmen als eigenständige Module (ICM, GTO)",
            "Multi-Page App mit geteiltem Design",
        ],
        "key_learning": "Modulares JavaScript und Algorithmen-Implementierung",
    },
    "portfoliopilot": {
        "name": "PortfolioPilot",
        "emoji": "💰",
        "description": "Full-Stack Aktienportfolio-Dashboard mit AI-Analyse",
        "difficulty": "⭐⭐⭐⭐⭐ Komplex",
        "technologies": [
            "Python", "FastAPI", "Uvicorn", "Pydantic",
            "httpx", "pandas", "APScheduler",
            "Parqet API (OAuth2)", "FMP API", "yfinance",
            "yFinance WebSocket", "Google Gemini API",
            "Telegram Bot API", "Docker", "Google Cloud Run",
            "pytest", "HTML/JS/CSS Frontend",
        ],
        "best_practices": [
            "Layered Architecture (Routes → Services → Engine → Fetchers)",
            "Pydantic Models (20+ typisierte Datenklassen)",
            "Async/Await für parallele API-Calls",
            "Environment Configuration (.env + config.py)",
            "OAuth2 Token-Management (refresh automatisch)",
            "Datei-basiertes Caching mit TTL",
            "Multi-Faktor Scoring Engine (9 gewichtete Faktoren)",
            "Scheduled Jobs (APScheduler, Cron-basiert)",
            "Docker mit Non-Root User (Security)",
            "WebSocket Streaming für Echtzeit-Kurse",
            "Graceful Degradation (Demo-Modus bei fehlenden Keys)",
        ],
        "key_learning": "Full-Stack: APIs, Auth, Caching, AI, Cloud-Deployment",
    },
    "job_automation": {
        "name": "Mission Purpose for Love",
        "emoji": "🚀",
        "description": "Job-Such-Automatisierung mit Scraper und Dashboard",
        "difficulty": "⭐⭐⭐⭐ Fortgeschritten",
        "technologies": [
            "Python", "Flask", "Playwright", "BeautifulSoup",
            "pandas", "Streamlit", "Pydantic",
            "Docker Compose", "PostgreSQL", "Next.js",
            "pytest", "black", "isort", "flake8", "mypy",
        ],
        "best_practices": [
            "Docker Compose für Multi-Container-Orchestrierung",
            "Code Quality Toolchain (black, isort, flake8, mypy)",
            "Relationale Datenbank statt Dateien",
            "Frontend/Backend als eigenständige Services",
        ],
        "key_learning": "DevOps, Code-Qualität und professionelle Projektstruktur",
    },
}


# ─────────────────────────────────────────────────────────────
# Tägliche Tipps
# ─────────────────────────────────────────────────────────────

DAILY_TIPS: list[dict] = [
    # ── Projekt: AI Knowledge ──
    {
        "category": "JavaScript",
        "project": "ai_knowledge",
        "title": "IIFE – Code in einer unsichtbaren Box",
        "text": (
            "🔒 *IIFE-Pattern*\n\n"
            "`(function() { ... })();` erstellt eine Funktion die sich sofort selbst ausführt. "
            "Alle Variablen darin sind von außen unsichtbar – kein anderes Script kann sie überschreiben.\n\n"
            "📂 Genutzt in: AI Knowledge Builder (`app.js`)"
        ),
    },
    {
        "category": "CSS",
        "project": "ai_knowledge",
        "title": "CSS Custom Properties",
        "text": (
            "🎨 *CSS Variables*\n\n"
            "Statt überall `#3b82f6` zu schreiben, definierst du einmal `--accent-blue: #3b82f6;` "
            "und nutzt `var(--accent-blue)`. Farbe ändern? Eine Stelle statt 50.\n\n"
            "💡 Tipp: Damit lassen sich Dark/Light-Modes leicht umsetzen!"
        ),
    },
    {
        "category": "JavaScript",
        "project": "ai_knowledge",
        "title": "Seeded Random – kontrollierter Zufall",
        "text": (
            "🎲 *Seeded Random*\n\n"
            "`Math.random()` gibt jedes Mal andere Zahlen. Aber manchmal willst du "
            "reproduzierbare Ergebnisse – z.B. damit alle Nutzer am selben Tag dieselben Quiz-Fragen bekommen.\n\n"
            "Lösung: Ein eigener Algorithmus der bei gleichem Startwert (Seed) immer die gleiche Reihenfolge liefert."
        ),
    },
    {
        "category": "JavaScript",
        "project": "ai_knowledge",
        "title": "Graceful State Loading",
        "text": (
            "🛡️ *Graceful State Loading*\n\n"
            "`{ ...defaultState(), ...savedState }` – nimm alle Standardwerte und überschreibe nur die gespeicherten.\n\n"
            "Warum? Wenn du ein neues Feature hinzufügst, haben alte Nutzer das Feld noch nicht im Speicher. "
            "Ohne das würde die App crashen!"
        ),
    },
    # ── Projekt: PokerPro ──
    {
        "category": "Architektur",
        "project": "pokerpro",
        "title": "Single Responsibility Principle",
        "text": (
            "🧩 *Single Responsibility*\n\n"
            "Jede Datei sollte genau EINE Aufgabe haben. Im PokerPro Simulator hat jede der 9 JS-Dateien "
            "eine klare Zuständigkeit: `poker-engine.js` = Karten, `icm.js` = ICM-Berechnung, `ui.js` = Darstellung.\n\n"
            "Vorteil: Du kannst die Strategie ändern, ohne den UI-Code anzufassen."
        ),
    },
    {
        "category": "Architektur",
        "project": "pokerpro",
        "title": "Engine-Pattern – Logik ≠ Anzeige",
        "text": (
            "⚙️ *Engine-Pattern*\n\n"
            "Die Spiellogik (`poker-engine.js`) hat keinen einzigen HTML- oder DOM-Zugriff. "
            "Die UI (`ui.js`) liest nur Ergebnisse und zeigt sie an.\n\n"
            "Das Ergebnis: Die Engine könnte ohne Browser laufen – z.B. für Batch-Simulationen in Node.js."
        ),
    },
    # ── Projekt: PortfolioPilot ──
    {
        "category": "Python",
        "project": "portfoliopilot",
        "title": "FastAPI – Modernes Python Web-Framework",
        "text": (
            "⚡ *FastAPI*\n\n"
            "FastAPI ist ein asynchrones Python-Framework das automatisch:\n"
            "• API-Dokumentation generiert (Swagger UI)\n"
            "• Daten validiert (via Pydantic)\n"
            "• Typen-Hints nutzt für bessere IDE-Unterstützung\n\n"
            "Es ist ~3x schneller als Flask dank async/await."
        ),
    },
    {
        "category": "Python",
        "project": "portfoliopilot",
        "title": "Pydantic – Daten die sich selbst prüfen",
        "text": (
            "📋 *Pydantic Models*\n\n"
            "Statt `{'ticker': 'AAPL', 'price': 150}` als loses Dict zu nutzen, definierst du eine Klasse:\n"
            "`class Stock(BaseModel): ticker: str; price: float`\n\n"
            "Liefert die API Text statt Zahl? → Sofortiger Fehler statt mysteriöser Bugs später."
        ),
    },
    {
        "category": "Python",
        "project": "portfoliopilot",
        "title": "Async/Await – Mehrere Dinge gleichzeitig",
        "text": (
            "⚡ *Async/Await*\n\n"
            "Statt nacheinander auf 5 APIs zu warten (5×2s = 10s), startest du alle gleichzeitig (~2s).\n\n"
            "`async def get_data():`\n"
            "`    results = await asyncio.gather(api1(), api2(), api3())`\n\n"
            "📂 PortfolioPilot holt Parqet, FMP und yfinance parallel."
        ),
    },
    {
        "category": "Sicherheit",
        "project": "portfoliopilot",
        "title": "Secrets gehören in .env, nie in den Code!",
        "text": (
            "🔐 *Environment Variables*\n\n"
            "API-Keys NIEMALS in den Code schreiben! Bots scannen GitHub und missbrauchen Keys innerhalb von Minuten.\n\n"
            "Lösung: `.env`-Datei + `.gitignore`. PortfolioPilot nutzt `python-dotenv` + `config.py` als zentrale Settings-Klasse."
        ),
    },
    {
        "category": "Architektur",
        "project": "portfoliopilot",
        "title": "Layered Architecture – Schichten statt Spaghetti",
        "text": (
            "🏗️ *Layered Architecture*\n\n"
            "Routes → Services → Engine → Fetchers\n\n"
            "Jede Schicht kennt nur die darunter. Route ruft Service, Service ruft Engine – nie umgekehrt.\n\n"
            "Vorteil: API-Anbieter wechseln? Nur den Fetcher ändern, alles andere bleibt."
        ),
    },
    {
        "category": "API",
        "project": "portfoliopilot",
        "title": "OAuth2 – Sicherer API-Zugang ohne Passwort",
        "text": (
            "🔄 *OAuth2 Flow*\n\n"
            "Du autorisierst dich einmal und bekommst zwei Tokens:\n"
            "• Access Token – kurzlebig (~1h), für API-Calls\n"
            "• Refresh Token – langlebig, holt neue Access Tokens\n\n"
            "📂 PortfolioPilot's `parqet_auth.py` erneuert Tokens automatisch."
        ),
    },
    {
        "category": "Performance",
        "project": "portfoliopilot",
        "title": "Caching – Einmal holen, mehrmals nutzen",
        "text": (
            "💾 *Caching mit TTL*\n\n"
            "API-Antworten werden als JSON-Dateien gespeichert – mit Ablaufdatum (TTL).\n\n"
            "5x am Tag die Seite laden? FMP wird nur 1x aufgerufen.\n"
            "Finanz-APIs haben Limits (250 Calls/Tag) – Caching ist überlebenswichtig!"
        ),
    },
    {
        "category": "Python",
        "project": "portfoliopilot",
        "title": "APScheduler – Automatische Zeitsteuerung",
        "text": (
            "⏰ *Scheduled Jobs*\n\n"
            "APScheduler führt Aufgaben automatisch aus:\n"
            "• 16:15 → Vollständige Analyse\n"
            "• Alle 15min (Mo-Fr, 8-22h) → Kurs-Updates\n"
            "• 16:30 → AI-Report via Telegram\n\n"
            "Konfiguration per Cron-Expressions – Industriestandard."
        ),
    },
    {
        "category": "Echtzeit",
        "project": "portfoliopilot",
        "title": "WebSocket vs. Polling – Echtzeit-Daten",
        "text": (
            "🌐 *WebSocket Streaming*\n\n"
            "Polling: Alle 5s fragen 'Hat sich was geändert?' → 240 Anfragen/Minute bei 20 Aktien.\n"
            "WebSocket: 1 offene Verbindung, Updates kommen automatisch sofort.\n\n"
            "📂 PortfolioPilot nutzt yFinance WebSocket für Live-Kurse."
        ),
    },
    {
        "category": "DevOps",
        "project": "portfoliopilot",
        "title": "Docker – Deine App in einer Box",
        "text": (
            "🐳 *Docker Container*\n\n"
            "Docker packt App + alle Abhängigkeiten in einen Container.\n"
            "Funktioniert auf deinem PC? → Funktioniert überall.\n\n"
            "PortfolioPilot Best Practices:\n"
            "• `python:3.12-slim` (kleines Image)\n"
            "• Non-Root User (Sicherheit)\n"
            "• Cloud Run Deployment"
        ),
    },
    {
        "category": "Resilienz",
        "project": "portfoliopilot",
        "title": "Graceful Degradation – Nie abstürzen",
        "text": (
            "🛡️ *Graceful Degradation*\n\n"
            "Wenn API-Keys fehlen → Demo-Modus statt Crash.\n"
            "Wenn Finnhub nicht startet → Warnung, aber App läuft weiter.\n"
            "Jedes Feature ist optional.\n\n"
            "Regel: Externe Dienste KÖNNEN immer ausfallen. Plane dafür!"
        ),
    },
    {
        "category": "KI",
        "project": "portfoliopilot",
        "title": "Google Gemini API – KI in der eigenen App",
        "text": (
            "🤖 *Gemini Integration*\n\n"
            "PortfolioPilot nutzt Gemini 2.5 Pro für:\n"
            "• Tägliche Portfolio-Analysen\n"
            "• Marktberichte mit Search Grounding\n"
            "• Earnings-Analysen\n"
            "• Risiko-Szenarien\n\n"
            "Fallback: Wenn Pro rate-limited → automatisch Flash nutzen."
        ),
    },
    {
        "category": "Scoring",
        "project": "portfoliopilot",
        "title": "Multi-Faktor Scoring – 9 Datenpunkte, 1 Score",
        "text": (
            "🏆 *Scoring Engine*\n\n"
            "Statt einer Zahl kombiniert PortfolioPilot 9 Faktoren:\n"
            "Quality 20% | Analyst 15% | Valuation 15%\n"
            "Technical 15% | Growth 12% | Quant 10%\n"
            "Sentiment 7% | Momentum 6% | Insider+ESG 5%\n\n"
            "Confidence zeigt an, wie viele Quellen verfügbar waren."
        ),
    },
    # ── Projekt: Job-Automation ──
    {
        "category": "DevOps",
        "project": "job_automation",
        "title": "Docker Compose – Mehrere Container orchestrieren",
        "text": (
            "🐳 *Docker Compose*\n\n"
            "Ein `docker-compose.yml` definiert 3 Services:\n"
            "• PostgreSQL-Datenbank\n"
            "• Python-Backend\n"
            "• Next.js-Frontend\n\n"
            "`docker compose up` → alles startet zusammen.\n"
            "Neuer Entwickler? Ein Befehl statt stundenlanges Setup."
        ),
    },
    {
        "category": "Code-Qualität",
        "project": "job_automation",
        "title": "Code Quality Toolchain – 4 automatische Prüfer",
        "text": (
            "🧹 *Linter & Formatter*\n\n"
            "• `black` → Formatiert Code einheitlich (nie wieder Stil-Diskussionen)\n"
            "• `isort` → Sortiert Imports automatisch\n"
            "• `flake8` → Findet Probleme (unbenutzte Variablen, zu lange Zeilen)\n"
            "• `mypy` → Prüft Datentypen BEVOR die App startet\n\n"
            "In Profi-Teams laufen sie bei jedem Git-Push automatisch."
        ),
    },
    {
        "category": "Datenbanken",
        "project": "job_automation",
        "title": "PostgreSQL statt JSON-Dateien",
        "text": (
            "🗃️ *Relationale Datenbank*\n\n"
            "JSON-Dateien skalieren nicht: bei 10.000 Einträgen wird alles langsam.\n\n"
            "PostgreSQL kann:\n"
            "• Komplexe Abfragen (WHERE, JOIN, ORDER BY)\n"
            "• Gleichzeitigen Zugriff ohne Datenverlust\n"
            "• Milliarden von Einträgen verwalten"
        ),
    },
    {
        "category": "Architektur",
        "project": "job_automation",
        "title": "Frontend/Backend als getrennte Services",
        "text": (
            "🔀 *Service-Architektur*\n\n"
            "Frontend (Next.js, Port 3000) und Backend (Python, Port 8000) sind völlig getrennt.\n\n"
            "Vorteil: Frontend durch Mobile App ersetzen? Backend bleibt.\n"
            "Backend in Go neu schreiben? Frontend merkt nichts.\n"
            "Getrennte Teams können parallel arbeiten."
        ),
    },
    # ── Allgemeine Konzepte ──
    {
        "category": "Grundlagen",
        "project": "portfoliopilot",
        "title": "API – Der Kellner zwischen Küche und Gast",
        "text": (
            "🔌 *Was ist eine API?*\n\n"
            "API = Application Programming Interface = Schnittstelle.\n\n"
            "Wie ein Kellner: Du (Client) gibst eine Bestellung auf, "
            "der Kellner (API) bringt sie zur Küche (Server) und kommt mit dem Essen (Daten) zurück.\n\n"
            "PortfolioPilot nutzt 7+ verschiedene APIs!"
        ),
    },
    {
        "category": "Frontend",
        "project": "ai_knowledge",
        "title": "Responsive Design – Eine App, alle Geräte",
        "text": (
            "📱 *Responsive Design*\n\n"
            "`@media (max-width: 640px) { ... }` – CSS-Regeln die nur auf kleinen Displays gelten.\n\n"
            "Über 60% des Web-Traffics kommt von Handys. "
            "Eine App die nur auf dem Desktop gut aussieht, verliert die Mehrheit der Nutzer."
        ),
    },
    {
        "category": "Grundlagen",
        "project": "ai_knowledge",
        "title": "LocalStorage – Daten im Browser speichern",
        "text": (
            "💾 *LocalStorage*\n\n"
            "`localStorage.setItem('key', JSON.stringify(data))` – speichert Daten direkt im Browser.\n\n"
            "Vorteile: Kein Server nötig, funktioniert offline, bleibt nach Neustart.\n"
            "Nachteil: Nur ~5MB, nur Strings, kein Sync zwischen Geräten."
        ),
    },
    {
        "category": "Telegram",
        "project": "portfoliopilot",
        "title": "Telegram Bot API – Dein eigener Bot",
        "text": (
            "🤖 *Telegram Bot*\n\n"
            "Ein Bot ist ein Programm das auf Nachrichten reagiert.\n"
            "PortfolioPilot nutzt Webhooks: Telegram sendet jede Nachricht an eine URL, "
            "der Server verarbeitet sie und antwortet.\n\n"
            "Befehle wie /portfolio, /score, /news machen den Bot interaktiv."
        ),
    },
    {
        "category": "Testing",
        "project": "portfoliopilot",
        "title": "pytest – Automatische Code-Tests",
        "text": (
            "🧪 *Automatisierte Tests*\n\n"
            "`pytest` führt Test-Funktionen aus und prüft ob dein Code korrekt ist.\n\n"
            "PortfolioPilot hat 15+ Testdateien die prüfen:\n"
            "• Scoring-Berechnung korrekt?\n"
            "• Telegram-Nachrichten richtig formatiert?\n"
            "• AI-Agent-Report vollständig?\n\n"
            "Tests = Sicherheitsnetz bei Änderungen."
        ),
    },
]


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def get_daily_tip(day_offset: int = 0) -> dict:
    """Gibt den Tipp des Tages zurück (rotiert täglich).

    Args:
        day_offset: Optionaler Offset zum heutigen Tag.

    Returns:
        Dict mit keys: category, project, title, text
    """
    today = date.today().toordinal() + day_offset
    idx = today % len(DAILY_TIPS)
    return DAILY_TIPS[idx]


def get_project_summary(project_key: str) -> str:
    """Gibt eine formatierte Zusammenfassung eines Projekts zurück.

    Args:
        project_key: Schlüssel aus PROJECT_KNOWLEDGE
                     (ai_knowledge, pokerpro, portfoliopilot, job_automation)

    Returns:
        Formatierter Text für Telegram.
    """
    # Fuzzy matching: "portfoliopilot", "PortfolioPilot", "finanz" → portfoliopilot
    key = _fuzzy_match_project(project_key)
    if not key:
        available = ", ".join(
            f"`{k}` ({v['name']})" for k, v in PROJECT_KNOWLEDGE.items()
        )
        return f"❓ Unbekanntes Projekt: `{project_key}`\n\nVerfügbar: {available}"

    p = PROJECT_KNOWLEDGE[key]
    lines = [
        f"{p['emoji']} *{p['name']}*",
        f"_{p['description']}_",
        f"Schwierigkeit: {p['difficulty']}",
        "",
        "🛠️ *Technologien:*",
    ]
    for tech in p["technologies"]:
        lines.append(f"  • {tech}")

    lines.append("")
    lines.append("✅ *Best Practices:*")
    for bp in p["best_practices"]:
        lines.append(f"  • {bp}")

    lines.append("")
    lines.append(f"💡 *Key Learning:* {p['key_learning']}")

    return "\n".join(lines)


def get_all_technologies() -> list[str]:
    """Gibt alle genutzten Technologien als flache, deduplizierte Liste zurück."""
    seen: set[str] = set()
    result: list[str] = []
    for project in PROJECT_KNOWLEDGE.values():
        for tech in project["technologies"]:
            if tech not in seen:
                seen.add(tech)
                result.append(tech)
    return result


def get_projects_overview() -> str:
    """Gibt eine Kurzübersicht aller Projekte zurück."""
    lines = ["📊 *Deine Antigravity-Projekte*\n"]
    for key, p in PROJECT_KNOWLEDGE.items():
        lines.append(f"{p['emoji']} *{p['name']}* ({p['difficulty']})")
        lines.append(f"  _{p['description']}_")
        lines.append(f"  `/wissen {key}`  für Details")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Interne Hilfsfunktion
# ─────────────────────────────────────────────────────────────

def _fuzzy_match_project(query: str) -> str | None:
    """Fuzzy-Match eines Projekt-Query auf einen PROJECT_KNOWLEDGE Key."""
    q = query.lower().strip().replace(" ", "_").replace("-", "_")

    # Exakter Match
    if q in PROJECT_KNOWLEDGE:
        return q

    # Alias-Map
    aliases = {
        "ai": "ai_knowledge",
        "ai_knowledge": "ai_knowledge",
        "knowledge": "ai_knowledge",
        "quiz": "ai_knowledge",
        "poker": "pokerpro",
        "pokerpro": "pokerpro",
        "simulator": "pokerpro",
        "finanz": "portfoliopilot",
        "portfoliopilot": "portfoliopilot",
        "aktien": "portfoliopilot",
        "portfolio": "portfoliopilot",
        "job": "job_automation",
        "jobs": "job_automation",
        "job_automation": "job_automation",
        "jobsearch": "job_automation",
        "career": "job_automation",
        "careerpilot": "job_automation",
        "mission": "job_automation",
    }

    if q in aliases:
        return aliases[q]

    # Partial match: "finanz" → "portfoliopilot"
    for key in PROJECT_KNOWLEDGE:
        if q in key or key in q:
            return key

    return None
