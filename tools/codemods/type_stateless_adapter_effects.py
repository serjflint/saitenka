"""Type every stateless adapter's effect boundary from its closed intent union.

    uv run --group codemod python tools/codemods/type_stateless_adapter_effects.py [--check]

Only ``apply(self, effect: object, /)`` on the seven named adapters is eligible. Other object-typed
methods and same-named classes are left untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = {
    "HoverAdapter": ("hover_adapter.py", "hover_intents.HoverEffect"),
    "InteractionAdapter": ("interaction_adapter.py", "interaction_intents.InteractionEffect"),
    "MineAdapter": ("mine_adapter.py", "mine_intents.MineEffect"),
    "PanelAdapter": ("panel_adapter.py", "panel_intents.PanelEffect"),
    "ProfileAdapter": ("profile_adapter.py", "profile_intents.ProfileEffect"),
    "SessionAdapter": ("session_adapter.py", "session_intents.SessionEffect"),
    "SubtitleAdapter": ("subtitle_adapter.py", "subtitle_intents.SubtitleEffect"),
}
PATHS = tuple(ROOT / "src/saitenka/app" / filename for filename, _ in ADAPTERS.values())


class TypeStatelessAdapterEffects(cst.CSTTransformer):
    def __init__(self) -> None:
        self._classes: list[str] = []
        self.count = 0

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._classes.append(node.name.value)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        del original_node
        self._classes.pop()
        return updated_node

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        if not self._classes or original_node.name.value != "apply":
            return updated_node
        target = ADAPTERS.get(self._classes[-1])
        if target is None:
            return updated_node
        positional = list(updated_node.params.posonly_params)
        effect = next((param for param in positional if param.name.value == "effect"), None)
        if effect is None or effect.annotation is None:
            return updated_node
        if not isinstance(effect.annotation.annotation, cst.Name):
            return updated_node
        if effect.annotation.annotation.value != "object":
            return updated_node
        effect_type = cst.parse_expression(target[1])
        positional[positional.index(effect)] = effect.with_changes(
            annotation=effect.annotation.with_changes(annotation=effect_type)
        )
        self.count += 1
        return updated_node.with_changes(
            params=updated_node.params.with_changes(posonly_params=positional)
        )


def transformed(source: str) -> tuple[str, int]:
    transform = TypeStatelessAdapterEffects()
    tree = cst.parse_module(source).visit(transform)
    return tree.code, transform.count


def _self_test() -> None:
    source = (
        "class HoverAdapter:\n"
        "    def apply(self, effect: object, /) -> None:\n"
        "        pass\n"
        "class OtherAdapter:\n"
        "    def apply(self, effect: object, /) -> None:\n"
        "        pass\n"
    )
    expected = source.replace("effect: object", "effect: hover_intents.HoverEffect", 1)
    rewritten, count = transformed(source)
    if rewritten != expected or count != 1:
        raise AssertionError((rewritten, count))


def main(argv: list[str]) -> int:
    _self_test()
    check = "--check" in argv
    total = touched = 0
    for path in PATHS:
        source = path.read_text(encoding="utf-8")
        rewritten, count = transformed(source)
        if not count:
            continue
        total += count
        touched += 1
        if not check:
            path.write_text(rewritten, encoding="utf-8")
    verb = "would rewrite" if check else "rewrote"
    print(f"type-stateless-adapter-effects: {verb} {total} site(s) in {touched} file(s)")
    return int(check and total != 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
