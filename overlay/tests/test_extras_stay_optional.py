"""Architecture contract: optional-EXTRA packages stay optional.

The base wheel (``saitenka`` with no extras) must import and run, so the optional packages — deinflect /
taffylite / jamdict / OpenTelemetry (see ``overlay/pyproject.toml`` ``[project.optional-dependencies]``)
— must load lazily, only when their feature is actually used. This asserts the real invariant behaviourally:
in a fresh interpreter, importing the console-script entry point (``overlay.app.cli``) pulls **none** of
them into ``sys.modules``. It holds whether or not the extras happen to be installed in the test env —
it checks what the eager import graph *touches*, not what's on disk. A regression that adds a top-level
``import taffylite`` to a module on the CLI's import path fails here.

Complements ``test_anki_optional.py``: that contract is the optional *service* (Anki down at runtime),
this is the optional *packages* (extra absent at install)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

# extra -> the top-level import name(s) it provides.
OPTIONAL_MODULES = [
    "saitenka_deinflect",  # [deinflect] GPL inflection add-on
    "taffylite",  # [layout-engine] Rust flexbox row-solver
    "jamdict",  # [jmdict] JMdict English fallback
    "jamdict_data",  # [jmdict] its bundled database
    "opentelemetry",  # [telemetry] the OTel SDK
]


def _extras_pulled_by_importing(entry: str, tmp_path) -> list[str]:
    """Optional-extra top-level modules present in ``sys.modules`` after a fresh ``import <entry>``."""
    code = (
        "import sys, json;"
        f"import {entry};"
        "roots = {m.split('.')[0] for m in sys.modules};"
        f"print(json.dumps(sorted(roots & set({OPTIONAL_MODULES!r}))))"
    )
    env = {**os.environ, "SAITENKA_CONFIG": str(tmp_path / "no-such-config.toml")}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr  # the base import must SUCCEED, never ImportError
    return json.loads(out.stdout)


def test_importing_the_cli_pulls_no_optional_extra(tmp_path):
    leaked = _extras_pulled_by_importing("overlay.app.cli", tmp_path)
    assert leaked == [], (
        f"importing the CLI eagerly pulled optional extras {leaked} — import them lazily (inside the "
        "function that uses the feature) so the base wheel runs without extras installed"
    )
