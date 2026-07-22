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

# Plain-ASCII status markers on Windows (the classic console mangles ✓/✗ and forcing UTF-8 breaks typing).
_WIN = sys.platform == "win32"
_OK = "[ok]" if _WIN else "✓"
_FAIL = "[x]" if _WIN else "✗"

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
        print(f"  {_OK} all toolchain deps already present")
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


def _run_doctor():  # pragma: no cover — thin glue over the unit-tested doctor
    from overlay.app.doctor import print_report, run_checks

    # summary=True collapses the ✓ wall so the wizard stays readable; every warning/failure still
    # prints in full. Returns the report so the caller can branch on the pass/fail outcome.
    report = run_checks()
    print_report(report, summary=True)
    return report


def _run_init(confirm: Confirm) -> None:  # pragma: no cover — thin glue over init_wizard
    from overlay.app.init_wizard import (
        DEFAULT_CONFIG,
        _maybe_store_jimaku_key,
        dumps_toml,
        write_config,
    )
    from overlay.mpvio.discover import find_mpv

    mpv = find_mpv()
    print(f"  mpv: {mpv or 'not found'}")
    proposal = dict(DEFAULT_CONFIG)
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


def _deck_sizes(anki) -> dict[str, int]:  # pragma: no cover — needs a live AnkiConnect
    """Deck name → total card count, in ONE ``getDeckStats`` call, for ranking + a sensible default."""
    try:
        ids = anki._call("deckNamesAndIds") or {}
        stats = anki._call("getDeckStats", decks=list(ids.keys())) or {}
        return {s.get("name", ""): int(s.get("total_in_deck", 0)) for s in stats.values()}
    except Exception:
        log.debug("reading deck stats failed", exc_info=True)
        return {}


def rank_decks(decks: list[str], sizes: dict[str, int]) -> list[str]:
    """Decks biggest-first (ties alphabetical) — the most likely known-word decks float to the top so
    a 50-deck collection doesn't need scrolling."""
    return sorted(decks, key=lambda d: (-sizes.get(d, 0), d))


def default_known_deck(decks: list[str], sizes: dict[str, int]) -> str:
    """A sensible default 'words I already know' deck: prefer Saitenka's own ``Saitenka::Known`` (or any
    ``…::Known`` leaf) if present — that's the config convention — else the largest deck that isn't
    empty or the built-in ``Default``. ``''`` when nothing qualifies, so the caller offers skip."""
    if "Saitenka::Known" in decks:
        return "Saitenka::Known"
    known_leaf = next((d for d in rank_decks(decks, sizes) if d.rsplit("::", 1)[-1] == "Known"), "")
    if known_leaf:
        return known_leaf
    for d in rank_decks(decks, sizes):
        if sizes.get(d, 0) > 0 and d.lower() != "default":
            return d
    return ""


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

    sizes = _deck_sizes(anki)
    ranked = rank_decks(decks, sizes)
    default_known = default_known_deck(decks, sizes)
    top = ranked[:12]  # don't dump 50+ decks; show the biggest, accept any typed name below
    print("\n  Decks (largest first):")
    for i, d in enumerate(top, 1):
        n = sizes.get(d, 0)
        print(f"    {i:2}. {d}" + (f"  ({n} cards)" if n else ""))
    if len(ranked) > len(top):
        print(f"    … +{len(ranked) - len(top)} more — type a deck name to match one not listed")

    cfg = load_config()
    cur = dict(cfg.get("mine") or {})
    raw_known = _prompt(
        f"  Deck of words you already KNOW → coloring [{default_known or 'none'}]; 'n' to skip?",
        top,
    )
    # 'n'/skip → no known deck; blank → the default (Saitenka::Known or largest); else the typed name.
    skip = raw_known.lower() in ("n", "no", "skip", "-")
    known_deck = "" if skip else (raw_known or default_known)
    known_field = ""
    if known_deck:
        fields = _deck_fields(anki, known_deck)
        default_field = fields[0] if fields else "Expression"
        known_field = (
            _prompt(f"    Field with the word {fields or '(none read)'} [{default_field}]?", fields)
            or default_field
        )
    mine_deck = _prompt(
        f"  Mining deck [{cur.get('deck', 'Saitenka::Mining')}]?", decks
    ) or cur.get("deck", "Saitenka::Mining")
    mine_model = _prompt(f"  Mining note type [{cur.get('model', 'Lapis')}]?", models) or cur.get(
        "model", "Lapis"
    )

    frag = anki_config_fragment(known_deck, known_field, mine_deck, mine_model, existing_mine=cur)
    write_config({**cfg, **frag}, confirm=lambda _p: True)
    print("  Anki config written.")


def anki_config_fragment(
    known_deck: str,
    known_field: str,
    mine_deck: str,
    mine_model: str,
    existing_mine: dict | None = None,
) -> dict:
    """Build the config fragment from the wizard's Anki choices: ``[known]`` deck→[field] (drives
    coloring; empty deck → omitted) + ``[mine]`` deck/model (merged over any existing [mine] keys, so
    a custom key/all_key survives). Pure — unit-tested."""
    frag: dict = {"mine": {**(existing_mine or {}), "deck": mine_deck, "model": mine_model}}
    if known_deck:
        frag["known"] = {known_deck: [known_field or "Expression"]}
    return frag


def _offer_import(confirm: Confirm) -> None:  # pragma: no cover — thin glue over yomitan_import
    if not confirm("Import your Yomitan settings now?"):
        return
    from overlay.app.yomitan_import import YomitanImportError, run_import

    try:
        run_import(None, None, confirm)
    except YomitanImportError as e:
        print(f"  import skipped: {e}")


def _offer_plugin(confirm: Confirm) -> None:  # pragma: no cover — thin glue over plugin
    if not confirm("Install the mpv plugin (auto-launch overlay on any mpv start)?"):
        return
    from overlay.app.plugin import install_plugin

    dest = install_plugin()
    print(f"  installed {dest}")


def _ask(prompt: str) -> bool:  # pragma: no cover — interactive I/O
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def run_setup(yes: bool, dry_run: bool) -> int:
    """Full wizard: inventory → install → doctor → init → import → plugin."""
    confirm: Confirm = (lambda _p: True) if yes else _ask
    print("saitenka-overlay setup")

    # Only surface the tooling inventory when something actually needs installing. When everything's
    # present (the common re-run) the installer's own Discovery already listed these and the Doctor
    # below re-confirms mpv/ffmpeg — so an itemised inventory here is pure duplication.
    inv = inventory()
    if not all(inv.values()):
        print("\nInventory:")
        for tool, present in inv.items():
            print(f"  {_OK if present else _FAIL} {tool}")
        print("\nToolchain:")
        do_install(list(INSTALL_TOOLS), dry_run=dry_run, confirm=confirm)

    print("\nConfig:")
    _run_init(confirm)

    print("\nAnki:")
    _offer_anki(confirm)
    _offer_import(confirm)
    _offer_plugin(confirm)

    # One health check, AFTER config/import/plugin — it verifies the real end state (there's no point
    # running doctor a second time before any of that; a pre-config run just duplicates this one).
    print("\nDoctor:")
    report = _run_doctor()
    if report.exit_code == 0:
        print(
            f"\nSetup complete {_OK} - run `saitenka-overlay <video>`, or just open a video in mpv."
        )
    else:
        print(
            "\nSetup finished with problems (see [x]/! above). Fix them, re-run `saitenka-overlay doctor`,"
            " or send `saitenka-overlay report` if you need help."
        )
    return 0
