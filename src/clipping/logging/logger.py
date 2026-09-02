"""Structured JSON Logging System for Clipping Automation."""

import logging
import sys
from typing import Any, Dict
import structlog
from structlog.types import EventDict, WrappedLogger


def mask_sensitive_keys(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Processor to redact sensitive keywords, API keys, and tokens from logs."""
    sensitive_keywords = {"token", "secret", "password", "key", "credential", "auth"}
    for key, value in list(event_dict.items()):
        key_lower = str(key).lower()
        if any(sens in key_lower for sens in sensitive_keywords):
            if isinstance(value, str) and len(value) > 4:
                event_dict[key] = f"{value[:2]}***REDACTED***{value[-2:]}"
            else:
                event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Configures structlog processors and standard library root logger."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        mask_sensitive_keys,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if log_format == "json":
        formatter = structlog.processors.JSONRenderer()
    else:
        formatter = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "clipping") -> structlog.BoundLogger:
    """Returns a context-bound structured logger."""
    return structlog.get_logger(name)
