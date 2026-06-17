"""Structured logging + pluggable alerting for the live execution path.

Readiness-audit gap: the execution/risk paths emitted ``print``/jsonl only (one ``logging``
user in the whole tree) and had NO alert path — a kill-switch halt or an uncertain broker
state failed "loudly but silently" (a stack trace nobody watches). This module gives:

  * ``get_logger`` / ``log_event`` — one-line structured JSON logs (level, event, fields).
  * ``Notifier`` protocol + ``NullNotifier`` (off), ``LoggingNotifier`` (default, logs the
    alert), ``WebhookNotifier`` (best-effort POST; NEVER raises into the trading path).

It imports only stdlib and nothing from the execution package, so it stays cycle-free.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib import request as urllib_request

_HANDLER_FLAG = "_trader_json_handler"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping) and fields:
            payload["fields"] = dict(fields)
        return json.dumps(payload, default=str, sort_keys=True)


def get_logger(name: str = "trader.execution") -> logging.Logger:
    """A logger with a single JSON handler (idempotent — safe to call repeatedly)."""
    logger = logging.getLogger(name)
    if not any(getattr(handler, _HANDLER_FLAG, False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        setattr(handler, _HANDLER_FLAG, True)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    logger.log(level, message, extra={"event": event, "fields": fields})


@runtime_checkable
class Notifier(Protocol):
    def notify(
        self, *, level: str, event: str, message: str, fields: Mapping[str, Any]
    ) -> None: ...


class NullNotifier:
    """Alerts disabled."""

    def notify(self, *, level: str, event: str, message: str, fields: Mapping[str, Any]) -> None:
        return None


class LoggingNotifier:
    """Default notifier — records the alert as a structured log line (no external deps)."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or get_logger("trader.alerts")

    def notify(self, *, level: str, event: str, message: str, fields: Mapping[str, Any]) -> None:
        pylevel = logging.WARNING if level in ("warning", "error", "critical") else logging.INFO
        log_event(self._logger, event, message, level=pylevel, **dict(fields))


class WebhookNotifier:
    """Best-effort webhook alert. A failed alert is logged and SWALLOWED — an alerting outage
    must never halt or crash the executor. The URL is operator-configured (trusted); callers
    must not put secrets in ``fields`` (the body is posted verbatim)."""

    def __init__(self, url: str, *, timeout_s: float = 5.0, logger: logging.Logger | None = None):
        self._url = url
        self._timeout_s = timeout_s
        self._logger = logger or get_logger("trader.alerts")

    def notify(self, *, level: str, event: str, message: str, fields: Mapping[str, Any]) -> None:
        body = json.dumps(
            {"level": level, "event": event, "message": message, "fields": dict(fields)},
            default=str,
        ).encode("utf-8")
        req = urllib_request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib_request.urlopen(req, timeout=self._timeout_s).close()  # noqa: S310 — operator URL
        except Exception as exc:  # noqa: BLE001 — best-effort: never break trading on a failed alert
            log_event(
                self._logger,
                "alert_delivery_failed",
                f"webhook alert delivery failed: {exc}",
                level=logging.WARNING,
                original_event=event,
            )
