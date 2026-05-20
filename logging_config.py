"""PortfolioPilot - Structured Logging Setup

Konfiguriert structlog für:
  - Production (Cloud Run): JSON-Format → GCP Cloud Logging
  - Development: Farbige Console-Ausgabe mit Kontext

Aufruf in main.py: setup_logging()
"""
import logging
import sys

import structlog


def setup_logging(environment: str = "development"):
    """Konfiguriert Logging für die gesamte Anwendung.

    Args:
        environment: "production" → JSON, "development" → Console
    """
    is_prod = environment == "production"

    # Shared processors (beide Modi)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_prod:
        # Production: JSON für GCP Cloud Logging
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: Farbige Console-Ausgabe
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Standard-logging (alle Module die logging.getLogger nutzen) → structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Cloud Run: stderr verwenden (wird von Cloud Logging erfasst)
    # Development auf Windows: stdout mit UTF-8 encoding
    if is_prod:
        stream = sys.stderr
    else:
        stream = open(sys.stdout.fileno(), mode='w', encoding='utf-8',
                      errors='replace', closefd=False)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Drittanbieter-Logs dämpfen
    for noisy in ("httpx", "httpcore", "urllib3", "hpack", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
