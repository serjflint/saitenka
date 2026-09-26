"""The default report ships Saitenka's own log and trace without the user's media names or home.

Each canary enters through the production path that carries it in the field; the negative control
proves every one of them reaches the bundle once sanitising is off, so an absence is evidence.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

import pytest
from session_builder import build_session
from telemetry_helpers import enable_telemetry
from util import FakeIPC

from saitenka.app import doctor, log_privacy, logsetup, report, telemetry
from saitenka.app.jimaku import JimakuClient, JimakuFile, parse_filename
from saitenka.app.sub_index import load_index
from saitenka.app.subselect import (
    AttachSubtitleOptions,
    fetch_jimaku_path,
    prepare_attach_startup,
)
from saitenka.app.subtitle_cache import store_subs
from saitenka.app.subtitle_fonts import container_fonts

HOME = "Home-Sigmacanary"
USER = "canaryuser7"
SERIES_DIR = "Thetacanary Season 1"
VIDEO = "[Grp] Zetacanary Show - 07 [1080p].mkv"
SUBTITLE = "Omegacanary.ja.srt"
CUE = "Kappacanary 猫が好き"
#: Written by an older build; it must not reach the bundle through doctor's recent-errors.
OLD_LINE = "Lambdacanary.ja.srt"
#: A release name a provider picked, which reaches a trace attribute.
PICKED = "Iotacanary.WEBRip.ja.srt"
CANARIES = (
    "Sigmacanary",
    USER,
    "Zetacanary",
    "Thetacanary",
    "Omegacanary",
    "Lambdacanary",
    "Iotacanary",
    "Muattach",
    "Nuattach",
)


@pytest.fixture
def fresh_registry():
    log_privacy.reset()
    yield
    log_privacy.reset()


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
    old = {"level": "error", "event": f"cannot read {OLD_LINE}", "session": "old", "log_format": 1}
    (cache / "overlay.log").write_text(json.dumps(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "LOG_PATH", cache / "overlay.log")
    windows = rf"C:\Users\{USER}\AppData\Local\saitenka\overlay.toml"
    home_check = doctor.Check("config", "ok", f"{windows} | {home / 'overlay.toml'}")
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda *_a, **_k: doctor.Report([home_check, doctor.check_recent_errors()]),
    )
    config = tmp_path / "telemetry.toml"
    config.write_text(
        f'[telemetry]\nenabled = true\nexport_dir = "{(tmp_path / "trace").as_posix()}"\n'
    )
    monkeypatch.setenv("SAITENKA_CONFIG", str(config))
    enable_telemetry(monkeypatch, tmp_path)

    def download(_client, jf, dest_dir):
        return Path(dest_dir) / jf.name

    monkeypatch.setattr(
        JimakuClient, "episode_files", lambda *_a, **_k: [JimakuFile(PICKED, "https://x")]
    )
    monkeypatch.setattr(JimakuClient, "download", download)
    return home, cache


def _configure_logging(cache):
    for name in ("saitenka", logsetup.CONSOLE_LOGGER_NAME):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
    logsetup.configure_logging(cache / "overlay.log")


def _field_session(home, cache):
    video = home / "Anime" / SERIES_DIR / VIDEO
    video.parent.mkdir(parents=True)
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
    load_index(video.parent / "Omegacanary-beside.ass")

    ipc = FakeIPC()
    ipc.props["sub-text"] = CUE
    build_session(ipc).start()
    JimakuClient("k" * 32).fetch("Show", 7, cache, video=str(video))
    _attach_legs(home, cache)
    telemetry.shutdown()


def _attach_legs(home, cache):
    """`attach` resolves the file mpv already plays, so nothing upstream registered it."""
    attached = home / "Muattach Series" / "Muattach Show - 03.mkv"
    attached.parent.mkdir()
    attached.write_bytes(b"not a real container")
    log = logging.getLogger("saitenka.app.commands.attach")

    embedded = FakeIPC()
    embedded.props["path"] = str(attached)
    embedded.props["track-list"] = [{"type": "sub", "id": 1, "lang": "jpn"}]
    _startup, status, _providers = prepare_attach_startup(embedded, AttachSubtitleOptions())
    log.info("attach subs: %s", status)
    fonts = cache / "attach-fonts"
    stale = fonts / f"{attached.stem}-{attached.stat().st_size}-fonts"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text("{not json", encoding="utf-8")
    container_fonts(attached, cache_dir=fonts)

    explicit = FakeIPC()
    explicit.props["path"] = str(attached)
    sub_file = home / "Nuattach.ja.ass"
    _startup, status, _providers = prepare_attach_startup(
        explicit, AttachSubtitleOptions(sub_file=str(sub_file))
    )
    log.info("attach subs: %s", status)


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
@pytest.mark.usefixtures("fresh_registry")
def test_default_report_carries_the_session_log_without_any_canary(monkeypatch, tmp_path):
    home, cache = _environment(monkeypatch, tmp_path)
    _configure_logging(cache)
    _field_session(home, cache)

    members = _bundle(tmp_path)

    assert json.loads(members["logs/collection.json"])["status"] == "collected"
    assert "telemetry/trace.json" in members
    log_text = members["overlay.log"].decode()
    assert "<media:" in log_text
    assert "<title:" in log_text
    # Cue text is kept by policy: it is what a tokenization bug is diagnosed from.
    assert "Kappacanary" in log_text
    assert _leaks(members) == set()


@pytest.mark.timeout(10)
@pytest.mark.usefixtures("fresh_registry")
def test_every_canary_reaches_the_bundle_when_sanitising_is_off(monkeypatch, tmp_path):
    home, cache = _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(log_privacy, "scrub", lambda text: text)
    monkeypatch.setattr(log_privacy, "media_label", str)
    monkeypatch.setattr(logsetup, "redact", lambda text: text)
    monkeypatch.setattr(report, "redact", lambda text: text)
    monkeypatch.setattr(report, "_without_log_excerpts", lambda text: text)
    _configure_logging(cache)
    _field_session(home, cache)

    members = _bundle(tmp_path)

    assert _leaks(members) == set(CANARIES)
