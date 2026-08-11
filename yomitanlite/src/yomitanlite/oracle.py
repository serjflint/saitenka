from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable


@dataclass(frozen=True, slots=True)
class OracleQuery:
    kind: Literal["term", "kanji"]
    text: str
    mode: str | None = None
    options: Any = None


@dataclass(frozen=True, slots=True)
class HeadlessYomitanOracle:
    checkout: Path
    dictionary_directory: Path
    dictionary_name: str
    options_presets: dict[str, Any]
    runner: Traversable

    @classmethod
    def for_upstream_fixture(
        cls, checkout: str | Path, runner: str | Path | None = None
    ) -> HeadlessYomitanOracle:
        root = Path(checkout).expanduser().resolve()
        fixture = root / "test/data/dictionaries/valid-dictionary1"
        presets = json.loads((root / "test/data/translator-test-inputs.json").read_text())[
            "optionsPresets"
        ]
        default_runner = files("yomitanlite").joinpath("yomitan_oracle.mjs")
        selected_runner = Path(runner) if runner is not None else default_runner
        return cls(root, fixture, "Test Dictionary", presets, selected_runner)

    def batch(self, queries: tuple[OracleQuery, ...]) -> list[Any]:
        request = {
            "checkout": str(self.checkout),
            "dictionaryDirectory": str(self.dictionary_directory),
            "dictionaryName": self.dictionary_name,
            "optionsPresets": self.options_presets,
            "queries": [asdict(query) for query in queries],
        }
        with as_file(self.runner) as runner:
            completed = subprocess.run(
                ["node", str(runner)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )
        return json.loads(completed.stdout)

    def revision(self) -> str:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.checkout,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        return completed.stdout.strip()
