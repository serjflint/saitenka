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

#: The four phases inside `EXTRACT_MS`, which is ~99% of a geometry render and was one opaque number.
#: `libass` itself costs ~0.1 ms; everything else measured is ours, so this is where a perf answer
#: has to come from — and "the extraction is slow" is not one.
EXTRACT_OWNERS_MS = "extract_owners_ms"  # zeroing the frame-sized pixel-owner map
EXTRACT_COLLECT_MS = "extract_collect_ms"  # attributing each layer's pixels to a token color
EXTRACT_VALIDATE_MS = "extract_validate_ms"  # rejecting partial/ambiguous tokens
EXTRACT_COVERAGE_MS = "extract_coverage_ms"  # re-reading the masks the raster device paints

#: Every metric a sink must accept — the contract's ENUMERABLE half.
#:
#: A sink is free to reject an unknown name, and the application's does, by raising. That turns this
#: list into a hard dependency of rendering at all: adding a metric here without teaching the sink
#: made every geometry render fail with `provider-error`, so a live session had no hit boxes, no
#: color and no scanning — from four timing calls. Both `NullTelemetry` and the tests' collectors
#: accept anything, so nothing caught it until it shipped.
GEOMETRY_METRICS = frozenset(
    {
        RENDERER_BUILD_MS,
        RENDER_MS,
        EXTRACT_MS,
        EXTRACT_OWNERS_MS,
        EXTRACT_COLLECT_MS,
        EXTRACT_VALIDATE_MS,
        EXTRACT_COVERAGE_MS,
    }
)


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
