"""Planted controls for the layer-independence gate.

The evasion, not the example: the edge this gate exists for was a `TYPE_CHECKING` import, which is
precisely the form `.importlinter` is configured not to see. A gate that only catches the
module-level form would have passed on the defect it was written for.
"""

from __future__ import annotations

import ast

import pytest
from library_layer_independence import _app_imports, tracked_library_sources, violations

_MODULE_LEVEL = "from saitenka.app.features.analysis.episode_analysis import EpisodeAnalysis"
_TYPE_CHECKING = f"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    {_MODULE_LEVEL}
"""


def test_the_tree_is_clean_today():
    assert violations() == []


def test_the_census_is_not_empty():
    """Without this, every assertion above is vacuous if the file listing ever returns nothing."""
    sources = tracked_library_sources()
    assert len(sources) > 20
    assert not any(source.startswith("src/saitenka/app/") for source in sources)


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("module-level", _MODULE_LEVEL),
        # The form that actually shipped, and the only one the import-linter contracts are blind to.
        ("type-checking", _TYPE_CHECKING),
        ("plain-import", "import saitenka.app.dictionary"),
        ("from-package", "from saitenka import app"),
        ("submodule-attr", "from saitenka.app import paths"),
    ],
)
def test_every_shape_of_an_application_import_is_caught(label, source):
    assert _app_imports(ast.parse(source)), f"{label} import evaded the gate"


@pytest.mark.parametrize(
    "source",
    [
        "from saitenka.render import flow",
        "from saitenka_dict import Translator",
        # Prose naming the application is not an import — fonts.py and otel_metrics.py both do this
        # in a docstring, and flagging them would make the gate unusable.
        '"""See :func:`saitenka.app.telemetry.configure`."""',
        "APP = 'saitenka.app'",
    ],
)
def test_a_non_import_mention_of_the_application_is_not_a_violation(source):
    assert _app_imports(ast.parse(source)) == []
