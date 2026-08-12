"""``saitenka setup`` — the interactive install wizard.

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
from pathlib import Path

from saitenka.app import prompt

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


def _present(tool: str) -> bool:
    """Is ``tool`` usable? mpv/ffmpeg route through the SAME discovery launch/doctor use (``find_mpv``
    knows ``C:\\mpv``, mpv.net, the registry; ``find_tool`` knows the off-PATH bin dirs) — so setup
    doesn't try to reinstall an mpv that's present but simply not on PATH (the winget-crash loop).
    Other tools (uv) fall back to PATH."""
    from saitenka.mpvio.discover import find_mpv, find_tool

    if tool == "mpv":
        return find_mpv() is not None
    if tool in {"ffmpeg", "ffprobe"}:
        return find_tool(tool) is not None
    return shutil.which(tool) is not None


def inventory() -> dict[str, bool]:
    """Which required tools are present (✓/✗)."""
    return {t: _present(t) for t in REQUIRED_TOOLS}


def missing_tools(tools: list[str]) -> list[str]:
    """The subset of ``tools`` not present (resumability: only install what's missing)."""
    return [t for t in tools if not _present(t)]


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


def _darwin_plan(tools: list[str]) -> InstallPlan:
    if shutil.which("brew"):
        return InstallPlan("brew", [_manager_command("brew", t) for t in tools])
    return InstallPlan(
        None,
        hint="Homebrew not found — install it from https://brew.sh, then re-run `setup`.",
    )


def _windows_plan(tools: list[str]) -> InstallPlan:
    for mgr in _WINDOWS_MANAGERS:
        if shutil.which(mgr):
            return InstallPlan(mgr, [_manager_command(mgr, t) for t in tools])
    return InstallPlan(
        None,
        hint="No package manager found — install winget (ships with Windows 11), choco, or "
        "scoop, then re-run `setup`.",
    )


def _linux_hint(tools: list[str]) -> InstallPlan:
    # Print copy-paste hints, never auto-install.
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


def install_plan(tools: list[str]) -> InstallPlan:
    """Decide how to install ``tools`` on this OS (never auto-install on Linux)."""
    system = platform.system()
    if system == "Darwin":
        return _darwin_plan(tools)
    if system == "Windows":
        return _windows_plan(tools)
    return _linux_hint(tools)


def do_install(tools: list[str], *, dry_run: bool, confirm: Confirm) -> int:
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
    installed = 0
    for tool, cmd in zip(todo, plan.commands, strict=True):
        print("  $", " ".join(cmd))
        try:
            _run_cmd(cmd)
            installed += 1
        except subprocess.CalledProcessError as e:
            # A package manager exiting non-zero (already-installed, no applicable installer, declined
            # UAC, source agreement) MUST NOT crash setup — warn, keep going, let doctor flag what's
            # still missing. This was the "reinstall fucks stuff up" crash: winget nonzero → check=True.
            print(
                f"  {_FAIL} {tool} install failed (exit {e.returncode}) — skipping; {plan.hint or ''}".rstrip()
            )
            log.warning(
                "setup: %s install failed via %s (exit %s)", tool, plan.manager, e.returncode
            )
    return installed


def _resolve_mpv_input(raw: str) -> str | None:
    """A user-typed mpv path → a validated executable, or None. Strips the quotes Windows "Copy as
    path" adds; if a directory is given, looks for a known mpv binary inside. Pure — unit-tested."""
    from saitenka.mpvio.discover import _MPV_EXE_NAMES, _is_exe

    s = raw.strip().strip('"').strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if p.is_dir():
        p = next((p / n for n in (*_MPV_EXE_NAMES, "mpv", "mpvnet") if _is_exe(p / n)), p)
    return str(p) if _is_exe(p) else None


def ensure_mpv_path(
    confirm: Confirm,
) -> str | None:  # pragma: no cover — interactive I/O + config write
    """Last resort when auto-detection failed: ask for mpv's path and persist it to ``overlay.toml``
    ``mpv_path`` (``find_mpv`` checks that first, so it sticks). Non-interactive (plugin/attach spawns
    us with no console) → print a hint instead of hanging on ``input``."""
    from saitenka.mpvio.discover import find_mpv

    if find_mpv() is not None:
        return None
    if not sys.stdin.isatty():
        print(
            "  mpv not found — set `mpv_path` in overlay.toml (or install mpv), then re-run setup."
        )
        return None
    print("  mpv not found automatically.")
    for _ in range(3):
        raw = prompt.text("  Full path to the mpv / mpvnet executable (blank to skip):")
        if not raw:
            return None
        if resolved := _resolve_mpv_input(raw):
            from saitenka.app.init_wizard import write_config

            write_config({"mpv_path": resolved}, confirm=confirm)
            print(f"  {_OK} saved mpv_path = {resolved}")
            return resolved
        print(f"  {_FAIL} not an mpv executable: {raw}")
    return None


# --- step glue (mock points; the CLI subcommands do the real work) --------------------------


def _run_doctor():  # pragma: no cover — thin glue over the unit-tested doctor
    from saitenka.app.doctor import print_report, run_checks

    # summary=True collapses the ✓ wall so the wizard stays readable; every warning/failure still
    # prints in full. Returns the report so the caller can branch on the pass/fail outcome.
    report = run_checks()
    print_report(report, summary=True)
    return report


def _run_init(confirm: Confirm) -> None:  # pragma: no cover — thin glue over init_wizard
    from saitenka.app.init_wizard import (
        DEFAULT_CONFIG,
        _maybe_store_jimaku_key,
        dumps_toml,
        write_config,
    )
    from saitenka.mpvio.discover import find_mpv

    mpv = find_mpv()
    print(f"  mpv: {mpv or 'not found'}")
    proposal = dict(DEFAULT_CONFIG)
    print(dumps_toml(proposal))
    write_config(proposal, confirm=confirm)
    _maybe_store_jimaku_key()


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


def intersect_default(preferred: str, available: list[str]) -> str:
    """The default to propose for a choice: ``preferred`` only when it's actually in Anki's
    ``available`` list, else the first available name (or ``''`` when nothing is). Stops the wizard from
    defaulting to a note type/deck that doesn't exist on this machine (e.g. the hardcoded ``Lapis``)."""
    if preferred in available:
        return preferred
    return available[0] if available else ""


def default_known_deck(decks: list[str], sizes: dict[str, int], prefer: str = "") -> str:
    """A sensible default 'words I already know' deck: prefer Saitenka's own ``Saitenka::Known`` (or any
    ``…::Known`` leaf) if present — that's the config convention — then ``prefer`` (the mining deck, so a
    single-deck user reuses it for coloring), else the largest deck that isn't empty or the built-in
    ``Default``. ``''`` when nothing qualifies, so the caller offers skip."""
    if "Saitenka::Known" in decks:
        return "Saitenka::Known"
    known_leaf = next((d for d in rank_decks(decks, sizes) if d.rsplit("::", 1)[-1] == "Known"), "")
    if known_leaf:
        return known_leaf
    if prefer and prefer in decks:
        return prefer
    for d in rank_decks(decks, sizes):
        if sizes.get(d, 0) > 0 and d.lower() != "default":
            return d
    return ""


def _offer_anki(confirm: Confirm) -> None:  # pragma: no cover — interactive, needs live AnkiConnect
    """Configure the Anki KNOWN-words deck (drives coloring) + the MINING deck/model over AnkiConnect.
    Skips cleanly when Anki isn't reachable — the config's ``[known]``/``[mine]`` can be set later."""
    import json

    from saitenka.app.anki import Anki, AnkiError, anki_reachable
    from saitenka.app.config import load_config
    from saitenka.app.init_wizard import write_config

    if not anki_reachable():
        print(
            "  Anki/AnkiConnect not reachable — skipping (start Anki + AnkiConnect, then re-run setup)"
        )
        return
    if not confirm("\nConfigure your Anki known-words deck + mining deck now?"):
        return
    anki = Anki()
    try:
        with prompt.spinner("  querying Anki (decks / note types)…"):
            decks = sorted(anki._call("deckNames") or [])
            models = sorted(anki._call("modelNames") or [])
    except (AnkiError, json.JSONDecodeError):
        print("  couldn't query AnkiConnect (decks/models) — skipping")
        return

    with prompt.spinner("  reading deck sizes…"):
        sizes = _deck_sizes(anki)
    ranked = rank_decks(decks, sizes)  # biggest first → the deck picker's completion order

    cfg = load_config()
    cur = dict(cfg.get("mine") or {})
    # Ask the mining deck/model FIRST: most users have one mining deck, so it's the natural default for
    # the known-words deck below (a single-deck user reuses it for coloring instead of picking twice).
    # Deck pickers are type-to-filter but accept a NEW name — the mining deck is created on first mine,
    # so it needn't already exist; the note type must (Anki won't create it).
    mine_deck = prompt.autocomplete(
        "  Mining deck (type to filter; a new name is fine)?",
        ranked,
        default=cur.get("deck", "Saitenka::Mining"),
    )
    model_default = intersect_default(cur.get("model", "Lapis"), models)
    mine_model = prompt.select("  Mining note type?", models, default=model_default)
    mine_kind = _prompt_card_kind(cur, mine_model)
    mine_fields = _prompt_mine_fields(anki, mine_model, cur)
    default_known = default_known_deck(decks, sizes, prefer=mine_deck)
    # A yes/no gate (via the wizard's confirm seam, so --yes / non-tty resolve it) makes "no known deck"
    # unambiguously reachable — an autocomplete pre-filled with the default can't cleanly express skip.
    known_deck = ""
    if confirm("  Add a deck of words you already KNOW (drives subtitle coloring)?"):
        known_deck = prompt.autocomplete("  Known-words deck?", ranked, default=default_known)
    known_field = ""
    if known_deck:
        fields = _deck_fields(anki, known_deck)
        default_field = fields[0] if fields else "Expression"
        known_field = prompt.select("    Field with the word?", fields, default=default_field)

    frag = anki_config_fragment(
        known_deck,
        known_field,
        mine_deck,
        mine_model,
        existing_mine=cur,
        card_kind=mine_kind,
        fields=mine_fields,
    )
    write_config({**cfg, **frag}, confirm=lambda _p: True)
    print("  Anki config written.")


def _prompt_card_kind(current: dict, model: str = "") -> str:
    """Which card template the mine marks. word-and-sentence is the Lapis/Kiku default; 'sentence' is
    Saitenka's historical marker; 'audio'/'click'/'none' cover the other Lapis-family templates. A
    non-preset note type has no ``IsXxxCard`` flag field, so writing one would make the add fail —
    default it to 'none'."""
    from saitenka.app.anki import CARD_KINDS, PRESETS

    default = current.get("card_kind") or ("word-and-sentence" if model in PRESETS else "none")
    return prompt.select("  Card kind?", list(CARD_KINDS), default=default)


# saitenka entities offered for mapping, in card order. ``expression`` is first and mandatory — it must
# fill the note type's first field or Anki rejects the note as empty; the rest are skippable.
_MAP_ENTITIES = ("expression", "reading", "sentence", "glossary", "picture", "audio")


def _default_field_for(entity: str, real: list[str], existing: dict, *, first: bool) -> str:
    """Pre-selected field for an entity: a still-valid prior mapping → a name match (Reading→Reading,
    audio→SentenceAudio) → the first field for the mandatory ``expression`` (never empty) → ``""`` for a
    skippable one with no guess. Pure — unit-tested."""
    prior = existing.get(entity)
    if prior and prior in real:
        return prior
    el = entity.lower()
    for name in real:
        if (nl := name.lower()) == el or el in nl:
            return name
    return real[0] if first and real else ""


def _prompt_mine_fields(anki, model: str, current: dict) -> dict | None:
    """Map each saitenka entity to one of a NON-preset note type's real fields, so the user never
    hand-writes ``[mine].fields`` (the mis-map that silently blocks every add). ``None`` when a preset
    supplies its own map, or the model's fields can't be read (leave any existing map untouched)."""
    from saitenka.app.anki import PRESETS

    if model in PRESETS:
        return None
    try:
        real = list(anki._call("modelFieldNames", modelName=model) or [])
    except Exception:  # pragma: no cover — a read hiccup just skips guided mapping
        log.debug("reading %r fields failed", model, exc_info=True)
        return None
    if not real:
        return None
    raw_fields = current.get("fields")
    existing = raw_fields if isinstance(raw_fields, dict) else {}
    print(f"    Map each mined value to a field on {model!r} (Enter accepts the guess):")
    mapping: dict = {}
    skip = "(skip)"
    for entity in _MAP_ENTITIES:  # pragma: no cover — interactive prompt loop
        mandatory = entity == "expression"
        default = _default_field_for(entity, real, existing, first=mandatory)
        picked = prompt.select(
            f"      {entity}?", real if mandatory else [skip, *real], default=default or skip
        )
        if picked and picked != skip:
            mapping[entity] = picked
    return mapping or None


def anki_config_fragment(
    known_deck: str,
    known_field: str,
    mine_deck: str,
    mine_model: str,
    existing_mine: dict | None = None,
    card_kind: str = "word-and-sentence",
    fields: dict | None = None,
) -> dict:
    """Build the config fragment from the wizard's Anki choices: ``[known]`` deck→[field] (drives
    coloring; empty deck → omitted) + ``[mine]`` deck/model/card_kind (merged over any existing [mine]
    keys, so a custom key/all_key survives). ``fields`` (the guided logical→real map) replaces any prior
    ``fields`` when given; ``None`` leaves an existing map untouched (presets / skipped). Pure — unit-tested."""
    mine = {**(existing_mine or {}), "deck": mine_deck, "model": mine_model, "card_kind": card_kind}
    if fields:
        mine["fields"] = fields
    frag: dict = {"mine": mine}
    if known_deck:
        frag["known"] = {known_deck: [known_field or "Expression"]}
    return frag


def _offer_import(confirm: Confirm) -> None:  # pragma: no cover — thin glue over yomitan_import
    if not confirm("Import your Yomitan settings now?"):
        return
    from saitenka.app.yomitan_import import YomitanImportError, run_import

    try:
        run_import(None, None, confirm)
    except YomitanImportError as e:
        print(f"  import skipped: {e}")


def _offer_plugin(confirm: Confirm) -> None:  # pragma: no cover — thin glue over plugin
    if not confirm("Install the mpv plugin (auto-launch overlay on any mpv start)?"):
        return
    from saitenka.app.plugin import install_plugin

    dest = install_plugin()
    print(f"  installed {dest}")


def run_setup(*, yes: bool, dry_run: bool) -> int:
    """Full wizard: inventory → install → doctor → init → import → plugin."""
    confirm: Confirm = (lambda _p: True) if yes else prompt.confirm
    print("saitenka setup")

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
        if not dry_run:
            ensure_mpv_path(confirm)  # still no mpv? ask for its path + persist to mpv_path

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
        print(f"\nSetup complete {_OK} - run `saitenka <video>`, or just open a video in mpv.")
    else:
        print(
            "\nSetup finished with problems (see [x]/! above). Fix them, re-run `saitenka doctor`,"
            " or send `saitenka report` if you need help."
        )
    return 0
