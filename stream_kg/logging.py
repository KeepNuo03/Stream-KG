"""Structured logging configuration."""

import logging
import sys

from stream_kg.config import settings


def setup_logging() -> None:
    """Configure root logger for JSON-friendly structured output."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
