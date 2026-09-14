"""Generate separately pinned original ASS/SRT corpora without private media."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pysubs2
from character_masks import digest, manifest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests/fixtures/character_corpus/cases.json"


def documents(spec: dict) -> dict[str, str]:
    result = {}
    for suffix in ("ass", "srt"):
        document = pysubs2.SSAFile()
        document.info.update(PlayResX="1280", PlayResY="720", **{"YCbCr Matrix": "None"})
        style = document.styles["Default"]
        style.fontname = spec["font"]["family"]
        style.fontsize = 40
        style.outline = 0
        style.shadow = 0
        for index, case in enumerate(spec["cases"]):
            name = case["id"]
            document.styles[name] = style.copy()
            document.styles[name].spacing = case["spacing"]
            text = case["text"].replace("\n", r"\N")
            document.events.append(
                pysubs2.SSAEvent(
                    start=1000 + index * 2000,
                    end=2000 + index * 2000,
                    text=(case["tags"] if suffix == "ass" else "") + text,
                    style=name,
                )
            )
        result[suffix] = document.to_string(suffix)
    return result


def generate(output: Path) -> dict:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    font = ROOT / spec["font"]["path"]
    if hashlib.sha256(font.read_bytes()).hexdigest() != spec["font"]["sha256"]:
        raise ValueError("bundled corpus font digest changed")
    if not (ROOT / spec["font"]["license_file"]).is_file():
        raise ValueError("bundled corpus font license missing")
    output.mkdir(parents=True, exist_ok=False)
    corpora = {}
    for suffix, source in documents(spec).items():
        path = output / f"original.{suffix}"
        path.write_text(source, encoding="utf-8")
        document = pysubs2.SSAFile.from_string(source, format_=suffix)
        corpora[suffix] = manifest(
            [(row.start, row.end, row.plaintext) for row in document],
            {
                "generator": spec["generator"],
                "spec_sha256": digest(spec),
                "document_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "font": spec["font"],
                "case_ids": [case["id"] for case in spec["cases"]],
                "format": suffix,
                "support": "unqualified until executed; SRT does not preserve ASS styles or tags",
            },
        )
    (output / "corpora.json").write_text(
        json.dumps(corpora, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return corpora


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    generated = generate(args.output)
    print(json.dumps({key: value["census_sha256"] for key, value in generated.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
