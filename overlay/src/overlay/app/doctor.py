"""``saitenka doctor`` — read-only health check.

Mirrors the ✓/!/✗ inventory style of ``install/doctor-*.sh`` and the SubMiner doctors, but for the
overlay's own runtime: mpv ≥ 0.37 (overlay-add BGRA), ffmpeg + aac encoder, the config parses, every
configured dict/freq/pitch zip exists, the SQLite dict cache is built, fonts load, AnkiConnect is
reachable (+ the mine deck/model exist), the interpreter is free-threaded with the GIL actually off,
and — socket coexistence — whether ``mpv.conf`` sets ``input-ipc-server`` and which other tools are
known to share it. If plugin mode is installed, it checks the ``saitenka.lua`` user-script spawns the
correct ``attach`` subcommand (not a stale ``--attach``) and matches this build; when jimaku is
enabled it checks an API key resolves (and warns if it's only in a GUI-invisible env var). It WARNS,
never modifies. ``--json`` for tooling. A "recent errors" section tails the rotating error log.

Every check is a pure function returning a :class:`Check`, so the whole thing is mockable and
hermetic in tests (no network, no real files).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import sysconfig
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from overlay.app.anki import ANKI_DOWN_ERRORS
from overlay.app.config import config_path, load_config
from overlay.app.paths import cache_dir

Status = str  # "ok" | "warn" | "fail"

LOG_PATH = cache_dir() / "overlay.log"
ANKI_HOST = "http://127.0.0.1:8765"
MPV_MIN = (0, 37)  # overlay-add BGRA landed in 0.37

# Known consumers of an mpv input-ipc-server socket — flagged for the coexistence story so the user
# knows we JOIN a shared socket rather than fight over it (the SubMiner-vs-animecards Windows bug).
KNOWN_SOCKETS = {
    "/tmp/subminer-socket": "SubMiner",  # noqa: S108  # third-party tool socket path we DETECT, not one we create
    "/tmp/mpv-socket": "animecards",  # noqa: S108  # third-party tool socket path we DETECT, not one we create
    "/tmp/mpvsocket": "mpv_websocket",  # noqa: S108  # third-party tool socket path we DETECT, not one we create
}


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    info: bool = False  # a passing, purely-informational line — hidden in the default view (kept in
    # `--json` and shown with `doctor --verbose`), so a healthy run isn't a wall of green noise.


@dataclass
class Report:
    checks: list[Check]

    @property
    def counts(self) -> dict[str, int]:
        out = {"ok": 0, "warn": 0, "fail": 0}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    @property
    def exit_code(self) -> int:
        return 1 if self.counts["fail"] else 0

    def to_json(self) -> dict:
        return {
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail, "info": c.info}
                for c in self.checks
            ],
            "summary": self.counts,
        }


# --- low-level helpers (mock points) ---------------------------------------------------------


def _run(*args: str) -> str:
    """Run a command, returning combined stdout (best-effort; '' on failure)."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _anki_call(action: str, **params):
    """doctor's AnkiConnect probe — fast-fail (short timeout, no retry) via the single client (SSOT).
    Raises from :data:`~overlay.app.anki.ANKI_DOWN_ERRORS`; callers catch that and warn."""
    from overlay.app.anki import Anki

    return Anki()._call(action, timeout=5, attempts=1, **params)


def _mpv_conf_path() -> Path:
    """The mpv.conf that exists (checking mpv's own dir then mpv.net's), else mpv's default. Mirrors
    mpv's own resolution so the Windows checks look at %APPDATA%\\mpv, not ~/.config/mpv."""
    from overlay.app.paths import mpv_conf_paths

    candidates = mpv_conf_paths()
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


# --- individual checks -----------------------------------------------------------------------


def check_mpv() -> Check:
    from overlay.mpvio.discover import find_mpv

    # Resolve like `run` does (config mpv_path → $SAITENKA_MPV_PATH → PATH → known dirs / mpv.net), so
    # doctor doesn't cry "not found" for a perfectly usable off-PATH mpv (the Windows norm).
    mpv = find_mpv(load_config().get("mpv_path"))
    if not mpv:
        return Check(
            "mpv",
            "fail",
            "mpv not found (needed to play + composite the overlay) — install it, or set `mpv_path` "
            "in overlay.toml / $SAITENKA_MPV_PATH",
        )
    out = _run(mpv, "--version")
    m = re.search(r"mpv\s+v?(\d+)\.(\d+)", out)
    if not m:
        # mpv.net reports its own version string; if it responded at all, treat as present.
        detail = f"mpv.net ({mpv})" if "mpvnet" in Path(mpv).name.lower() else f"present ({mpv})"
        return Check("mpv", "warn", f"mpv version unparseable — {detail}")
    ver = (int(m.group(1)), int(m.group(2)))
    vs = f"{ver[0]}.{ver[1]}"
    if ver < MPV_MIN:
        return Check("mpv", "fail", f"mpv {vs} too old — need ≥ 0.37 for overlay-add BGRA")
    return Check("mpv", "ok", f"mpv {vs} ({mpv})")


def check_ffmpeg() -> Check:
    if not shutil.which("ffmpeg"):
        return Check(
            "ffmpeg", "fail", "ffmpeg not found on PATH (needed for mined-clip audio/frames)"
        )
    out = _run("ffmpeg", "-hide_banner", "-encoders")
    if not re.search(r"^\s*\S*\s+aac\b", out, re.MULTILINE):
        return Check(
            "ffmpeg", "warn", "ffmpeg present but no aac encoder — mined SentenceAudio won't encode"
        )
    # Opt-in [mine].animated_screenshot prefers WebP but falls back to GIF (native to every ffmpeg), so
    # animation works even without libwebp — informational, never a warning.
    if any(e in out for e in ("libwebp_anim", "libwebp")):
        anim = " + animated webp"
    elif re.search(r"^\s*\S*\s+gif\b", out, re.MULTILINE):
        anim = " + animated gif (no libwebp — WebP clips unavailable, GIF used)"
    else:
        anim = " (no webp/gif encoder — animated screenshots stay still)"
    return Check("ffmpeg", "ok", "ffmpeg + aac" + anim)


def check_config() -> Check:
    p = config_path()
    if not p.exists():
        return Check("config", "warn", f"no config at {p} — run `saitenka init`")
    try:
        with p.open("rb") as stream:
            tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return Check(
            "config",
            "fail",
            f"config parse error: {e}; Windows pipe paths are safest as single-quoted TOML, "
            r"for example mpv_socket = '\\.\pipe\mpvsocket'",
        )
    return Check("config", "ok", f"config parses ({p})")


def _no_db_checks(db_file, *, any_configured: bool) -> list[Check]:
    if any_configured:
        return [
            Check(
                "dict-db",
                "fail",
                "config lists dictionaries but none are imported yet — run "
                f"`saitenka import <dir-with-zips>` (no DB at {db_file})",
            )
        ]
    if _jmdict_available():
        return [Check("dict-db", "warn", "no dictionaries imported (JMdict fallback only)")]
    return [
        Check(
            "dict-db",
            "warn",
            "no dictionaries imported and no JMdict fallback installed — tooltips and mined cards "
            "will have no glosses. Import Yomitan dicts (`saitenka import <dir>`), or add "
            "the fallback: reinstall with the `jmdict` extra.",
        )
    ]


def _title_checks(configured: dict[str, list[str]], imported: dict) -> list[Check]:
    """One check per configured title. A resolved title is an ``info`` line (the full itemised list,
    shown only with --verbose); a title the config references but the DB lacks is a hard failure."""
    checks: list[Check] = []
    for kind, titles in configured.items():
        for title in titles:
            if title in imported:
                checks.append(Check(kind, "ok", f"{kind}: {title}", info=True))
            else:
                checks.append(
                    Check(
                        kind,
                        "fail",
                        f"{kind} not imported: {title!r} — run `saitenka import <dir>`",
                    )
                )
    return checks


def check_dict_db() -> list[Check]:
    """Report the consolidated dictionary DB: which dictionaries are imported, and whether every title
    the config references actually resolves (dictionaries are imported once by ``saitenka
    import`` — a configured-but-unimported title is a clear failure, not a silent empty lookup)."""
    from overlay.app.dictdb import DictionaryDb, db_path

    cfg = load_config()
    configured = {kind: list(cfg.get(kind) or []) for kind in ("dicts", "freq", "pitch")}
    any_configured = any(configured.values())
    db_file = db_path()

    if not db_file.exists():
        return _no_db_checks(db_file, any_configured=any_configured)

    db = DictionaryDb.open()
    imported = {
        r.title: r for r in db.list_dictionaries() if r.import_order >= 0
    }  # hide system dicts
    n = {kind: sum(1 for t in titles if t in imported) for kind, titles in configured.items()}
    checks = [
        Check("dict-db", "ok", f"dicts: {n['dicts']} · freq: {n['freq']} · pitch: {n['pitch']}"),
        Check("dict-db", "ok", f"{len(imported)} imported in {db_file}", info=True),
    ]
    checks += _title_checks(configured, imported)
    return checks


def _jmdict_available() -> bool:
    """True when the optional JMdict fallback (jamdict + its database) is importable."""
    import importlib.util

    return all(importlib.util.find_spec(m) is not None for m in ("jamdict", "jamdict_data"))


def check_legacy_files() -> Check:
    """Warn (informational) when pre-consolidation leftovers exist: the old per-zip SQLite cache and the
    copied dictionary zips. The single ``dictionaries.sqlite`` no longer needs them, so they're safe to
    delete — but nothing is removed automatically."""
    from overlay.app.paths import legacy_dict_artifacts

    arts = legacy_dict_artifacts()
    if not arts:
        return Check("legacy-files", "ok", "no pre-consolidation dictionary files to clean up")
    total = sum(b for _, _, b in arts)
    where = "; ".join(f"{d} ({n} files, {b / 1e6:.0f} MB)" for d, n, b in arts)
    return Check(
        "legacy-files",
        "warn",
        f"{total / 1e6:.0f} MB of pre-consolidation files are unused and safe to delete: {where}",
    )


def check_sub_auto() -> Check:
    """mpv's ``sub-auto=all`` loads EVERY text file in the video's folder as a subtitle (junk
    externals the overlay may read). ``fuzzy``/``exact`` are safe."""
    p = _mpv_conf_path()
    if not p.exists():
        return Check("sub-auto", "ok", "no mpv.conf — mpv default sub-auto=exact", info=True)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # pragma: no cover
        return Check("sub-auto", "warn", f"couldn't read {p}: {e}")
    m = re.search(r"^\s*sub-auto\s*=\s*(\S+)", text, re.MULTILINE)
    val = m.group(1) if m else "exact"
    if val == "all":
        return Check(
            "sub-auto",
            "warn",
            "mpv.conf sub-auto=all loads every text file in the folder as a subtitle — set "
            "sub-auto=fuzzy (or exact) so the overlay doesn't pick up junk externals",
        )
    return Check("sub-auto", "ok", f"mpv.conf sub-auto={val}", info=True)


def check_fonts() -> Check:
    try:
        from overlay import fonts

        missing = [f for f in fonts.FONT_FILES if not (fonts.ASSETS / f).exists()]
    except (ImportError, OSError) as e:  # pragma: no cover — import failure fails elsewhere first
        return Check("fonts", "fail", f"font module import failed: {e}")
    if missing:
        return Check("fonts", "fail", f"vendored fonts missing: {missing}")
    return Check("fonts", "ok", f"vendored fonts present ({len(fonts.FONT_FILES)})")


_TTS_HINTS = {
    "win32": "Install the Japanese language pack: Settings → Time & Language → Language → add 日本語 "
    "→ Language options → Speech.",
    "darwin": "Add a Japanese voice: System Settings → Accessibility → Spoken Content → System Voice "
    "→ Manage Voices (e.g. Kyoko).",
}


def check_tts() -> Check:
    """The OS TTS the tooltip 🔊 button uses to pronounce a scanned word. When no JAPANESE voice is
    available the button is hidden (it would silently do nothing) — surface why so it's not a mystery."""
    from overlay.app.media import tts_available

    if tts_available():
        return Check("tts", "ok", "Japanese TTS voice available — 🔊 speaks scanned words")
    hint = _TTS_HINTS.get(sys.platform, "Install espeak (e.g. `apt install espeak`).")
    return Check("tts", "warn", f"no Japanese TTS voice — the 🔊 button is hidden. {hint}")


def check_anki(deck: str, model: str) -> Check:
    from overlay.app.anki import resolve_anki

    host, _ = resolve_anki()
    try:
        ver = _anki_call("version")
    except ANKI_DOWN_ERRORS:
        return Check(
            "anki",
            "warn",
            f"AnkiConnect unreachable at {host} (optional — needed for mining/coloring; set "
            "[anki].url if you changed AnkiConnect's port)",
        )
    detail = f"AnkiConnect v{ver}"
    try:
        decks = _anki_call("deckNames") or []
        models = _anki_call("modelNames") or []
    except ANKI_DOWN_ERRORS:
        return Check("anki", "warn", f"{detail}, but couldn't list decks/models")
    if model not in models:  # a note type can't be auto-created — mining truly can't run without it
        return Check("anki", "fail", f"{detail}, but mining note type {model!r} doesn't exist")
    if problem := _mining_field_problem(
        model
    ):  # configured field map vs the note type's real fields
        return Check("anki", "warn", f"{detail}, but mining note type {model!r} {problem}")
    if deck not in decks:  # a deck IS auto-created on the first addNote, so this is only a heads-up
        return Check(
            "anki",
            "warn",
            f"{detail}, but mining deck {deck!r} doesn't exist yet (created on first mine)",
        )
    return Check("anki", "ok", f"{detail}; mining deck+note type present")


def _card_format_marker_problem(card_format: dict) -> str | None:
    """Unknown ``{marker}`` in a ``[mine.card_format]`` template — a marker Saitenka can't fill, so the
    field would render empty. Doesn't need Anki (a pure marker-name check)."""
    from overlay.app.card_markers import MARKERS, markers_in

    used = {m for tmpl in card_format.values() for m in markers_in(str(tmpl))}
    unknown = sorted(used - MARKERS)
    return f"uses unknown [mine.card_format] marker(s) {unknown}" if unknown else None


def _mining_field_problem(model: str) -> str | None:
    """The mismatch (if any) between the effective mining fields and ``model``'s real fields: configured
    field names absent from the note type, or an unknown ``card_format`` marker. ``None`` when all is
    well, or the fields can't be read (skip rather than guess). Warn-level — an unknown field/marker just
    writes nothing (fields are dropped at build time), it doesn't crash mining. The effective field names
    are ``card_format``'s keys when it's set (it wins wholesale, #192), else the ``fields`` map values."""
    from overlay.app.reader_deps import _mine_config_from

    mine_conf = _mine_config_from(load_config().get("mine") or {})
    if mine_conf.card_format and (problem := _card_format_marker_problem(mine_conf.card_format)):
        return problem  # marker names need no Anki call — report first
    field_names = (
        set(mine_conf.card_format) if mine_conf.card_format else set(mine_conf.fields.values())
    )
    try:
        real = set(_anki_call("modelFieldNames", modelName=model) or [])
    except ANKI_DOWN_ERRORS:
        return None  # a transient read failure must skip validation, not crash the doctor run
    if not real:
        return None
    missing = sorted(name for name in field_names if name not in real)
    return f"is missing mining field(s) {missing}" if missing else None


def _known_deck_fields(deck: str) -> set[str] | None:
    """Field names on the first note of ``deck``, or ``None`` when the deck is empty / unreadable — so
    the caller can confirm the deck exists yet skip field validation it can't perform."""
    ids = _anki_call("findNotes", query=f'deck:"{deck}"') or []
    if not ids:
        return None
    info = _anki_call("notesInfo", notes=ids[:1]) or []
    return set(info[0].get("fields", {})) if info else None


def _known_deck_problem(deck: str, fields, decks: set[str]) -> str | None:
    """The mismatch (if any) between one configured ``[known]`` deck and Anki: a missing deck, or a
    chosen field absent from its note type. ``None`` when the deck is fine (or is empty/unreadable, so
    its fields can't be checked — the deck exists, which is what matters)."""
    if deck not in decks:
        return f"deck {deck!r} not found"
    note_fields = _known_deck_fields(deck)
    if note_fields is None:
        return None
    missing = [f for f in (fields or []) if f not in note_fields]
    if missing:
        return f"{deck!r} has no field(s) {missing} (has {sorted(note_fields)})"
    return None


def check_known() -> Check:
    """Validate the ``[known]`` coloring config against Anki: every configured deck exists and its chosen
    field(s) exist on the deck's note type. A deck/field that isn't there means coloring silently sees an
    empty known set, so it's an error the user should fix (or re-run ``setup``), not a silent no-op."""
    known = load_config().get("known") or {}
    if not known:
        return Check("known", "ok", "no known-words deck configured (coloring by freq+JLPT)")
    try:
        decks = set(_anki_call("deckNames") or [])
    except ANKI_DOWN_ERRORS:
        # The `anki` check already owns the single "AnkiConnect is down" warning — don't warn twice
        # for one root cause. This is just a skipped validation, hidden unless --verbose.
        return Check(
            "known", "ok", "[known] deck/fields unverified — AnkiConnect unreachable", info=True
        )
    problems = [
        p for deck, fields in known.items() if (p := _known_deck_problem(deck, fields, decks))
    ]
    if problems:
        return Check(
            "known", "fail", "known-words config doesn't match Anki: " + "; ".join(problems)
        )
    return Check("known", "ok", f"[known] deck(s)+field(s) present ({len(known)})")


def check_python() -> Check:
    """Report the exact interpreter — version, implementation, and GIL/free-threaded build. Always
    green (informational): it exists so a bug report shows *which* Python is really running, since the
    free-threading advice below reads very differently on a 3.14 vs a 3.14t build, and a user can swap
    builds between installs. ``platform.python_version()`` has no 't' suffix, so the build string
    carries the free-threaded/GIL fact."""
    import platform

    ft_build = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if ft_build:
        gil_off = not getattr(sys, "_is_gil_enabled", lambda: True)()
        build = "free-threaded, GIL off" if gil_off else "free-threaded, GIL ON"
    else:
        build = "standard (GIL)"
    return Check(
        "python", "ok", f"{platform.python_implementation()} {platform.python_version()} ({build})"
    )


def check_free_threading() -> Check:
    ft_build = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    gil_off = not getattr(sys, "_is_gil_enabled", lambda: True)()
    if not ft_build:
        if sys.platform == "win32":
            # fugashi (the MeCab tokenizer) ships NO free-threaded Windows wheels yet, so a 3.14t
            # install builds it from source and fails (needs a system MeCab). Regular 3.14 is the
            # working config here — not a problem the user should "fix". Green, with a note.
            return Check(
                "free-threading",
                "ok",
                "standard 3.14 build — fine. For the ~3.8x render win on Windows, install the MSVC++ "
                "Build Tools (14+) and MeCab at C:\\mecab, then reinstall on 3.14t (fugashi builds from "
                "source; there are no 3.14t wheels yet)",
            )
        return Check(
            "free-threading",
            "warn",
            "not a free-threaded (3.14t) build — render won't parallelise (~3.8× lost). Reinstall on "
            "3.14t: `uv tool install --python 3.14+freethreaded --reinstall 'saitenka[full]'`",
        )
    if not gil_off:
        return Check(
            "free-threading", "warn", "3.14t build but GIL is ON — set PYTHON_GIL=0 (cli re-execs)"
        )
    return Check("free-threading", "ok", "free-threaded interpreter, GIL off")


def check_mpv_ipc() -> Check:
    p = _mpv_conf_path()
    if not p.exists():
        return Check(
            "mpv-ipc", "ok", "no mpv.conf input-ipc-server — overlay uses its own socket", info=True
        )
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # pragma: no cover
        return Check("mpv-ipc", "warn", f"couldn't read {p}: {e}")
    m = re.search(r"^\s*input-ipc-server\s*=\s*(\S+)", text, re.MULTILINE)
    if not m:
        return Check(
            "mpv-ipc", "ok", "mpv.conf has no input-ipc-server — no socket to share", info=True
        )
    sock = m.group(1)
    owner = KNOWN_SOCKETS.get(sock)
    who = f" (used by {owner})" if owner else ""
    return Check(
        "mpv-ipc",
        "ok",
        f"mpv.conf input-ipc-server={sock}{who} — attach mode can share it (mpv allows many clients)",
    )


def check_plugin() -> Check:
    """The mpv user-script (plugin mode). Absent is fine — plugin mode is opt-in. If installed,
    catch the two ways it silently no-ops on mpv launch:

    * the ``--attach`` form (a stale build called a flag the CLI rejects), and
    * a **bare** ``SAITENKA_BIN`` that a Finder/Dock-launched mpv can't resolve on its minimal PATH,
      or a baked path that no longer exists — both fixed by re-running ``install-plugin``."""
    from overlay.app.plugin import LUA_NAME, all_scripts_dirs

    # Check every scripts dir (mpv + mpv.net on Windows); report on the first installed copy.
    dest = next((d / LUA_NAME for d in all_scripts_dirs() if (d / LUA_NAME).exists()), None)
    if dest is None:
        return Check(
            "plugin", "ok", "mpv plugin not installed (optional — `install-plugin` for auto-start)"
        )
    try:
        installed = dest.read_text(encoding="utf-8")
    except OSError as e:  # pragma: no cover
        return Check("plugin", "warn", f"couldn't read {dest}: {e}")
    if "'--attach'" in installed or "'attach'" not in installed:
        return Check(
            "plugin",
            "fail",
            f"installed {LUA_NAME} uses the broken `--attach` form (mpv spawns a process that dies) "
            "— re-run `saitenka install-plugin`",
        )
    m = re.search(r"SAITENKA_BIN\s*=\s*(?:\[\[(.*?)\]\]|'([^']*)')", installed)
    binp = (m.group(1) or m.group(2)) if m else None
    if not binp or ("/" not in binp and "\\" not in binp):  # bare name (no separator, either OS)
        return Check(
            "plugin",
            "fail",
            f"installed {LUA_NAME} spawns a bare `{binp or '?'}` — a Finder-launched mpv can't "
            "resolve it on its PATH; re-run `saitenka install-plugin` to bake the abs path",
        )
    if not Path(binp).exists():
        return Check(
            "plugin",
            "warn",
            f"installed {LUA_NAME} points at {binp} which no longer exists — re-run `install-plugin`",
        )
    return Check("plugin", "ok", f"mpv plugin installed ({dest}) → {binp} attach")


def check_jimaku() -> Check:
    """When ``[jimaku].enabled``, confirm an API key resolves from persistent storage or the env."""
    cfg = load_config()
    _jm = cfg.get("jimaku")
    jm = _jm if isinstance(_jm, dict) else {}
    if not jm.get("enabled"):
        return Check("jimaku", "ok", "jimaku disabled (embedded JP subs only)")
    from overlay.app.jimaku import resolve_jimaku_key

    key, src = resolve_jimaku_key(jm.get("key"))
    if not key:
        return Check(
            "jimaku",
            "warn",
            "jimaku enabled but no API key — run `saitenka set-jimaku-key` (persistent and readable "
            "by plugin-mode mpv)",
        )
    if src == "env":
        # The resolver prefers env over the Keychain, so a key present in BOTH reports src=env. What
        # actually matters for plugin-mode mpv is whether the Keychain has it (it can't read the shell
        # env) — so only warn when the Keychain is genuinely empty, not just shadowed by $JIMAKU_API_KEY.
        from overlay.app.jimaku import keychain_get

        if keychain_get():
            return Check(
                "jimaku", "ok", "jimaku enabled; API key in Keychain (also set in $JIMAKU_API_KEY)"
            )
        return Check(
            "jimaku",
            "warn",
            "jimaku key from $JIMAKU_API_KEY only — works in a terminal but NOT under a GUI-launched "
            "(plugin) mpv; run `set-jimaku-key` to persist it",
        )
    return Check("jimaku", "ok", f"jimaku enabled; API key from {src}")


def check_subminer_conflict() -> Check:
    """SubMiner injects its own mpv overlay; running it alongside the saitenka plugin draws two
    overlays over one video (flicker / stuck "overlay loading"). Warn when it's live."""
    from overlay.app.conflicts import subminer_installed, subminer_running

    if subminer_running():
        return Check(
            "subminer",
            "warn",
            "SubMiner is RUNNING — it injects its own mpv overlay; the saitenka overlay steps aside "
            "while it runs. Quit SubMiner (or uninstall its plugin) to use saitenka",
        )
    if subminer_installed():
        return Check(
            "subminer", "ok", "SubMiner installed but not running (no overlay conflict)", info=True
        )
    return Check("subminer", "ok", "no SubMiner (no overlay conflict)", info=True)


def check_crashes() -> Check:
    """Surface captured crash reports (from crashlog's excepthooks) so the user knows to send them."""
    from overlay.app.crashlog import crash_dir

    d = crash_dir()
    reports = sorted(d.glob("crash-*.log")) if d.exists() else []
    if not reports:
        return Check("crashes", "ok", "no crash reports")
    return Check(
        "crashes",
        "warn",
        f"{len(reports)} crash report(s) captured; latest {reports[-1].name} — run "
        "`saitenka report` to bundle them",
    )


def check_perf() -> Check:
    """Live latency + memory snapshot (render, hover hit-test, RSS) from :mod:`overlay.app.perf` — the
    same percentiles the ``--stress`` benchmark reports, but from the actual running session.
    Informational: latency is empty until a tooltip has been shown; RSS is always available."""
    from overlay.app.perf import rss_mb, snapshot

    snap = snapshot()
    rss = rss_mb()
    parts = [
        f"{op} p50={s['p50']:.1f}ms p95={s['p95']:.1f}ms max={s['max']:.1f}ms (n={s['n']:.0f})"
        for op, s in snap.items()
    ]
    if not parts:
        parts.append("no ops recorded yet (nothing shown this session)")
    if rss is not None:
        parts.append(f"rss={rss:.0f}MB")
    return Check("perf", "ok", "; ".join(parts))


def check_telemetry() -> Check:
    """OTel tracing/metrics status — off by default, purely
    informational either way. ``doctor`` runs as its OWN short-lived process, so it can't read a
    separate live overlay session's in-memory metrics (pull-based / process-local by design,
    :func:`overlay.otel_metrics.snapshot`); it reports config state + the on-disk CTF trace
    file the live session wrote, if telemetry was enabled for it."""
    from overlay.app.config import load_config, resolve_telemetry
    from overlay.app.telemetry import export_dir, latest_trace

    opts = resolve_telemetry(load_config())
    if not opts.enabled:
        return Check(
            "telemetry", "ok", "telemetry disabled ([telemetry] enabled = false)", info=True
        )
    trace_path = latest_trace(
        export_dir(opts)
    )  # newest per-session trace (they rotate, timestamped)
    if trace_path is None:
        return Check(
            "telemetry",
            "ok",
            f"telemetry enabled, no trace yet in {export_dir(opts)} (nothing recorded this session, "
            "or the 'telemetry' extra isn't installed)",
        )
    st = trace_path.stat()
    return Check(
        "telemetry",
        "ok",
        f"telemetry enabled — last trace {trace_path} ({st.st_size / 1024:.0f} KiB, "
        f"modified {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))})",
    )


def _summarize_log_line(ln: str) -> str | None:
    """Compact one-liner for a recent-errors entry, or ``None`` to skip it.

    Structured (JSON) records are filtered by their real ``level`` — an ``error`` string buried
    in an ``exception`` traceback must NOT promote a ``debug`` record (e.g. the expected
    Anki-down cache-refresh noise). The full traceback is collapsed to its final
    ``ExcType: message`` line so ``doctor`` never dumps a screenful. Non-JSON lines fall back to a
    word match on the raw text."""
    try:
        rec = json.loads(ln)
    except ValueError:
        raw = ln.strip()
        return (
            _clip(raw) if re.search(r"\b(error|critical|warning)\b", raw, re.IGNORECASE) else None
        )
    if not isinstance(rec, dict) or str(rec.get("level", "")).lower() not in {
        "warning",
        "error",
        "critical",
    }:
        return None
    event = str(rec.get("event", "")).strip()
    if exc := rec.get("exception"):
        last = next((ln.strip() for ln in reversed(str(exc).splitlines()) if ln.strip()), "")
        event = f"{event} — {last}" if event and last else event or last
    return _clip(f"[{rec['level']}] {event}") if event else None


def _clip(s: str, width: int = 200) -> str:
    return s if len(s) <= width else s[: width - 1] + "…"


def check_recent_errors(n: int = 5) -> Check:
    if not LOG_PATH.exists():
        return Check("recent-errors", "ok", "no log yet (nothing has failed)")
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:  # pragma: no cover
        return Check("recent-errors", "warn", f"couldn't read log: {e}")
    errs = [s for ln in lines if (s := _summarize_log_line(ln))][-n:]
    if not errs:
        return Check("recent-errors", "ok", "no recent errors in the log")
    return Check("recent-errors", "warn", "recent log errors:\n    " + "\n    ".join(errs))


_OVERLAY_START_RE = re.compile(r"saitenka overlay (\S+) starting")


def _norm_version(v: str) -> str:
    """Drop the volatile ``-dirty`` suffix: an editable checkout's working tree can be dirty at session
    start but clean at doctor time (or vice versa — a test run rewrites the complexipy snapshot), and
    that flip alone must not read as a different build. Base + git sha still compare."""
    return v.removesuffix("-dirty")


def check_stale_overlay() -> Check:
    """Warn when the overlay that LAST ran (its ``… starting`` line in overlay.log) is a different build
    from the installed one — the 'I reinstalled but nothing changed' trap. The mpv plugin spawns
    ``saitenka attach`` ONCE per mpv session, so an mpv left open across an update keeps its old modules
    until you fully quit and relaunch. No log, no version line (a fresh install, or logs predating this
    line), or an unreadable log → informational, never a false warning."""
    from overlay.version import overlay_version

    if not LOG_PATH.exists():
        return Check("overlay-build", "ok", "no overlay session logged yet", info=True)
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # pragma: no cover
        return Check("overlay-build", "ok", f"couldn't read overlay log: {e}", info=True)
    seen = _OVERLAY_START_RE.findall(text)
    if not seen:
        return Check(
            "overlay-build",
            "ok",
            "overlay build unknown (log predates the version line)",
            info=True,
        )
    ran, installed = seen[-1], overlay_version()
    if _norm_version(ran) == _norm_version(installed):
        return Check(
            "overlay-build", "ok", f"overlay build matches installed ({installed})", info=True
        )
    return Check(
        "overlay-build",
        "warn",
        f"overlay last ran {ran} but {installed} is installed — fully quit and relaunch mpv to load it "
        "(the attach process is spawned once per mpv session)",
    )


def check_deinflect() -> Check:
    """The optional GPL-3.0 deinflect add-on supplies the tooltip's inflection-chain chips
    (🧩 -て « -いる « -た). The Apache-2.0 core runs without it (no chips shown), so this WARNS with
    how to enable it rather than failing."""
    try:
        import saitenka_deinflect  # noqa: F401,TID251  # GPL-3.0 chokepoint: doctor is a sanctioned importer (.importlinter gpl-chokepoint)
    except ImportError:
        return Check(
            "deinflect",
            "warn",
            "deinflect add-on not installed → no inflection chips. Enable it with "
            "`uv tool install 'saitenka[deinflect]'` (GPL-3.0) or `[full]`",
        )
    return Check("deinflect", "ok", "deinflect add-on installed → inflection chips enabled")


# --- driver ----------------------------------------------------------------------------------


def check_version() -> Check:
    """The overlay's own version — first line of the report, so a bug report is anchored to a build
    without cross-referencing versions.txt."""
    from overlay.version import overlay_version as _overlay_version

    return Check("version", "ok", f"saitenka {_overlay_version()}")


def check_windows() -> Check:
    """Windows edition + build (the OS half of a Windows bug report). Green everywhere — informational;
    on non-Windows it just records the platform. Positive ``== 'win32'`` guard so mypy exempts the
    Windows-only branch from ``warn_unreachable`` off-Windows."""
    import platform

    if sys.platform == "win32":
        return Check(
            "windows", "ok", f"{platform.platform()} (edition {platform.win32_edition() or '?'})"
        )
    return Check("windows", "ok", f"not Windows ({platform.system()})", info=True)


def check_powershell() -> Check:
    """PowerShell version — the shell that runs the install stubs; a bug report needs it and it isn't
    in versions.txt. Non-Windows → n/a."""
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return Check("powershell", "warn", "PowerShell not found / not queryable")
        v = out.stdout.strip()
        return Check("powershell", "ok", f"PowerShell {v}" if v else "PowerShell (version unknown)")
    return Check("powershell", "ok", "n/a (not Windows)", info=True)


def check_mpv_socket() -> Check:
    """Whether ``mpv_socket`` is set (attach-to-your-own-mpv). Informational (never warns): plugin mode
    passes its own socket, so an unset value is fine — but a manual ``attach`` NEEDS it, and its silent
    absence is what stalls users. mpv.net's default IPC pipe is ``\\\\.\\pipe\\mpvsocket``."""
    sock = load_config().get("mpv_socket")
    if sock:
        from overlay.mpvio.ipc import is_windows_pipe_path, normalize_ipc_path

        normalized = normalize_ipc_path(str(sock))
        if sys.platform == "win32" and not is_windows_pipe_path(normalized):
            return Check(
                "mpv-socket",
                "warn",
                f"mpv_socket is not a Windows named pipe ({sock}); use "
                r"'\\.\pipe\mpvsocket' or omit it for mpv.net's default",
            )
        if normalized != sock:
            return Check(
                "mpv-socket",
                "warn",
                f"mpv_socket contains locale/path separators; Saitenka will normalize it to "
                f"{normalized}. Rewrite it as a single-quoted TOML string.",
            )
        return Check("mpv-socket", "ok", f"mpv_socket set ({sock}) — bare `attach` connects here")
    if sys.platform == "win32":
        from overlay.mpvio.ipc import MPVNET_DEFAULT_PIPE

        return Check(
            "mpv-socket",
            "ok",
            f"no mpv_socket — bare `attach` uses mpv.net's default ({MPVNET_DEFAULT_PIPE})",
            info=True,
        )
    return Check(
        "mpv-socket",
        "ok",
        "no mpv_socket — plugin mode auto-passes its own; to attach to YOUR already-running mpv set "
        "mpv_socket in overlay.toml (for example /tmp/mpv-socket)",
        info=True,
    )


def run_checks(deck: str = "Saitenka::Mining", model: str = "Lapis") -> Report:
    checks: list[Check] = [
        check_version(),
        check_python(),
        check_windows(),
        check_powershell(),
        check_mpv(),
        check_ffmpeg(),
        check_free_threading(),
        check_config(),
        *check_dict_db(),
        check_legacy_files(),
        check_sub_auto(),
        check_fonts(),
        check_tts(),
        check_deinflect(),
        check_anki(deck, model),
        check_known(),
        check_mpv_ipc(),
        check_mpv_socket(),
        check_plugin(),
        check_subminer_conflict(),
        check_jimaku(),
        check_crashes(),
        check_recent_errors(),
        check_stale_overlay(),
        check_telemetry(),
    ]
    return Report(checks)


# On Windows keep it PLAIN ASCII — no ANSI colours, no ✓/✗ glyphs. The classic console mangles both,
# and forcing a UTF-8 codepage to render them breaks interactive typing. POSIX terminals get the
# coloured version.
_WIN = sys.platform == "win32"
_GLYPH = (
    {"ok": "[ok] ", "warn": "[!]  ", "fail": "[x]  "}
    if _WIN
    else {"ok": "\033[32m✓\033[0m", "warn": "\033[33m!\033[0m", "fail": "\033[31m✗\033[0m"}
)


def _shown_checks(report: Report, *, summary: bool, verbose: bool) -> list[Check]:
    """The checks to print at each density: ``summary`` → only ``!``/``✗``; ``verbose`` → all;
    default → everything but the purely-informational ``info`` lines."""
    if summary:
        return [c for c in report.checks if c.status != "ok"]
    if verbose:
        return report.checks
    return [c for c in report.checks if not c.info]


def _print_footer(report: Report) -> None:  # pragma: no cover — formatting/IO
    s = report.counts
    if _WIN:
        print(f"\nSummary: {s['ok']} ok / {s['warn']} warn / {s['fail']} fail")
    else:
        print(
            f"\nSummary: \033[32m{s['ok']} ok\033[0m · "
            f"\033[33m{s['warn']} warn\033[0m · \033[31m{s['fail']} fail\033[0m"
        )
    if report.exit_code == 0:
        print("Healthy" if _WIN else "Healthy ✅")
    else:
        print("Problems found - see [x] above" if _WIN else "Problems found — see ✗ above ❌")
        print("Tip: `saitenka report` bundles this + logs into a zip for a bug report.")


def print_report(
    report: Report, *, summary: bool = False, verbose: bool = False
) -> None:  # pragma: no cover — formatting/IO
    """Print the report. Three densities: ``summary`` shows only ``!``/``✗`` (installer/setup, which
    re-run doctor); default hides purely-informational ``info`` lines (platform, unset sockets, the
    full dict list) so a healthy run is short but every problem still shows verbatim; ``verbose``
    shows everything. ``--json`` always carries the full set."""
    print("[saitenka doctor]" if _WIN else "\033[1;36m[saitenka doctor]\033[0m")
    shown = _shown_checks(report, summary=summary, verbose=verbose)
    for c in shown:
        print(f"  {_GLYPH.get(c.status, '?')} {c.detail}")
    if summary and not shown:  # nothing but ✓ — one reassuring line instead of the full list
        print(f"  {_GLYPH['ok']} all {report.counts['ok']} checks passed")
    hidden = sum(1 for c in report.checks if c.info)
    if not summary and not verbose and hidden:  # make the collapsed lines discoverable
        print(f"  {_GLYPH['ok']} +{hidden} informational checks hidden — `--verbose` to show")
    _print_footer(report)
