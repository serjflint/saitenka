"""Stage 12: Tests for app/resync.py — subtitle resync via alass / ffsubsync.

TDD: these tests are written BEFORE the implementation and must fail initially.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: import the module (fails until it exists)
# ---------------------------------------------------------------------------


def _import_resync():
    from overlay.app import resync

    return resync


# ---------------------------------------------------------------------------
# 1. Command-construction tests
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    """resync() builds the right subprocess command."""

    def test_alass_command_when_on_path(self, tmp_path):
        """When alass is on PATH, the command is: alass <video> <srt> <out>."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"

        commands_run: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            commands_run.append(list(cmd))
            return MagicMock(returncode=0)

        with (
            patch("shutil.which", return_value="/usr/local/bin/alass"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = resync.resync(video, src_srt, out_srt)

        assert result == out_srt
        assert len(commands_run) == 1
        assert commands_run[0] == ["alass", str(video), str(src_srt), str(out_srt)]

    def test_ffsubsync_command_when_alass_absent(self, tmp_path):
        """When alass is NOT on PATH, falls back to: uvx ffsubsync <video> -i <srt> -o <out>."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"

        commands_run: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            commands_run.append(list(cmd))
            return MagicMock(returncode=0)

        def which_no_alass(name):
            return None if name == "alass" else f"/usr/bin/{name}"

        with (
            patch("shutil.which", side_effect=which_no_alass),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = resync.resync(video, src_srt, out_srt)

        assert result == out_srt
        assert len(commands_run) == 1
        assert commands_run[0] == [
            "uvx",
            "ffsubsync",
            str(video),
            "-i",
            str(src_srt),
            "-o",
            str(out_srt),
        ]

    def test_neither_tool_raises_resync_unavailable(self, tmp_path):
        """When neither alass nor uvx exists, ResyncUnavailable is raised."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"

        with (
            patch("shutil.which", return_value=None),
            pytest.raises(resync.ResyncUnavailable),
        ):
            resync.resync(video, src_srt, out_srt)


# ---------------------------------------------------------------------------
# 2. Cache / marker behaviour
# ---------------------------------------------------------------------------


class TestCacheMarker:
    """The .synced marker prevents re-running the tool."""

    def test_marker_prevents_rerun(self, tmp_path):
        """If <out>.synced marker exists, resync() returns early without subprocess."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"
        # Pre-populate the output and the marker
        out_srt.write_text("synced content", encoding="utf-8")
        marker = tmp_path / "ep01.synced.srt.synced"
        marker.touch()

        with patch("subprocess.run") as mock_run:
            result = resync.resync(video, src_srt, out_srt)

        mock_run.assert_not_called()
        assert result == out_srt

    def test_marker_written_after_successful_resync(self, tmp_path):
        """After a successful run the .synced marker is created."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"
        marker = tmp_path / "ep01.synced.srt.synced"

        assert not marker.exists()

        with (
            patch("shutil.which", return_value="/usr/local/bin/alass"),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            resync.resync(video, src_srt, out_srt)

        assert marker.exists(), "marker should be created after a successful resync"

    def test_marker_not_written_on_failure(self, tmp_path):
        """If the subprocess fails (non-zero returncode), no marker is written."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"
        marker = tmp_path / "ep01.synced.srt.synced"

        with (
            patch("shutil.which", return_value="/usr/local/bin/alass"),
            patch("subprocess.run", return_value=MagicMock(returncode=1)),
            pytest.raises(resync.ResyncFailed),
        ):
            resync.resync(video, src_srt, out_srt)

        assert not marker.exists()

    def test_timeout_raises_resync_failed(self, tmp_path):
        """subprocess.TimeoutExpired → ResyncFailed (not a crash)."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"

        with (
            patch("shutil.which", return_value="/usr/local/bin/alass"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="alass", timeout=120),
            ),
            pytest.raises(resync.ResyncFailed),
        ):
            resync.resync(video, src_srt, out_srt)


# ---------------------------------------------------------------------------
# 3. maybe_resync — the public wrapper used by jimaku integration
# ---------------------------------------------------------------------------


class TestMaybeResync:
    """maybe_resync(video, srt, *, enabled) respects the enabled flag."""

    def test_disabled_returns_original(self, tmp_path):
        """When enabled=False, the original srt path is returned unchanged."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("original", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            result = resync.maybe_resync(video, src_srt, enabled=False)

        mock_run.assert_not_called()
        assert result == src_srt

    def test_enabled_unavailable_returns_original_without_raising(self, tmp_path):
        """When neither tool exists, maybe_resync returns the original (no crash, no toast crash)."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")

        with patch("shutil.which", return_value=None):
            result = resync.maybe_resync(video, src_srt, enabled=True)

        assert result == src_srt

    def test_enabled_runs_resync_and_returns_synced(self, tmp_path):
        """When enabled=True and alass is available, the synced path is returned."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")

        with (
            patch("shutil.which", return_value="/usr/local/bin/alass"),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            result = resync.maybe_resync(video, src_srt, enabled=True)

        # Result should NOT equal the original
        assert result != src_srt
        # Result should have the synced suffix convention
        assert ".synced" in result.name

    def test_enabled_failed_returns_original(self, tmp_path):
        """When the tool fails, maybe_resync falls back to the original rather than crashing."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")

        with (
            patch("shutil.which", return_value="/usr/local/bin/alass"),
            patch("subprocess.run", return_value=MagicMock(returncode=1)),
        ):
            result = resync.maybe_resync(video, src_srt, enabled=True)

        assert result == src_srt


# ---------------------------------------------------------------------------
# 4. Config: resync field on ReaderOptions / config schema
# ---------------------------------------------------------------------------


class TestConfigResyncField:
    """The config schema has a resync bool that defaults to True for jimaku-sourced subs."""

    def test_reader_options_has_resync_field(self):
        """ReaderOptions gains a resync field (via a new sub-dataclass or directly)."""
        from overlay.app.config import ReaderOptions

        # The field should exist and default to True
        opts = ReaderOptions()
        assert hasattr(opts, "resync") or hasattr(opts.mining, "resync"), (
            "ReaderOptions should expose a 'resync' flag"
        )
