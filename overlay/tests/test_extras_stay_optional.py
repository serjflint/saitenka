"""Architecture contract: optional-EXTRA packages stay optional.

The base wheel (``saitenka`` with no extras) must import and run, so the optional packages — deinflect /
taffylite / jamdict / OpenTelemetry (see ``overlay/pyproject.toml`` ``[project.optional-dependencies]``)
— must load lazily, only when their feature is actually used. This asserts the real invariant behaviourally:
in a fresh interpreter, importing each entry point of the base install's eager graph pulls **none** of them
into ``sys.modules``. It holds whether or not the extras happen to be installed in the test env — it checks
what the eager import graph *touches*, not what's on disk. A regression that adds a top-level ``import
taffylite`` to a module on any of these paths fails here.

The entry points span the CLI surface AND the ``run`` / ``attach`` runtime graph — the payload the base
wheel actually loads to play a video (the Reader, its dep builder, the run/attach impl), where a stray
top-level ``import taffylite`` / ``import saitenka_deinflect`` in the render or deinflect stack would hide.

Complements ``test_anki_optional.py``: that contract is the optional *service* (Anki down at runtime),
this is the optional *packages* (extra absent at install)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

# extra -> the top-level import name(s) it provides.
OPTIONAL_MODULES = [
    "saitenka_deinflect",  # [deinflect] GPL inflection add-on
    "taffylite",  # [layout-engine] Rust flexbox row-solver
    "jamdict",  # [jmdict] JMdict English fallback
    "jamdict_data",  # [jmdict] its bundled database
    "opentelemetry",  # [telemetry] the OTel SDK
]

# The base install's eager import graph: the CLI surface plus what `run`/`attach` load to play a video.
# cli imports cli_run at module top; run/attach then build a Reader (controller) via reader_deps and drive
# the render/tooltip stack — the modules where a top-level optional import would actually hide.
ENTRY_POINTS = [
    "overlay.app.cli",  # console-script surface: every command
    "overlay.app.cli_run",  # the run/attach command impl
    "overlay.app.reader_deps",  # dep builder (dictdb / scoring / wordlists / anki)
    "overlay.app.controller",  # the Reader — the run/attach runtime payload (tooltip + render stack)
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


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_entry_point_pulls_no_optional_extra(entry, tmp_path):
    leaked = _extras_pulled_by_importing(entry, tmp_path)
    assert leaked == [], (
        f"importing {entry} eagerly pulled optional extras {leaked} — import them lazily (inside the "
        "function that uses the feature) so the base wheel runs without extras installed"
    )
