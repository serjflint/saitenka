"""Stage 8b (tooling): free-threaded verification — under ``poe test-ft`` (PYTHON_GIL=0) the GIL
must STAY off after all overlay imports (fugashi would silently re-enable it without the env)."""

import os
import sys
import sysconfig

import pytest

FT_BUILD = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
GIL_FORCED_OFF = os.environ.get("PYTHON_GIL") == "0"


@pytest.mark.skipif(
    not (FT_BUILD and GIL_FORCED_OFF),
    reason="only meaningful under `poe test-ft` (3.14t + PYTHON_GIL=0)",
)
def test_gil_stays_disabled_after_all_imports():
    # Import the heavy stack the live run uses — including fugashi via a real tokenize call.
    from overlay.app.controller import Reader  # noqa: F401
    from overlay.app.tokenize import tokenize

    tokenize("本を読む")  # forces the fugashi C extension to load
    assert sys._is_gil_enabled() is False, (
        "the GIL was re-enabled after imports — a C extension without free-threading "
        "declaration slipped in (this destroys the parallel prefetch render win)"
    )
