"""The shared prompt seam (#194): tty gating + the numbered/input fallback (the degrade-don't-break
contract). The questionary TUI itself is exercised live (no pseudo-terminal in unit tests); here we pin
that a non-tty never prompts, the fallback reproduces today's behaviour, and a TUI failure degrades to it.
"""

import builtins
import sys

from overlay.app import prompt


def _isatty(monkeypatch, *, stdin: bool, stdout: bool) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout)


def _fallback(monkeypatch) -> None:
    """A real tty on both ends but the questionary TUI disabled → calls take the ``input()`` fallback
    deterministically, no pseudo-terminal needed."""
    _isatty(monkeypatch, stdin=True, stdout=True)
    monkeypatch.setenv("SAITENKA_NO_TUI", "1")


def _fancy(monkeypatch) -> None:
    """A tty with the TUI enabled — so the questionary branch runs (its calls get monkeypatched)."""
    _isatty(monkeypatch, stdin=True, stdout=True)
    monkeypatch.delenv("SAITENKA_NO_TUI", raising=False)


def _answers(monkeypatch, *replies: str) -> None:
    """Feed successive ``input()`` calls, ignoring the prompt string."""
    it = iter(replies)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(it))


# --- non-tty: never prompt, return the default (a console-less plugin run must not block) ---


def test_confirm_non_tty_returns_default(monkeypatch):
    _isatty(monkeypatch, stdin=False, stdout=False)
    assert prompt.confirm("ok?", default=True) is True
    assert prompt.confirm("ok?") is False  # default is decline, matching [y/N]


def test_select_non_tty_returns_default(monkeypatch):
    _isatty(monkeypatch, stdin=False, stdout=False)
    assert prompt.select("pick", ["a", "b"], default="b") == "b"


def test_autocomplete_non_tty_returns_default(monkeypatch):
    _isatty(monkeypatch, stdin=False, stdout=False)
    assert prompt.autocomplete("deck", ["a"], default="New::Deck") == "New::Deck"


def test_text_non_tty_returns_default(monkeypatch):
    _isatty(monkeypatch, stdin=False, stdout=False)
    assert prompt.text("path", default="/x") == "/x"


def test_select_empty_choices_returns_default(monkeypatch):
    _isatty(monkeypatch, stdin=False, stdout=False)
    assert prompt.select("pick", [], default="d") == "d"


# --- fallback (tty, TUI off): today's numbered / [y/N] input behaviour, unchanged ---


def test_confirm_fallback_yes(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "y")
    assert prompt.confirm("ok?") is True


def test_confirm_fallback_blank_is_no(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "")
    assert prompt.confirm("ok?") is False


def test_select_fallback_accepts_1_based_number(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "2")
    assert prompt.select("pick", ["a", "b", "c"], default="a") == "b"


def test_select_fallback_accepts_typed_name(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "Custom::Deck")
    assert prompt.select("pick", ["a"], default="a") == "Custom::Deck"


def test_select_fallback_blank_keeps_default(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "")
    assert prompt.select("pick", ["a", "b"], default="b") == "b"


def test_autocomplete_fallback_allows_a_new_value(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "Brand::New")
    assert prompt.autocomplete("deck", ["a", "b"], default="a") == "Brand::New"


def test_numbered_fallback_caps_at_12_and_notes_the_remainder(monkeypatch, capsys):
    _fallback(monkeypatch)
    _answers(monkeypatch, "")
    prompt.select("pick", [f"d{i}" for i in range(20)], default="d0")
    out = capsys.readouterr().out
    assert out.count(". d") == 12  # only the first 12 listed
    assert "+8 more" in out


def test_text_fallback_blank_keeps_default(monkeypatch):
    _fallback(monkeypatch)
    _answers(monkeypatch, "")
    assert prompt.text("path", default="/d") == "/d"


# --- the questionary branch: taken when fancy, and any TUI failure degrades to input() ---


def test_confirm_uses_questionary_when_fancy(monkeypatch):
    import questionary

    _fancy(monkeypatch)
    seen = {}

    class _Q:
        def ask(self):
            return True

    def _confirm(*_a, **_k):
        seen["hit"] = True
        return _Q()

    monkeypatch.setattr(questionary, "confirm", _confirm)
    assert prompt.confirm("ok?") is True
    assert seen.get("hit")


def test_confirm_falls_back_to_input_when_questionary_raises(monkeypatch):
    """A legacy console / broken terminfo makes questionary raise — degrade to the [y/N] prompt, don't crash."""
    import questionary

    _fancy(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("no terminal")

    monkeypatch.setattr(questionary, "confirm", _boom)
    _answers(monkeypatch, "y")
    assert prompt.confirm("ok?") is True


def test_select_cancel_returns_default(monkeypatch):
    """Esc / Ctrl-C → questionary .ask() returns None → we keep the default, never crash mid-prompt."""
    import questionary

    _fancy(monkeypatch)

    class _Cancelled:
        def ask(self):
            return None

    monkeypatch.setattr(questionary, "select", lambda *_a, **_k: _Cancelled())
    assert prompt.select("pick", ["a", "b"], default="b") == "b"


def test_autocomplete_returns_questionary_value_when_fancy(monkeypatch):
    import questionary

    _fancy(monkeypatch)

    class _Q:
        def ask(self):
            return "Chosen::Deck"

    monkeypatch.setattr(questionary, "autocomplete", lambda *_a, **_k: _Q())
    assert prompt.autocomplete("deck", ["a"], default="a") == "Chosen::Deck"


def test_spinner_non_tty_prints_message_and_yields(monkeypatch, capsys):
    """No terminal → the spinner prints its line once and runs the body (rich stays off the import path)."""
    _isatty(monkeypatch, stdin=False, stdout=False)
    ran = False
    with prompt.spinner("querying Anki…"):
        ran = True
    assert ran
    assert "querying Anki…" in capsys.readouterr().out
