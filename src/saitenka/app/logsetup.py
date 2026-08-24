"""Structured logging pipeline: JSON lines to the rotating file (what the ``doctor`` "recent errors"
section tails and ``report`` bundles), a human-readable renderer to stderr, and a redaction processor
so a leaked secret or home path never reaches either sink. Stdlib ``logging.getLogger(__name__)``
call sites throughout the codebase are unchanged — they're bridged through the same processors via
:class:`structlog.stdlib.ProcessorFormatter`, so no call site needs to move to ``structlog``.
"""

from __future__ import annotations

import logging
import logging.handlers
from typing import TYPE_CHECKING, Any

import msgspec
import structlog

from saitenka.app.report import redact

if TYPE_CHECKING:
    from pathlib import Path

    from structlog.types import EventDict, Processor, WrappedLogger

ROOT_LOGGER_NAME = "saitenka"

#: Records the person watching mpv is meant to read — a startup banner, a session summary. The root
#: console handler is WARNING, so these used to be `print` calls inside the runtime, which skipped the
#: redaction every other record goes through and left the session summary out of `overlay.log`
#: entirely. Logging them here reaches the terminal AND the file, through the same processors.
CONSOLE_LOGGER_NAME = f"{ROOT_LOGGER_NAME}.console"
CONSOLE_PREFIX = "[saitenka]"


def _json_dumps(obj: EventDict, **_kw: Any) -> str:
    """``structlog.processors.JSONRenderer``'s serializer hook. msgspec, already a dependency, ships
    true free-threaded (``cp3XXt``) wheels — unlike orjson, which has none — so it's the faster choice
    over stdlib ``json`` without the GIL risk. ``**_kw`` absorbs ``JSONRenderer.__init__``'s
    ``**dumps_kw`` passthrough (unused here, msgspec takes no equivalent)."""
    return msgspec.json.encode(obj).decode("utf-8")


def _redact_event_dict(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Redact secrets + home/username from every string value (not just ``event``) — extra
    kwargs on a log call (``log.warning("fetch failed", url=url)``) are just as leak-prone."""
    for k, v in event_dict.items():
        if isinstance(v, str):
            event_dict[k] = redact(v)
    return event_dict


def _add_session(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Stamp the per-process session id on every record, so a report can tell which run each line is
    from (overlay.log accumulates across runs). File sink only — the console renderer drops it (see
    :func:`_drop_session`) so the session is quoted once, at launch, not on every stderr line."""
    from saitenka.session import session_id

    event_dict.setdefault("session", session_id())
    return event_dict


def _drop_session(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Console-only: strip the session id so it isn't repeated on every human-readable stderr line (the
    launch banner already prints it once). It stays in the JSON file log for report run-attribution."""
    event_dict.pop("session", None)
    return event_dict


def _render_console_line(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> str:
    """A user-facing line rendered as itself — no level, timestamp, or ``key=value`` tail.

    `ConsoleRenderer` is right for a log the reader is debugging with and wrong for one sentence
    addressed to them; this is the only sink where the message IS the output.
    """
    return f"{CONSOLE_PREFIX} {event_dict.get('event', '')}"


def user_facing_logger() -> logging.Logger:
    """The logger for a line the user is meant to read. See :data:`CONSOLE_LOGGER_NAME`."""
    return logging.getLogger(CONSOLE_LOGGER_NAME)


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
        _add_session,
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
            _drop_session,  # session is quoted once at launch; keep it off every stderr line
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

    # INFO, unlike `sh` — that is the whole point. Propagation still carries the record to `fh`, and
    # `sh`'s WARNING floor is what stops it being printed a second time.
    announce = logging.StreamHandler()
    announce.setLevel(logging.INFO)
    announce.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                _render_console_line,
            ],
        )
    )
    user_facing_logger().addHandler(announce)
