"""No test reaches past `tests/driver.py` without an argument — the input-seam ratchet.

Uplifted from `vibe/pr408_ledger.py`'s `driver_residue`, which is git-ignored and so has never run
in CI. It guarded work that is finished: every controller test drives the real input path now, and
nothing stops the next one from poking `set_hover` / `_show_tooltip` again.

`Driver` exists so a test exercises what a cursor exercises — the hit-test, the dictionary probe,
the dwell timers. Reaching past it sets a state the runtime can only arrive at through that path,
which stays green until the path changes and then fails somewhere else entirely.

**The classifier over-counted three times before it was believed**, each time the same shape: a name
matched and a meaning did not (`tests/driver.py` counting itself; a class that had become sanctioned
when the hover decision got its own home; `sidebar.on_click(ports, x, y)`, a module function, read as
`SessionController.on_click()`). So the exemptions below are a *per-site list with a reason each*, never a rule:
a rule excuses the next test of the same shape, which is exactly how the over-counts survived.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Sites that reach past `Driver` on purpose, one argument each. Adding a row is a deliberate
#: re-blessing, like a golden or the corpus lock — not a way to make a red test green.
_ARGUED: dict[tuple[str, str], str] = {
    ("test_session_controller.py", "test_set_hover_refuses_the_nothing_hovered_sentinel"): (
        "asserts the seam refuses an argument the input path cannot produce; calling the seam IS "
        "the test"
    ),
    ("test_session_controller.py", "test_property_change_event_drives_hover"): (
        "the subject is that hover reads the *observed* property; a `Driver.move` writes "
        "`ipc.props` directly and would answer the question it is asking"
    ),
    (
        "test_session_controller.py",
        "test_show_tooltip_renders_only_the_head_then_grows_on_scroll",
    ): (
        "a jump to `full_height` — the deferred tail only measures at the bottom, and a notch "
        "count does not reach it"
    ),
    ("test_live_mpv.py", "test_live_cursor_over_tooltip_keeps_lease_and_captures_click"): (
        "30% of screen height against a real mpv, so the tip clearly leaves its first band"
    ),
    ("test_replay_sessions.py", "_apply"): (
        "replays recorded (action, arg) steps; the corpus records offsets, and the fixture's "
        "tooltip covers words 1-2 so a cursor cannot reach them"
    ),
    ("test_tooltip_statemachine.py", "hover"): (
        "the fixture's tooltip (1280x864 at y=352) covers words 1 and 2, so a cursor that opened "
        "word 0 can never reach another — correct runtime behaviour, and it collapses the rule to "
        "one word. The subject is panel geometry across transitions"
    ),
    ("test_tooltip_statemachine.py", "scroll"): (
        "the oracle is scroll *position* vs drawn geometry; the two producers (a wheel notch, a "
        "TIP_UP/DOWN page) reach few of the offsets where clamping and band boundaries live"
    ),
}


def _residue_class(node: ast.Call, *, hover_established: bool) -> str | None:
    """Which seam a call reaches past, or None when it does not reach past one."""
    attribute = node.func.attr
    if attribute == "scroll_tip":
        return "scroll-in-pixels"
    if attribute == "_update_hover":
        return "hover-without-a-cursor"
    if attribute in {"on_click", "copy_click"}:
        # `SessionController.on_click()` takes nothing — it reads the cursor. Anything with arguments is a
        # different symbol (`sidebar.on_click(ports, x, y)`), a predicate under the seam rather than
        # an input entry point. Matching the bare name counted ten of those as residue.
        return None if (node.args or node.keywords) else "click-without-a-cursor"
    if attribute == "_show_tooltip":
        # A `_show_tooltip` in a body that writes `hover` itself is a paint isolated from hover on
        # purpose (raster, cache, scale) — one side of a cut seam, which AGENTS.md sanctions.
        return None if hover_established else "tooltip-without-a-hover"
    if attribute == "set_hover":
        return (
            "hover-without-a-cursor"
            if node.args and isinstance(node.args[0], ast.UnaryOp)
            else ("tooltip-without-a-hover")
        )
    return None


def _residue() -> list[tuple[str, str, int, str]]:
    """`(file, enclosing function, line, class)` for every site reaching past the facade."""
    found: list[tuple[str, str, int, str]] = []
    for path in sorted(TESTS.glob("**/*.py")):
        if path.name == "driver.py":
            continue  # the facade poking the real entry points IS the facade; it is not residue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes = [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        # One walk per scope, not one per (scope, node): the naive form is quadratic in a 4000-line
        # test module and cost this file twenty-odd seconds of `poe test`. Innermost wins — a rule
        # method inside a state-machine class is the name an argument is written against, not the
        # class body enclosing it.
        enclosing: dict[int, str] = {}
        established_hover: dict[int, bool] = {}
        for fn in scopes:
            selects_hover = any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "select"
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "tooltip_controller"
                for call in ast.walk(fn)
            )
            for inner in ast.walk(fn):
                enclosing[id(inner)] = fn.name
                established_hover[id(inner)] = selects_hover
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            klass = _residue_class(node, hover_established=established_hover.get(id(node), False))
            if klass is not None:
                found.append((path.name, enclosing.get(id(node), ""), node.lineno, klass))
    return found


def test_no_test_reaches_past_the_input_facade_without_an_argument():
    """The ratchet. A new `set_hover(i)` / `_show_tooltip(i)` / `_update_hover()` in a test is a
    precondition established off the path production uses, and it fails here rather than silently
    months later when the seam moves."""
    unargued = [
        f"{file}:{line} in {function} ({klass})"
        for file, function, line, klass in _residue()
        if (file, function) not in _ARGUED
    ]

    assert not unargued, (
        "tests reaching past `tests/driver.py`:\n  "
        + "\n  ".join(unargued)
        + "\n\nDrive the real input path (`Driver.move_to_word` / `.click()` / `.wheel()`), or add "
        "the site to `_ARGUED` with the reason a cursor cannot reach it."
    )


def test_every_argued_site_still_exists():
    """The exemption list's negative control, and the failure mode this file exists to avoid: a
    renamed test leaves a row that excuses nothing, and the ratchet above quietly loosens."""
    live = {(file, function) for file, function, _line, _klass in _residue()}
    stale = sorted(
        f"{file}::{function}" for file, function in _ARGUED if (file, function) not in live
    )

    assert not stale, (
        f"argued sites that no longer reach past the facade: {stale}. Delete the rows — an "
        "exemption for a site that has been converted is a hole nothing is watching."
    )
