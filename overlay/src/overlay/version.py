"""The installed overlay version string — a core leaf helper (no app/render deps).

Lives at the package root, not in ``app.report``, so ``app.doctor`` (and anyone else) can read the
version without importing ``report`` — which would close a ``report ↔ doctor`` package cycle
(``report`` bundles ``doctor``'s checks; ``doctor`` only ever needed this one string back). See the
``.importlinter`` no-cycles contract.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def overlay_version() -> str:
    try:
        return version("saitenka-overlay")
    except PackageNotFoundError:  # pragma: no cover — source checkout without an installed dist
        return "0+unknown"
