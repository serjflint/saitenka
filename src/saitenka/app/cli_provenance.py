"""Capture token origins during a CLI invocation, without retaining argument values."""

from __future__ import annotations

from contextvars import ContextVar

import cyclopts

from saitenka.app.option_evidence import RUN_FLAGS

_active: ContextVar[dict | None] = ContextVar("cli-provenance", default=None)


class DiagnosticApp(cyclopts.App):
    def __call__(self, *args, **kwargs):
        token = _active.set({})
        try:
            return super().__call__(*args, **kwargs)
        finally:
            _active.reset(token)


class DiagnosticToml(cyclopts.config.Toml):
    def __call__(self, app, commands, arguments):
        super().__call__(app, commands, arguments)
        active = _active.get()
        if active is None:
            return
        sources = {}
        for argument in arguments:
            name = argument.field_info.name
            if name not in RUN_FLAGS:
                continue
            tokens = argument.tokens
            if not tokens:
                sources[name] = "default"
            elif all(token.source == "cli" for token in tokens):
                sources[name] = "cli"
            elif all(token.source == self.source for token in tokens):
                sources[name] = "config-file"
            else:
                sources[name] = "unknown"
        active["sources"] = tuple(sorted(sources.items()))


def current_sources() -> tuple[tuple[str, str], ...]:
    return (_active.get() or {}).get("sources", ())
