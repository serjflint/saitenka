"""One place to ask the user something — the shared confirm / select / text seam.

Arrow-key selection + type-to-filter via ``questionary`` on a real terminal; degrades to the plain
numbered-list / ``[y/N]`` ``input()`` fallback when there's no tty (the mpv plugin spawns the wizard
console-less), when questionary can't drive the terminal (legacy Windows console → the ``except``
catches it), or when ``SAITENKA_NO_TUI=1`` forces it off. ``--yes`` is handled a layer up (the wizard's
``Confirm`` seam returns ``True`` before reaching here), so a non-interactive run never blocks.

questionary is lazy-imported inside the interactive branch ONLY: merely *constructing* a Question in a
non-tty makes prompt_toolkit print a "not a terminal" warning, so the fallback path must never touch it —
and it keeps prompt_toolkit off the render pipeline's import path (free-threading hygiene). A cancelled
prompt (Esc / Ctrl-C → questionary returns ``None``) resolves to the default, so the wizard never crashes
mid-prompt.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

log = logging.getLogger(__name__)


@contextlib.contextmanager
def spinner(message: str) -> Generator[None]:
    """A rich status spinner around a silent slow step (an AnkiConnect round-trip), on a tty only. No
    terminal → print the line once and yield (rich stays off the import path). rich is already a dep."""
    if not sys.stdout.isatty():
        print(message)
        yield
        return
    from rich.console import Console

    with Console().status(message):
        yield


def _fancy() -> bool:
    """Attempt the questionary TUI? Needs a real terminal on both ends and no explicit opt-out. Any
    *actual* TUI failure (legacy console, missing terminfo) is caught at the call site → plain fallback."""
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("SAITENKA_NO_TUI") != "1"


def confirm(message: str, *, default: bool = False) -> bool:
    """Yes/no. A non-tty stdin returns ``default`` (never blocks a console-less run); an interactive
    terminal gets the questionary confirm, falling back to a ``[y/N]`` ``input()``."""
    if not sys.stdin.isatty():
        return default
    if _fancy():
        try:
            import questionary

            answer = questionary.confirm(message, default=default, auto_enter=True).ask()
            return default if answer is None else bool(answer)
        except Exception:
            log.debug("questionary confirm failed; using input() fallback", exc_info=True)
    return input(f"{message} [y/N] ").strip().lower() in ("y", "yes")


def select(message: str, choices: list[str], *, default: str = "") -> str:
    """Pick one of ``choices`` (arrow-key + type-to-filter). Empty choices or a non-tty → ``default``.
    Interactive → questionary select; on failure, today's numbered list that also accepts a typed name."""
    if not choices or not sys.stdin.isatty():
        return default
    if _fancy():
        try:
            import questionary

            # default must be one of the choices or questionary raises — else pass None (no preselect).
            answer = questionary.select(
                message,
                choices=choices,
                default=default if default in choices else None,
                use_search_filter=True,
                use_jk_keys=False,
            ).ask()
            return default if answer is None else answer
        except Exception:
            log.debug("questionary select failed; using numbered fallback", exc_info=True)
    return _numbered_fallback(message, choices, default)


def autocomplete(message: str, choices: list[str], *, default: str = "") -> str:
    """Free text with type-to-complete over ``choices`` — a value NOT in the list is allowed (e.g. a
    mining deck created on first mine). Non-tty → ``default``. Interactive → questionary autocomplete,
    falling back to the numbered list (which also accepts a typed name)."""
    if not sys.stdin.isatty():
        return default
    if _fancy():
        try:
            import questionary

            answer = questionary.autocomplete(message, choices=choices, default=default).ask()
            return default if answer is None else (answer or default)
        except Exception:
            log.debug("questionary autocomplete failed; using numbered fallback", exc_info=True)
    return _numbered_fallback(message, choices, default)


def _numbered_fallback(message: str, choices: list[str], default: str) -> str:
    """Today's picker: the first ~12 choices printed numbered, answer accepted as a NAME or a 1-based
    number. Capped so a 50-deck list doesn't scroll — any name (listed or not) is still accepted."""
    shown = choices[:12]
    for i, choice in enumerate(shown, 1):
        print(f"    {i:2}. {choice}")
    if len(choices) > len(shown):
        print(f"    … +{len(choices) - len(shown)} more — type a name to match one not listed")
    answer = input(f"{message} [{default or '?'}] ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return choices[int(answer) - 1]
    return answer or default


def text(message: str, *, default: str = "") -> str:
    """Free text with a default. Non-tty → ``default``; interactive → questionary text, falling back to
    ``input()``. A blank answer keeps the default."""
    if not sys.stdin.isatty():
        return default
    if _fancy():
        try:
            import questionary

            answer = questionary.text(message, default=default).ask()
            return default if answer is None else (answer or default)
        except Exception:
            log.debug("questionary text failed; using input() fallback", exc_info=True)
    return input(f"{message} ").strip() or default
