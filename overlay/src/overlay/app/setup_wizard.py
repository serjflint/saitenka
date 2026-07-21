"""``saitenka-overlay setup`` — the interactive install wizard.

All installer logic lives HERE, in Python — the shell stubs only bootstrap uv and hand off. The
wizard composes the setup pieces in order: inventory (✓/✗) → install mpv+ffmpeg → ``doctor`` →
``init`` → offer ``import-settings`` → offer ``install-plugin``. Each step is confirm-first; ``--yes``
skips prompts, ``--dry-run`` runs nothing. It is resumable — a re-run skips already-satisfied steps
(only missing tools are installed). Non-destructive rules apply throughout (config writes use the
confirm+backup sink so existing files are never silently overwritten).

Fully pytest-tested with mocked package managers and fake home dirs — which is exactly WHY the logic
is not in shell.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

Confirm = Callable[[str], bool]

REQUIRED_TOOLS = ("uv", "mpv", "ffmpeg")
INSTALL_TOOLS = ("mpv", "ffmpeg")  # uv is bootstrapped by the shell stub, not here

# Windows package managers in preference order (winget ships with Windows 11).
_WINDOWS_MANAGERS = ("winget", "choco", "scoop")


@dataclass(frozen=True)
class InstallPlan:
    manager: str | None  # the package manager to use, or None (unavailable / Linux)
    commands: list[list[str]] = field(default_factory=list)
    hint: str = ""  # printed when we can't auto-install


def _run_cmd(cmd: list[str]) -> None:  # pragma: no cover — real subprocess (mocked in tests)
    subprocess.run(cmd, check=True)


def inventory() -> dict[str, bool]:
    """Which required tools are present (✓/✗)."""
    return {t: shutil.which(t) is not None for t in REQUIRED_TOOLS}


def missing_tools(tools: list[str]) -> list[str]:
    """The subset of ``tools`` not on PATH (resumability: only install what's missing)."""
    return [t for t in tools if shutil.which(t) is None]


def _manager_command(manager: str, tool: str) -> list[str]:
    if manager == "brew":
        return ["brew", "install", tool]
    if manager == "winget":
        return ["winget", "install", "-e", "--id", _WINGET_IDS.get(tool, tool)]
    if manager == "choco":
        return ["choco", "install", "-y", tool]
    if manager == "scoop":
        return ["scoop", "install", tool]
    raise ValueError(f"unknown manager: {manager}")  # pragma: no cover


_WINGET_IDS = {"mpv": "mpv.net", "ffmpeg": "Gyan.FFmpeg"}


def install_plan(tools: list[str]) -> InstallPlan:
    """Decide how to install ``tools`` on this OS (never auto-install on Linux)."""
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return InstallPlan("brew", [_manager_command("brew", t) for t in tools])
        return InstallPlan(
            None,
            hint="Homebrew not found — install it from https://brew.sh, then re-run `setup`.",
        )
    if system == "Windows":
        for mgr in _WINDOWS_MANAGERS:
            if shutil.which(mgr):
                return InstallPlan(mgr, [_manager_command(mgr, t) for t in tools])
        return InstallPlan(
            None,
            hint="No package manager found — install winget (ships with Windows 11), choco, or "
            "scoop, then re-run `setup`.",
        )
    # Linux and everything else: print copy-paste hints, never auto-install.
    joined = " ".join(tools)
    return InstallPlan(
        None,
        hint=(
            f"On Linux install {joined} with your distro's package manager, e.g.\n"
            f"  Debian/Ubuntu:  sudo apt install {joined}\n"
            f"  Fedora:         sudo dnf install {joined}\n"
            f"  Arch:           sudo pacman -S {joined}"
        ),
    )


def do_install(tools: list[str], dry_run: bool, confirm: Confirm) -> int:
    """Install the MISSING subset of ``tools``. Returns the count that was (or would be) installed."""
    todo = missing_tools(tools)
    if not todo:
        print("  ✓ all toolchain deps already present")
        return 0
    plan = install_plan(todo)
    if plan.manager is None:
        print(plan.hint)
        return 0
    print(f"  will install via {plan.manager}: {', '.join(todo)}")
    if dry_run:
        for cmd in plan.commands:
            print("  DRY:", " ".join(cmd))
        return len(todo)
    if not confirm(f"Install {', '.join(todo)} with {plan.manager}?"):
        print("  skipped install")
        return 0
    for cmd in plan.commands:
        print("  $", " ".join(cmd))
        _run_cmd(cmd)
    return len(todo)


# --- step glue (mock points; the CLI subcommands do the real work) --------------------------


def _run_doctor() -> None:  # pragma: no cover — thin glue over the unit-tested doctor
    from overlay.app.doctor import print_report, run_checks

    print_report(run_checks())


def _run_init(confirm: Confirm) -> None:  # pragma: no cover — thin glue over init_wizard
    from overlay.app.init_wizard import _maybe_store_jimaku_key, dumps_toml, write_config
    from overlay.mpvio.discover import find_mpv

    mpv = find_mpv()
    print(f"  mpv: {mpv or 'not found'}")
    proposal = {"slang": "ja,jpn,jp", "tip_height": 0.6}
    print(dumps_toml(proposal))
    write_config(proposal, confirm=confirm)
    _maybe_store_jimaku_key()


def _prompt(msg: str, options: list[str]) -> str:  # pragma: no cover — interactive I/O
    """Ask for a value; accept a NAME or its 1-based number from ``options``. Blank / non-tty → ''."""
    if not sys.stdin.isatty():
        return ""
    ans = input(f"{msg} ").strip()
    if ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1]
    return ans


def _deck_fields(anki, deck: str) -> list[str]:  # pragma: no cover — needs a live AnkiConnect
    """Field names of the first note in ``deck`` — so we can pick the word/expression field."""
    try:
        ids = anki.find_notes(f'deck:"{deck}"')
        if ids and (info := anki.notes_info(ids[:1])):
            return list(info[0].get("fields", {}).keys())
    except Exception:
        log.debug("reading deck fields failed", exc_info=True)
    return []


def _offer_anki(confirm: Confirm) -> None:  # pragma: no cover — interactive, needs live AnkiConnect
    """Configure the Anki KNOWN-words deck (drives coloring) + the MINING deck/model over AnkiConnect.
    Skips cleanly when Anki isn't reachable — the config's ``[known]``/``[mine]`` can be set later."""
    from overlay.app.anki import Anki, anki_reachable
    from overlay.app.config import load_config
    from overlay.app.init_wizard import write_config

    if not anki_reachable():
        print(
            "  Anki/AnkiConnect not reachable — skipping (start Anki + AnkiConnect, then re-run setup)"
        )
        return
    if not confirm("\nConfigure your Anki known-words deck + mining deck now?"):
        return
    anki = Anki()
    try:
        decks = sorted(anki._call("deckNames") or [])
        models = sorted(anki._call("modelNames") or [])
    except Exception:
        print("  couldn't query AnkiConnect (decks/models) — skipping")
        return

    print("\n  Decks:")
    for i, d in enumerate(decks, 1):
        print(f"    {i:2}. {d}")

    cfg = load_config()
    new: dict = {}
    known_deck = _prompt(
        "  Deck of words you already KNOW (name/number → coloring; blank to skip)?", decks
    )
    if known_deck:
        fields = _deck_fields(anki, known_deck)
        default_field = fields[0] if fields else "Expression"
        field = (
            _prompt(f"    Field with the word {fields or '(none read)'} [{default_field}]?", fields)
            or default_field
        )
        new["known"] = {known_deck: [field]}

    mine = dict(cfg.get("mine") or {})
    mine["deck"] = _prompt(
        f"  Mining deck [{mine.get('deck', 'Saitenka::Mining')}]?", decks
    ) or mine.get("deck", "Saitenka::Mining")
    mine["model"] = _prompt(
        f"  Mining note type [{mine.get('model', 'Lapis')}]?", models
    ) or mine.get("model", "Lapis")
    new["mine"] = mine

    write_config({**cfg, **new}, confirm=lambda _p: True)
    print("  Anki config written.")


def _offer_import(confirm: Confirm) -> None:  # pragma: no cover — thin glue over yomitan_import
    if not confirm("Import your Yomitan settings now?"):
        return
    from overlay.app.yomitan_import import YomitanImportError, run_import

    try:
        run_import(None, None, confirm)
    except YomitanImportError as e:
        print(f"  import skipped: {e}")


def _offer_copy_dicts(confirm: Confirm) -> None:  # pragma: no cover — thin glue over relocate
    """Offer to copy dicts out of TCC-protected folders so a GUI-launched plugin mpv doesn't prompt
    for Documents access. No-op (silent) when nothing is under a protected folder."""
    from overlay.app.config import dicts_data_dir, is_protected, load_config

    cfg = load_config()
    protected = [
        p for kind in ("dicts", "freq", "pitch") for p in (cfg.get(kind) or []) if is_protected(p)
    ]
    if not protected:
        return
    if not confirm(
        f"{len(protected)} dict(s) are under a protected folder (Documents/…), which makes GUI mpv "
        f"prompt for access. Copy them to {dicts_data_dir()} and repoint the config?"
    ):
        return
    from overlay.app.relocate import relocate_dicts

    mappings = relocate_dicts()
    print(f"  copied {len(mappings)} dict(s) → {dicts_data_dir()}, repointed the config")


def _offer_plugin(confirm: Confirm) -> None:  # pragma: no cover — thin glue over plugin
    if not confirm("Install the mpv plugin (auto-launch overlay on any mpv start)?"):
        return
    from overlay.app.plugin import install_plugin

    dest = install_plugin()
    print(f"  installed {dest}")


def _ask(prompt: str) -> bool:  # pragma: no cover — interactive I/O
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def run_setup(yes: bool, dry_run: bool) -> int:
    """Full wizard: inventory → install → doctor → init → import → copy-dicts → plugin."""
    confirm: Confirm = (lambda _p: True) if yes else _ask
    print("saitenka-overlay setup\n")

    print("Inventory:")
    for tool, present in inventory().items():
        print(f"  {'✓' if present else '✗'} {tool}")

    print("\nToolchain:")
    do_install(list(INSTALL_TOOLS), dry_run=dry_run, confirm=confirm)

    print("\nDoctor:")
    _run_doctor()

    print("\nConfig:")
    _run_init(confirm)

    print("\nAnki:")
    _offer_anki(confirm)
    _offer_import(confirm)
    _offer_copy_dicts(confirm)
    _offer_plugin(confirm)

    # Final self-verify: re-run the full health check now that config + plugin exist, so the user sees
    # the real end state (the first doctor ran before any of that) and a clear pass/fail summary.
    print("\nFinal check:")
    from overlay.app.doctor import print_report, run_checks

    report = run_checks()
    print_report(report)
    if report.exit_code == 0:
        print("\nSetup complete ✅ — run `saitenka-overlay <video>`, or just open a video in mpv.")
    else:
        print(
            "\nSetup finished with problems (see ✗ above). Fix them, re-run `saitenka-overlay doctor`,"
            " or send `saitenka-overlay report` if you need help."
        )
    return 0
