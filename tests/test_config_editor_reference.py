"""Editor field metadata and value datasets; interactive sessions remain ordinary tests."""

import json
from pathlib import Path

import pytest

from saitenka.app import config_editor as editor

DATA = json.loads(
    (Path(__file__).parent / "fixtures/config_reference.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("record", DATA["editor-catalog"], ids=lambda r: r["id"])
def test_editor_field_metadata_reference(record):
    field = next(s for s in editor.catalog() if s.label == record["input"]["label"])
    assert {
        "kind": field.kind,
        "choices": list(field.choices),
        "path": list(field.toml_path),
        "optional": field.optional,
        "help": bool(field.help),
        "default": field.default,
    } == record["expected"]


@pytest.mark.parametrize("record", DATA["editor-defaults"], ids=lambda r: r["id"])
def test_editor_current_default_reference(record):
    v = record["input"]
    path = tuple(v.get("path", [v["field"]]))
    field = next(s for s in editor.catalog() if s.toml_path == path)
    assert editor.current_default(field, v["config"]) == record["expected"]


@pytest.mark.parametrize(
    "record", [r for r in DATA["editor-coerce"] if "value" in r["expected"]], ids=lambda r: r["id"]
)
def test_editor_value_conversion_reference(record):
    v = record["input"]
    field = next(s for s in editor.catalog() if s.label == v["field"])
    assert editor.coerce(field, v["input"]) == record["expected"]["value"]


@pytest.mark.parametrize(
    "record", [r for r in DATA["editor-coerce"] if "error" in r["expected"]], ids=lambda r: r["id"]
)
def test_invalid_editor_value_is_rejected(record):
    v = record["input"]
    field = next(s for s in editor.catalog() if s.label == v["field"])
    with pytest.raises(ValueError):
        editor.coerce(field, v["input"])
