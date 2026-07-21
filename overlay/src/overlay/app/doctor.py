"""``saitenka-overlay doctor`` — read-only health check.

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
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from overlay.app.config import config_path, expand_paths, is_protected, load_config
from overlay.app.paths import cache_dir

Status = str  # "ok" | "warn" | "fail"

LOG_PATH = cache_dir() / "overlay.log"
CACHE_DIR = cache_dir() / "dicts"
ANKI_HOST = "http://127.0.0.1:8765"
MPV_MIN = (0, 37)  # overlay-add BGRA landed in 0.37

# Known consumers of an mpv input-ipc-server socket — flagged for the coexistence story so the user
# knows we JOIN a shared socket rather than fight over it (the SubMiner-vs-animecards Windows bug).
KNOWN_SOCKETS = {
    "/tmp/subminer-socket": "SubMiner",
    "/tmp/mpv-socket": "animecards",
    "/tmp/mpvsocket": "mpv_websocket",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


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
                {"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks
            ],
            "summary": self.counts,
        }


# --- low-level helpers (mock points) ---------------------------------------------------------


def _run(*args: str) -> str:
    """Run a command, returning combined stdout (best-effort; '' on failure)."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _anki_call(action: str, **params):
    from overlay.app.anki import resolve_anki

    host, api_key = resolve_anki()  # honors [anki].url / host / port / api_key
    payload: dict = {"action": action, "version": 6, "params": params}
    if api_key:
        payload["key"] = api_key
    req = urllib.request.Request(
        host, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        res = json.loads(r.read())
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res.get("result")


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
    return Check("ffmpeg", "ok", "ffmpeg + aac")


def check_config() -> Check:
    p = config_path()
    if not p.exists():
        return Check("config", "warn", f"no config at {p} — run `saitenka-overlay init`")
    try:
        load_config()
    except Exception as e:  # pragma: no cover — load_config already swallows parse errors
        return Check("config", "fail", f"config parse error: {e}")
    return Check("config", "ok", f"config parses ({p})")


def check_dict_files() -> list[Check]:
    cfg = load_config()
    checks: list[Check] = []
    for kind in ("dicts", "freq", "pitch"):
        for path in expand_paths(cfg.get(kind)):
            p = Path(path)
            if p.exists():
                checks.append(Check(f"{kind}", "ok", f"{kind}: {p.name}"))
            else:
                # A "path" with no separator is almost always a bare Yomitan TITLE left in the config
                # by `import-settings` without --scan-dir — the exact FileNotFoundError crash on Windows.
                looks_like_title = "/" not in str(path) and "\\" not in str(path)
                hint = (
                    " — looks like a Yomitan title, not a file; run `import-settings --scan-dir <dir>`"
                    " or `import-dictionaries <export.json>`"
                    if looks_like_title
                    else ""
                )
                checks.append(Check(f"{kind}", "fail", f"{kind} not found: {path}{hint}"))
    if not checks:
        if _jmdict_available():
            checks.append(
                Check("dicts", "warn", "no dictionaries configured (JMdict fallback only)")
            )
        else:
            checks.append(
                Check(
                    "dicts",
                    "warn",
                    "no dictionaries configured and no JMdict fallback installed — tooltips and mined "
                    "cards will have no glosses. Import Yomitan dicts (`import-settings`), or add the "
                    "fallback: reinstall with the `jmdict` extra (e.g. `uv tool install "
                    "'saitenka-overlay[jmdict]'`).",
                )
            )
    return checks


def _jmdict_available() -> bool:
    """True when the optional JMdict fallback (jamdict + its database) is importable."""
    import importlib.util

    return all(importlib.util.find_spec(m) is not None for m in ("jamdict", "jamdict_data"))


def check_dict_locations() -> Check:
    """Warn when any configured dict/freq/pitch zip lives in a TCC-protected folder — a GUI-launched
    (plugin-mode) mpv trips a macOS consent prompt reading them each run. ``copy-dicts`` fixes it."""
    cfg = load_config()
    prot = [
        p
        for kind in ("dicts", "freq", "pitch")
        for p in expand_paths(cfg.get(kind))
        if is_protected(p)
    ]
    if prot:
        return Check(
            "dict-location",
            "warn",
            f"{len(prot)} dict(s) under a protected folder (Documents/Desktop/Downloads) — GUI mpv "
            "prompts for access each run; run `saitenka-overlay copy-dicts` to relocate + repoint",
        )
    return Check("dict-location", "ok", "dictionaries outside protected folders (no GUI prompt)")


def check_sub_auto() -> Check:
    """mpv's ``sub-auto=all`` loads EVERY text file in the video's folder as a subtitle (junk
    externals the overlay may read). ``fuzzy``/``exact`` are safe."""
    p = _mpv_conf_path()
    if not p.exists():
        return Check("sub-auto", "ok", "no mpv.conf — mpv default sub-auto=exact")
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
    return Check("sub-auto", "ok", f"mpv.conf sub-auto={val}")


def check_dict_cache() -> Check:
    if not CACHE_DIR.exists():
        return Check("dict-cache", "warn", f"no dict cache yet at {CACHE_DIR} (built on first run)")
    n = len(list(CACHE_DIR.glob("*.sqlite")))
    if n == 0:
        return Check("dict-cache", "warn", f"dict cache dir empty ({CACHE_DIR})")
    return Check("dict-cache", "ok", f"{n} cached dict index(es) in {CACHE_DIR}")


def check_fonts() -> Check:
    try:
        from overlay import fonts

        missing = [f for f in fonts.FONT_FILES if not (fonts.ASSETS / f).exists()]
    except Exception as e:  # pragma: no cover — import failure would already fail elsewhere
        return Check("fonts", "fail", f"font module import failed: {e}")
    if missing:
        return Check("fonts", "fail", f"vendored fonts missing: {missing}")
    return Check("fonts", "ok", f"vendored fonts present ({len(fonts.FONT_FILES)})")


def check_anki(deck: str, model: str) -> Check:
    from overlay.app.anki import resolve_anki

    host, _ = resolve_anki()
    try:
        ver = _anki_call("version")
    except Exception:
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
        if deck not in decks:
            return Check("anki", "warn", f"{detail}, but mine deck {deck!r} not found")
        if model not in models:
            return Check("anki", "warn", f"{detail}, but note type {model!r} not found")
    except Exception:
        return Check("anki", "warn", f"{detail}, but couldn't list decks/models")
    return Check("anki", "ok", f"{detail}; deck+model present")


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
                "standard 3.14 build — free-threading isn't available on Windows yet (the tokenizer "
                "has no 3.14t wheels); rendering is single-threaded but fine",
            )
        return Check(
            "free-threading",
            "warn",
            "not a free-threaded (3.14t) build — render won't parallelise (~3.8× lost). Reinstall on "
            "3.14t: `uv tool install --python 3.14+freethreaded --reinstall 'saitenka-overlay[full]'`",
        )
    if not gil_off:
        return Check(
            "free-threading", "warn", "3.14t build but GIL is ON — set PYTHON_GIL=0 (cli re-execs)"
        )
    return Check("free-threading", "ok", "free-threaded interpreter, GIL off")


def check_mpv_ipc() -> Check:
    p = _mpv_conf_path()
    if not p.exists():
        return Check("mpv-ipc", "ok", "no mpv.conf input-ipc-server — overlay uses its own socket")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # pragma: no cover
        return Check("mpv-ipc", "warn", f"couldn't read {p}: {e}")
    m = re.search(r"^\s*input-ipc-server\s*=\s*(\S+)", text, re.MULTILINE)
    if not m:
        return Check("mpv-ipc", "ok", "mpv.conf has no input-ipc-server — no socket to share")
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
            "— re-run `saitenka-overlay install-plugin`",
        )
    m = re.search(r"SAITENKA_BIN\s*=\s*(?:\[\[(.*?)\]\]|'([^']*)')", installed)
    binp = (m.group(1) or m.group(2)) if m else None
    if not binp or ("/" not in binp and "\\" not in binp):  # bare name (no separator, either OS)
        return Check(
            "plugin",
            "fail",
            f"installed {LUA_NAME} spawns a bare `{binp or '?'}` — a Finder-launched mpv can't "
            "resolve it on its PATH; re-run `saitenka-overlay install-plugin` to bake the abs path",
        )
    if not Path(binp).exists():
        return Check(
            "plugin",
            "warn",
            f"installed {LUA_NAME} points at {binp} which no longer exists — re-run `install-plugin`",
        )
    return Check("plugin", "ok", f"mpv plugin installed ({dest}) → {binp} attach")


def check_jimaku() -> Check:
    """When ``[jimaku].enabled``, confirm an API key resolves (config > env > Keychain). A key in
    the Keychain is the one plugin-mode mpv can read; a shell env var it cannot."""
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
            "jimaku enabled but no API key — run `saitenka-overlay set-jimaku-key` (Keychain, "
            "readable by plugin-mode mpv)",
        )
    if src == "env":
        return Check(
            "jimaku",
            "warn",
            "jimaku key from $JIMAKU_API_KEY — works in a terminal but NOT under a GUI-launched "
            "(plugin) mpv; run `set-jimaku-key` to store it in the Keychain",
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
        return Check("subminer", "ok", "SubMiner installed but not running (no overlay conflict)")
    return Check("subminer", "ok", "no SubMiner (no overlay conflict)")


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
        "`saitenka-overlay report` to bundle them",
    )


def check_recent_errors(n: int = 5) -> Check:
    if not LOG_PATH.exists():
        return Check("recent-errors", "ok", "no log yet (nothing has failed)")
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:  # pragma: no cover
        return Check("recent-errors", "warn", f"couldn't read log: {e}")
    errs = [ln for ln in lines if re.search(r"\b(ERROR|CRITICAL|WARNING)\b", ln)][-n:]
    if not errs:
        return Check("recent-errors", "ok", "no recent errors in the log")
    return Check("recent-errors", "warn", "recent log errors:\n    " + "\n    ".join(errs))


# --- driver ----------------------------------------------------------------------------------


def run_checks(deck: str = "Saitenka::Mining", model: str = "Lapis") -> Report:
    checks: list[Check] = [
        check_mpv(),
        check_ffmpeg(),
        check_free_threading(),
        check_config(),
        *check_dict_files(),
        check_dict_locations(),
        check_dict_cache(),
        check_sub_auto(),
        check_fonts(),
        check_anki(deck, model),
        check_mpv_ipc(),
        check_plugin(),
        check_subminer_conflict(),
        check_jimaku(),
        check_crashes(),
        check_recent_errors(),
    ]
    return Report(checks)


_GLYPH = {"ok": "\033[32m✓\033[0m", "warn": "\033[33m!\033[0m", "fail": "\033[31m✗\033[0m"}


def print_report(report: Report) -> None:  # pragma: no cover — pure formatting/IO
    print("\033[1;36m[saitenka doctor]\033[0m")
    for c in report.checks:
        print(f"  {_GLYPH.get(c.status, '?')} {c.detail}")
    s = report.counts
    print(
        f"\nSummary: \033[32m{s['ok']} ok\033[0m · "
        f"\033[33m{s['warn']} warn\033[0m · \033[31m{s['fail']} fail\033[0m"
    )
    print("Healthy ✅" if report.exit_code == 0 else "Problems found — see ✗ above ❌")
    if report.exit_code != 0:
        print("Tip: `saitenka-overlay report` bundles this + logs into a zip for a bug report.")
