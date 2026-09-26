"""The default report ships Saitenka's own log without the user's media, cue text or home paths.

Each canary enters through the production path that carries it in the field; the negative control
proves every one of them reaches the bundle once sanitising is off, so an absence is evidence.
"""

from __future__ import annotations

import json
import logging
import zipfile

import pytest
from session_builder import build_session
from util import FakeIPC

from saitenka.app import doctor, log_privacy, logsetup, report
from saitenka.app.jimaku import parse_filename
from saitenka.app.sub_index import load_index
from saitenka.app.subselect import fetch_jimaku_path
from saitenka.app.subtitle_cache import store_subs
from saitenka.app.subtitle_fonts import container_fonts

HOME = "Home-Sigmacanary"
USER = "canaryuser7"
VIDEO = "[Grp] Zetacanary Show - 07 [1080p].mkv"
SUBTITLE = "Omegacanary.ja.srt"
CUE = "Kappacanary 猫が好き"
CANARIES = ("Sigmacanary", USER, "Zetacanary", "Omegacanary", "Kappacanary", "猫が好き")


def _environment(monkeypatch, tmp_path):
    home = tmp_path / HOME
    cache = home / "AppData" / "Local" / "saitenka" / "Cache"
    cache.mkdir(parents=True)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):
        monkeypatch.setenv(name, USER)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(cache))
    monkeypatch.setattr(log_privacy, "_labels", {})
    monkeypatch.setattr(log_privacy, "_snapshot", None)

    class _Doctor:
        def to_json(self):
            windows = rf"C:\Users\{USER}\AppData\Local\saitenka\overlay.toml"
            return {
                "checks": [{"name": "config", "detail": f"{windows} | {home / 'overlay.toml'}"}]
            }

    monkeypatch.setattr(doctor, "run_checks", lambda *_a, **_k: _Doctor())
    return home, cache


def _configure_logging(cache):
    for name in ("saitenka", logsetup.CONSOLE_LOGGER_NAME):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    logsetup.configure_logging(cache / "overlay.log")


def _field_session(home, cache):
    video = home / "Anime" / VIDEO
    video.parent.mkdir()
    video.write_bytes(b"not a real container")
    subtitle = home / "Downloads" / SUBTITLE
    subtitle.parent.mkdir()
    subtitle.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{CUE}\n", encoding="utf-8")

    title, episode = parse_filename(video)
    store_subs(video, title, episode, subtitle, resync=False)
    _hit, status = fetch_jimaku_path(str(video), resync=False)
    logging.getLogger("saitenka.app.subtitle_modes").info("%s", status)

    fonts = cache / "fonts"
    stale = fonts / f"{video.stem}-{video.stat().st_size}-fonts"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text("{not json", encoding="utf-8")
    container_fonts(video, cache_dir=fonts)

    load_index(subtitle)
    load_index(home / "Missing" / "Omegacanary-lost.ass")

    ipc = FakeIPC()
    ipc.props["sub-text"] = CUE
    build_session(ipc).start()


def _bundle(tmp_path) -> dict[str, bytes]:
    archive = report.build_report_bundle(tmp_path / "reports", timestamp="canary")
    with zipfile.ZipFile(archive) as bundle:
        return {name: bundle.read(name) for name in bundle.namelist()}


def _leaks(members: dict[str, bytes]) -> set[str]:
    found = set()
    for canary in CANARIES:
        forms = {canary.encode(), json.dumps(canary)[1:-1].encode()}
        if any(form in data for form in forms for data in members.values()):
            found.add(canary)
    return found


@pytest.mark.timeout(10)
def test_default_report_carries_the_session_log_without_any_canary(monkeypatch, tmp_path):
    home, cache = _environment(monkeypatch, tmp_path)
    _configure_logging(cache)
    _field_session(home, cache)

    members = _bundle(tmp_path)

    assert json.loads(members["logs/collection.json"])["status"] == "collected"
    log_text = members["overlay.log"].decode()
    assert "<media:" in log_text
    assert "<title:" in log_text
    assert "<text:" in log_text
    assert _leaks(members) == set()


@pytest.mark.timeout(10)
def test_every_canary_reaches_the_bundle_when_sanitising_is_off(monkeypatch, tmp_path):
    home, cache = _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(log_privacy, "scrub", lambda text: text)
    monkeypatch.setattr(log_privacy, "text_label", str)
    monkeypatch.setattr(logsetup, "redact", lambda text: text)
    monkeypatch.setattr(report, "redact", lambda text: text)
    _configure_logging(cache)
    _field_session(home, cache)

    members = _bundle(tmp_path)

    assert _leaks(members) == set(CANARIES)
