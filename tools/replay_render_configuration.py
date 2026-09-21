"""Replay captured native geometry settings on original synthetic text, without mpv or private media."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

from compare_cached_characters import geometry_request_for
from diagnostic_findings import read_envelope
from saitenka_subtitles.geometry import FontProvider, FontSetup, GeometryPaletteEntry, RendererState
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from synthetic_characters import ROOT, SPEC, documents

from saitenka.app.render_evidence import safe_runtime_configuration
from saitenka.app.subtitle_fonts import FontEnvironment

_STATE = (
    "font_scale",
    "blur",
    "justify",
    "line_position",
    "line_spacing",
    "hinting",
    "selective_font_scale",
)
_REQUIRED = (
    "frame_width",
    "frame_height",
    "storage_width",
    "storage_height",
    "pixel_aspect",
    "margin_top",
    "margin_bottom",
    "margin_left",
    "margin_right",
    "use_margins",
    *_STATE,
)


def configuration(envelope: dict, *, owner: int | None = None, historical: bool = False) -> dict:
    runtime = safe_runtime_configuration(envelope.get("effective_runtime_configuration"))
    owners = runtime.get("owners", [])
    matches = [
        row
        for row in owners
        if row.get("status") == "collected" and (owner is None or row["owner"] == owner)
    ]
    if len(matches) != 1:
        raise ValueError("select exactly one retained geometry owner")
    selected = matches[0]
    reference = selected["last_published" if historical else "published"]
    if reference["status"] != "retained":
        raise ValueError("selected publication has no retained configuration")
    row = next(
        row for row in selected["configurations"] if row["revision"] == reference["revision"]
    )
    missing = [key for key in _REQUIRED if row["fields"].get(key) is None]
    if missing:
        raise ValueError("missing replay settings: " + ", ".join(missing))
    return {
        "owner": selected["owner"],
        "publication": reference,
        "historical": historical,
        "fields": row["fields"],
        "scope": "synthetic configuration class; not reconstruction of private text or fonts",
        "unreproduced": [
            "original-document",
            "resolved-font-faces",
            "mpv-composite",
            "display-color-pipeline",
        ],
    }


def _validate_values(values):
    size = (values["frame_width"], values["frame_height"])
    if any(
        type(value) is not int or value <= 0
        for value in (*size, values["storage_width"], values["storage_height"])
    ):
        raise ValueError("replay dimensions must be positive integers")
    if (
        max(*size, values["storage_width"], values["storage_height"]) > 8192
        or size[0] * size[1] > 16_777_216
    ):
        raise ValueError("replay dimensions exceed the bounded diagnostic canvas")
    ranges = {
        "pixel_aspect": (0.125, 8),
        "font_scale": (0.125, 8),
        "blur": (0, 10),
        "line_position": (0, 100),
        "line_spacing": (-1000, 1000),
    }
    for key, (lower, upper) in ranges.items():
        value = values[key]
        if (
            type(value) not in {int, float}
            or not lower <= value <= upper
            or not math.isfinite(value)
        ):
            raise ValueError(f"replay {key} outside bounded diagnostic range")
    for key in ("hinting", "justify"):
        if type(values[key]) is not int or values[key] not in range(4):
            raise ValueError(f"replay {key} must be a supported integer enum")
    for side in ("top", "bottom", "left", "right"):
        if type(values[f"margin_{side}"]) is not int:
            raise ValueError("replay margins must be integers")


def request_for_configuration(source, sample_ms, fonts, recipe):
    values = recipe["fields"]
    _validate_values(values)
    size = (values["frame_width"], values["frame_height"])
    request, _ = geometry_request_for(source, sample_ms, size, fonts)
    features = tuple(
        (number, values[key])
        for number, key in (
            (1, "feature_bidi_brackets"),
            (2, "feature_whole_text_layout"),
            (3, "feature_wrap_unicode"),
        )
        if type(values.get(key)) is bool
    )
    return replace(
        request,
        storage_size=(values["storage_width"], values["storage_height"]),
        pixel_aspect=values["pixel_aspect"],
        margins=tuple(values[f"margin_{side}"] for side in ("top", "bottom", "left", "right")),
        use_margins=values["use_margins"],
        renderer_state=RendererState(**{key: values[key] for key in _STATE}, features=features),
    )


def replay(recipe: dict) -> dict:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    font = (ROOT / spec["font"]["path"]).read_bytes()
    if hashlib.sha256(font).hexdigest() != spec["font"]["sha256"]:
        raise ValueError("synthetic font digest mismatch")
    fonts = FontEnvironment(
        FontSetup(default_family=spec["font"]["family"], font_provider=FontProvider.NONE),
        attachments=(("corpus.ttf", font),),
    )
    source = documents(spec)["ass"]
    backend, oracle = LibassGeometryBackend(), LibassGeometryBackend()
    rows = []
    try:
        for index, case in enumerate(spec["cases"]):
            try:
                inputs = request_for_configuration(source, 1500 + 2000 * index, fonts, recipe)
                actual = backend.render(inputs)
                original = replace(
                    inputs,
                    ass=inputs.native_ass,
                    native_ass=b"",
                    reserved_rgb=(),
                    palette=(GeometryPaletteEntry(inputs.palette[0].event_id, 0, 0xFFFFFF),),
                )
                reference = oracle.render(original)
                if not reference.tokens or not actual.tokens:
                    rows.append(
                        {
                            "case": case["id"],
                            "verdict": "inconclusive",
                            "reason": "empty-native-render",
                        }
                    )
                    continue
                rows.append(
                    {
                        "case": case["id"],
                        "verdict": "inconclusive",
                        "reason": "pixel-comparison-retired",
                        "libass_version": actual.libass_version,
                        "eligible_tokens": len(inputs.palette),
                        "found_tokens": len(actual.tokens),
                    }
                )
            except (ValueError, RuntimeError) as error:
                rows.append(
                    {"case": case["id"], "verdict": "inconclusive", "reason": type(error).__name__}
                )
    finally:
        backend.close()
        oracle.close()
    return {
        "schema": 1,
        "recipe": recipe,
        "font_sha256": spec["font"]["sha256"],
        "rows": rows,
        "qualification": "configuration replay only; independent mpv qualification not run",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--owner", type=int)
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    recipe = configuration(read_envelope(args.report), owner=args.owner, historical=args.historical)
    result = replay(recipe) if args.execute else {"recipe": recipe, "oracle_execution": "not-run"}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return int(args.execute and any(row["verdict"] != "passed" for row in result["rows"]))


if __name__ == "__main__":
    raise SystemExit(main())
