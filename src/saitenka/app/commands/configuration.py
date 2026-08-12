from __future__ import annotations

import sys
from typing import Annotated

import cyclopts

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from saitenka.app.cli_run import _resolve_names as _resolve_names  # noqa: PLC0414  # re-export
from saitenka.app.cli_run import (
    jimaku_should_fetch as jimaku_should_fetch,  # noqa: PLC0414  # re-export
)


def init() -> int:  # pragma: no cover — interactive wizard, exercised live
    """Write a starter config (the config-file primitive `setup` builds on). Prefer `setup`/`install`."""
    from saitenka.app.init_wizard import run_init

    return run_init()


def config() -> (
    int
):  # pragma: no cover — interactive editor; the schema/coercion core is unit-tested
    """Interactively edit ``overlay.toml``: pick a section → an option → a typed field, comment-preserving.

    Offers the current value (or the built-in default) and round-trips through tomlkit, so every other
    key + comment survives. On a non-tty it writes nothing (the prompts return their defaults)."""
    from saitenka.app.config_editor import run_editor

    return run_editor()


def set_jimaku_key(
    key: Annotated[
        str | None, cyclopts.Parameter(help="the key (omit to be prompted with hidden input)")
    ] = None,
    *,
    file: Annotated[
        bool,
        cyclopts.Parameter(
            help="store in the owner-only file, skipping the OS keyring (and persist that opt-out) — "
            "for Windows AV that flags the first Credential Locker read"
        ),
    ] = False,
    verify: Annotated[
        bool, cyclopts.Parameter(help="test the key against jimaku.cc right after saving")
    ] = True,
) -> int:  # pragma: no cover — interactive/secret I/O; store/verify helpers are unit-tested
    """Store your jimaku.cc API key where a plugin-mode (GUI-launched) mpv can read it.

    Uses the OS keyring when available, else an owner-only file beside overlay.toml (force the file with
    ``--file``). Either beats a shell env var, which a GUI-launched mpv can't see. Get a free key at
    https://jimaku.cc/account (API docs: https://jimaku.cc/api/docs).

    Windows paste tip: the hidden prompt does NOT accept Ctrl+V (it captures one control char), so a
    pasted key can silently truncate to a single character. Right-click to paste at the prompt, or pass
    the key as an argument on the normal command line where Ctrl+V works: ``set-jimaku-key <key>``.
    """
    import getpass

    from saitenka.app.init_wizard import store_jimaku_key
    from saitenka.app.jimaku import key_paste_warning, prompt_for_key

    if (
        key is None
    ):  # interactive: hidden prompt with a truncated-paste guard (the Windows Ctrl+V trap)
        k = prompt_for_key(getpass.getpass)
    else:  # key passed as an argument (paste-safe on the normal line) — still sanity-check its length
        k = key.strip()
        warn = key_paste_warning(k)
        if warn:
            print(warn, file=sys.stderr)
    if not k:
        print("no key entered", file=sys.stderr)
        return 2
    method, backup = store_jimaku_key(k, prefer_file=file)
    if method == "keyring":
        print("stored in the OS secret store (Keychain / Credential Locker / Secret Service)")
    else:
        from saitenka.app.jimaku import key_file_path

        print(f"stored in {key_file_path()} (plaintext, owner-only)")
        if backup:
            print(f"backed up existing config → {backup}")
    return _verify_saved_jimaku_key(k) if verify else 0


def _verify_saved_jimaku_key(
    k: str,
) -> int:  # pragma: no cover — thin wrapper; verify_key is tested
    """Best-effort post-save probe. Catches the wrong-but-full-length key the length guard can't (a
    typo / revoked / wrong-account key that fails silently mid-video otherwise). A definite rejection
    is loud + non-zero; a network hiccup never fails a correctly-saved key."""
    from saitenka.app.jimaku import verify_key

    status, msg = verify_key(k)
    if status == "ok":
        print(f"verified: {msg}")
        return 0
    if status == "bad":
        print(f"WARNING: jimaku REJECTED the saved key — {msg}", file=sys.stderr)
        print(
            "re-copy the full key from https://jimaku.cc/account (right-click to paste at the hidden "
            "prompt), then re-run set-jimaku-key.",
            file=sys.stderr,
        )
        return 3
    print(f"note: couldn't verify now ({msg}) — saved anyway.", file=sys.stderr)
    return 0


def jimaku_check(
    query: Annotated[str, cyclopts.Parameter(help="anime title to test-search")] = "Spy x Family",
) -> int:  # pragma: no cover — thin CLI wrapper; JimakuClient is tested
    """Diagnose jimaku without launching a video: resolve the key and run a test search, printing the
    exact outcome (key found? 200 OK / 401 bad key / 400 + server message / network error)."""
    from saitenka.app.jimaku import resolve_jimaku_key, verify_key

    key, src = resolve_jimaku_key()
    if not key:
        print("jimaku key: NOT configured — run `saitenka set-jimaku-key`", file=sys.stderr)
        return 1
    print(f"jimaku key: found (from {src}), {len(key)} chars")
    status, msg = verify_key(key, query)
    if status == "ok":
        print(f"search {query!r}: OK — {msg}")
        return 0
    print(f"search {query!r}: {msg}", file=sys.stderr)
    return 1


def register(app: cyclopts.App) -> None:
    app.command(init, show=False)
    app.command(config)
    app.command(set_jimaku_key, name="set-jimaku-key")
    app.command(jimaku_check, name="jimaku-check")
