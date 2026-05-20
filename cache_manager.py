"""PortfolioPilot - Zentraler Cache Manager

Einheitliches Caching mit In-Memory-Layer und Disk-Persistierung.
Ersetzt die duplizierten _load_cache()/_save_cache() Funktionen in allen Fetchers.
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

# Volatile Caches die beim Start gelöscht werden (Technicals)
VOLATILE_CACHES = {"technical"}

# Persistente Caches die beim Start erhalten bleiben
PERSISTENT_CACHES = {"parqet", "currency", "fmp", "yfinance", "fear_greed"}


class CacheManager:
    """Thread-safe Cache mit Memory-First, Disk-Backup Strategie.

    Features:
        - In-Memory für schnelle Reads (kein Disk-I/O pro Zugriff)
        - Automatisches Laden von Disk beim ersten Zugriff
        - Konfigurierbarer TTL pro Instanz
        - Negative Caching (merkt sich fehlgeschlagene Lookups)
        - flush() schreibt alle Änderungen auf Disk
        - Thread-safe via threading.Lock
        - Registry: Alle Instanzen werden registriert für globale Operationen
    """

    # Klassen-Registry aller CacheManager-Instanzen
    _registry: dict[str, "CacheManager"] = {}
    _registry_lock = threading.Lock()

    def __init__(self, name: str, ttl_hours: Optional[int] = None):
        """
        Args:
            name: Cache-Name (wird als Dateiname verwendet: {name}_cache.json)
            ttl_hours: Cache-TTL in Stunden. Default: settings.CACHE_TTL_HOURS
        """
        self.name = name
        self.file = settings.CACHE_DIR / f"{name}_cache.json"
        self.ttl = timedelta(hours=ttl_hours or settings.CACHE_TTL_HOURS)
        self._memory: dict = {}
        self._cached_at: Optional[datetime] = None
        self._dirty = False
        self._stale = False
        self._loaded = False
        self._lock = threading.Lock()

        # Automatisch in Registry eintragen
        with CacheManager._registry_lock:
            CacheManager._registry[name] = self

    def _ensure_loaded(self):
        """Lädt Cache von Disk beim ersten Zugriff.
        
        Bei abgelaufenen Daten: Daten werden als 'stale' markiert aber trotzdem
        geladen (besser als leere Felder im UI). Ein neuer Refresh überschreibt sie.
        """
        if self._loaded:
            return
        self._loaded = True

        if not self.file.exists():
            return

        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            cached_at_str = data.pop("_cached_at", "")
            if cached_at_str:
                cached_time = datetime.fromisoformat(cached_at_str)
                if datetime.now() - cached_time < self.ttl:
                    self._memory = data
                    self._cached_at = cached_time
                    self._stale = False
                    return
                # Cache abgelaufen → trotzdem als Stale-Fallback laden
                self._memory = data
                self._cached_at = cached_time
                self._stale = True
                logger.debug(
                    f"Cache '{self.name}' abgelaufen seit "
                    f"{(datetime.now() - cached_time).total_seconds():.0f}s "
                    f"— nutze als Stale-Fallback ({len(data)} Einträge)"
                )
                return
            # Kein Zeitstempel → Cache nicht vertrauenswürdig
            self._memory = {}
        except Exception:
            self._memory = {}

    def get(self, key: str) -> Optional[Any]:
        """Holt einen Wert aus dem Cache. Returns None wenn nicht vorhanden."""
        with self._lock:
            self._ensure_loaded()
            return self._memory.get(key)

    def set(self, key: str, value: Any):
        """Setzt einen Wert im Cache (zunächst nur In-Memory)."""
        with self._lock:
            self._ensure_loaded()
            self._memory[key] = value
            self._dirty = True

    def has(self, key: str) -> bool:
        """Prüft ob ein Key im Cache existiert."""
        with self._lock:
            self._ensure_loaded()
            return key in self._memory

    def set_negative(self, key: str):
        """Markiert einen Key als 'nicht verfügbar' um wiederholte API-Calls zu vermeiden."""
        self.set(key, "__NEGATIVE__")

    def is_negative(self, key: str) -> bool:
        """Prüft ob ein Key als negativ gemerkt wurde."""
        return self.get(key) == "__NEGATIVE__"

    def flush(self):
        """Schreibt alle Änderungen auf Disk."""
        with self._lock:
            if not self._dirty:
                return

            try:
                data = dict(self._memory)
                data["_cached_at"] = datetime.now().isoformat()
                self.file.write_text(
                    json.dumps(data, indent=2, default=str),
                    encoding="utf-8",
                )
                self._dirty = False
            except Exception as e:
                logger.warning(f"Cache '{self.name}' flush fehlgeschlagen: {e}")

    def clear(self):
        """Löscht den gesamten Cache (Memory + Disk)."""
        with self._lock:
            self._memory = {}
            self._cached_at = None
            self._dirty = False
            self._loaded = True
            if self.file.exists():
                self.file.unlink()
        logger.info(f"Cache '{self.name}' gelöscht")

    @property
    def size(self) -> int:
        """Anzahl der gecachten Einträge."""
        with self._lock:
            self._ensure_loaded()
            return len(self._memory)

    @property
    def age_hours(self) -> float | None:
        """Alter des Caches in Stunden (seit letztem Schreiben). None wenn nie geschrieben."""
        with self._lock:
            self._ensure_loaded()
            if self._cached_at is None:
                return None
            return (datetime.now() - self._cached_at).total_seconds() / 3600

    def is_fresh(self, key: str, max_hours: float = 6.0) -> bool:
        """Prüft ob ein Cache-Eintrag existiert und der gesamte Cache frisch ist.

        Nutzt den globalen _cached_at Zeitstempel des Caches.
        Returns True wenn: Key existiert UND Cache < max_hours alt.
        """
        with self._lock:
            self._ensure_loaded()
            if key not in self._memory:
                return False
            if self._memory[key] == "__NEGATIVE__":
                return False
            if self._cached_at is None:
                return False
            age = (datetime.now() - self._cached_at).total_seconds() / 3600
            return age < max_hours

    # --- Statische Methoden für globale Cache-Operationen ---

    @staticmethod
    def clear_volatile_caches():
        """Löscht alle volatilen Caches (yFinance, Technical, Fear&Greed).

        Wird beim Server-Start aufgerufen um saubere Preisdaten zu garantieren.
        FMP-Fundamentals, Parqet-Positionen und Wechselkurse bleiben erhalten.
        """
        cleared = 0

        # Lösche Instanzen aus der Registry
        with CacheManager._registry_lock:
            for name, cache in CacheManager._registry.items():
                if name in VOLATILE_CACHES:
                    cache.clear()
                    cleared += 1

        # Lösche auch Cache-Dateien die noch keine Instanz haben
        for name in VOLATILE_CACHES:
            cache_file = settings.CACHE_DIR / f"{name}_cache.json"
            if cache_file.exists():
                try:
                    cache_file.unlink()
                    cleared += 1
                except Exception as e:
                    logger.warning(f"Cache-Datei '{cache_file}' konnte nicht gelöscht werden: {e}")

        logger.info(f"🗑️ {cleared} volatile Caches beim Start gelöscht")
        return cleared

    @staticmethod
    def clear_all_caches():
        """Löscht ALLE Caches (volatile + persistente). Für manuellen Full-Reset."""
        cleared = 0
        with CacheManager._registry_lock:
            for name, cache in CacheManager._registry.items():
                cache.clear()
                cleared += 1

        # Lösche alle Cache-Dateien
        cache_dir = settings.CACHE_DIR
        if cache_dir.exists():
            for f in cache_dir.glob("*_cache.json"):
                try:
                    f.unlink()
                    cleared += 1
                except Exception:
                    pass

        logger.info(f"🗑️ {cleared} Caches gelöscht (Full Reset)")
        return cleared

    @staticmethod
    def cleanup_stale_files():
        """Löscht verwaiste Dateien aus der JSON→SQLite Migration.

        Räumt auf:
        - score_history.json (jetzt in SQLite)
        - portfolio_history.json.bak (Migrations-Backup)
        """
        stale_files = [
            "score_history.json",
            "portfolio_history.json.bak",
        ]
        removed = 0
        for filename in stale_files:
            f = settings.CACHE_DIR / filename
            if f.exists():
                try:
                    f.unlink()
                    removed += 1
                    logger.info(f"🧹 Verwaiste Datei gelöscht: {filename}")
                except Exception as e:
                    logger.warning(f"Konnte {filename} nicht löschen: {e}")
        if removed:
            logger.info(f"🧹 {removed} verwaiste Cache-Dateien aufgeräumt")
        return removed
