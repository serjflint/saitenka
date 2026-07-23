"""Structured logging pipeline: JSON lines to the rotating file (what the ``doctor`` "recent errors"
section tails and ``report`` bundles), a human-readable renderer to stderr, and a redaction processor
so a leaked secret or home path never reaches either sink. Stdlib ``logging.getLogger(__name__)``
call sites throughout the codebase are unchanged — they're bridged through the same processors via
:class:`structlog.stdlib.ProcessorFormatter`, so no call site needs to move to ``structlog``.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

import msgspec
import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from overlay.app.report import redact

ROOT_LOGGER_NAME = "overlay"


def _json_dumps(obj: EventDict, **_kw: Any) -> str:
    """``structlog.processors.JSONRenderer``'s serializer hook. msgspec, already a dependency, ships
    true free-threaded (``cp3XXt``) wheels — unlike orjson, which has none — so it's the faster choice
    over stdlib ``json`` without the GIL risk. ``**_kw`` absorbs ``JSONRenderer.__init__``'s
    ``**dumps_kw`` passthrough (unused here, msgspec takes no equivalent)."""
    return msgspec.json.encode(obj).decode("utf-8")


def _redact_event_dict(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Redact secrets + home/username from every string value (not just ``event``) — extra
    kwargs on a log call (``log.warning("fetch failed", url=url)``) are just as leak-prone."""
    for k, v in event_dict.items():
        if isinstance(v, str):
            event_dict[k] = redact(v)
    return event_dict


def configure_logging(log_path: Path) -> None:
    """Idempotent: a re-exec or repeated test call is a no-op once the root handler is attached."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    if root.handlers:
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)
    root.setLevel(logging.DEBUG)

    shared_processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_event_dict,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(serializer=_json_dumps),
        ],
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False, sort_keys=True),
        ],
    )

    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_formatter)

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(console_formatter)

    root.addHandler(fh)
    root.addHandler(sh)
