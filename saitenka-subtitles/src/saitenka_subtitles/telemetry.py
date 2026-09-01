"""What the geometry backend reports, as a port the subtitle core owns.

The backend used to call ``saitenka.otel_metrics`` directly. That module keeps its histograms in
module-level globals that ``configure`` reassigns, so the call was a library reaching into the
application's telemetry singleton — the one edge out of this package, and the one thing standing
between it and its own distribution.

Structural, deliberately: nothing implements this by inheritance, so the telemetry layer can satisfy
it without importing the subtitle core, and the arrow points only one way.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Generator
    from contextlib import AbstractContextManager

#: Histograms the backend records into, by the name it passes to :meth:`GeometryTelemetry.record`.
RENDERER_BUILD_MS = "renderer_build_ms"
RENDER_MS = "render_ms"
EXTRACT_MS = "extract_ms"


class Span(Protocol):
    def set(self, key: str, value: object) -> None: ...


@runtime_checkable
class GeometryTelemetry(Protocol):
    def span(self, name: str) -> AbstractContextManager[Span]: ...

    def record(self, metric: str, milliseconds: float) -> None: ...


class _NullSpan:
    def set(self, key: str, value: object) -> None:
        pass


class NullTelemetry:
    """The default. A backend constructed without a sink still runs — measuring is the host's
    choice, and a test that only wants geometry should not have to decline it."""

    @contextmanager
    def span(self, name: str) -> Generator[_NullSpan]:
        _ = name
        yield _NullSpan()

    def record(self, metric: str, milliseconds: float) -> None:
        pass
