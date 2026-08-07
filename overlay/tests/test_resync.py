"""Stage 12: Tests for app/resync.py — subtitle resync via alass / ffsubsync.

TDD: these tests are written BEFORE the implementation and must fail initially.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class _RecordingSpan:
    """A SpanSetter stand-in that captures ``.set(key, value)`` so a test can assert exactly which
    attributes a span carried — the report's structured payload, without a real OTel provider."""

    def __init__(self) -> None:
        self.attrs: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self.attrs[key] = value


def _patch_recording_traced(monkeypatch) -> list[_RecordingSpan]:
    """Swap ``otel_metrics.traced`` for a recorder; return the list the spans land in (order = open)."""
    from overlay import otel_metrics

    recorded: list[_RecordingSpan] = []

    @contextmanager
    def _traced(_name, **_attrs):
        span = _RecordingSpan()
        recorded.append(span)
        yield span

    monkeypatch.setattr(otel_metrics, "traced", _traced)
    return recorded


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

    @staticmethod
    def _run_with_tools(resync, tmp_path, available: dict[str, str]):
        """Run resync() with a stubbed tool-resolver (``find_tool``) and capture the command it runs.
        ``available`` maps a binary name → its resolved path; anything absent resolves to None."""
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"
        commands_run: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            commands_run.append(list(cmd))
            return MagicMock(returncode=0)

        with (
            patch("overlay.mpvio.discover.find_tool", side_effect=available.get),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = resync.resync(video, src_srt, out_srt)
        return result, out_srt, commands_run, (video, src_srt, out_srt)

    def test_alass_command_when_on_path(self, tmp_path):
        """alass wins over ffsubsync; the resolved binary path is used verbatim."""
        resync = _import_resync()
        result, out, cmds, (video, src, out_srt) = self._run_with_tools(
            resync, tmp_path, {"alass": "/usr/local/bin/alass"}
        )
        assert result == out
        assert cmds == [["/usr/local/bin/alass", str(video), str(src), str(out_srt)]]

    def test_alass_cli_binary_is_detected(self, tmp_path):
        """brew installs the binary as ``alass-cli`` (not ``alass``) — it must still be found, else a
        working alass install silently loses to ffsubsync's single-offset sync (the live timing bug)."""
        resync = _import_resync()
        result, out, cmds, (video, src, out_srt) = self._run_with_tools(
            resync, tmp_path, {"alass-cli": "/opt/homebrew/bin/alass-cli"}
        )
        assert result == out
        assert cmds == [["/opt/homebrew/bin/alass-cli", str(video), str(src), str(out_srt)]]

    def test_ffsubsync_command_when_alass_absent(self, tmp_path):
        """With no alass/alass-cli, falls back to: uvx ffsubsync <video> -i <srt> -o <out>."""
        resync = _import_resync()
        _result, _out, cmds, (video, src, out_srt) = self._run_with_tools(
            resync, tmp_path, {"uvx": "/usr/bin/uvx"}
        )
        assert cmds == [
            ["/usr/bin/uvx", "ffsubsync", str(video), "-i", str(src), "-o", str(out_srt)]
        ]

    def test_prefers_an_embedded_subtitle_as_the_alignment_reference(self, tmp_path, monkeypatch):
        """A co-timed embedded sub (exact-encode timing) is aligned TO, not the video's audio — the
        deterministic sub-to-sub path that fixes a different-broadcast source (live: 30s → dead-on)."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src = tmp_path / "ep01.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nT\n", encoding="utf-8")
        ref = tmp_path / "reference.srt"
        ref.write_text("1\n00:00:05,000 --> 00:00:06,000\nEN\n", encoding="utf-8")
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: ref)
        cmds: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            cmds.append(list(cmd))
            return MagicMock(returncode=0)

        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get),
            patch("subprocess.run", side_effect=fake_run),
        ):
            resync.resync(video, src, tmp_path / "out.srt")
        assert cmds[0][1] == str(ref)  # aligned to the embedded reference, not the video
        assert cmds[0][1] != str(video)

    def test_falls_back_to_the_video_audio_when_no_embedded_reference(self, tmp_path, monkeypatch):
        """The complement of the embedded-reference cell: with no usable embedded text sub, resync
        aligns to the VIDEO (audio-based) — the fallback must stay wired, not silently drop to a
        no-reference command."""
        resync = _import_resync()
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: None)
        _result, _out, cmds, (video, src, out_srt) = self._run_with_tools(
            resync, tmp_path, {"alass": "/b/alass"}
        )
        assert cmds[0] == ["/b/alass", str(video), str(src), str(out_srt)]  # aligned to the video

    @pytest.mark.parametrize(
        ("available", "head"),
        [
            ({"alass": "/a", "alass-cli": "/ac", "uvx": "/u"}, "/a"),  # alass wins outright
            ({"alass-cli": "/ac", "uvx": "/u"}, "/ac"),  # brew's alass-cli beats ffsubsync
            ({"uvx": "/u"}, "/u"),  # only ffsubsync left
        ],
    )
    def test_tool_precedence_matrix(self, tmp_path, available, head):
        """Every cell of the aligner-selection matrix: alass > alass-cli > uvx ffsubsync. No embedded
        tools present (ffprobe/ffmpeg absent) → audio-reference command, so the head is the aligner."""
        resync = _import_resync()
        _result, _out, cmds, _paths = self._run_with_tools(resync, tmp_path, available)
        assert cmds[0][0] == head

    @pytest.mark.parametrize(
        ("codec", "suffix", "codec_args"),
        [
            (
                "ass",
                ".ass",
                ["-c:s", "copy"],
            ),  # native ASS — alass parses it; srt-convert injects tags
            ("ssa", ".ass", ["-c:s", "copy"]),
            ("subrip", ".srt", ["-c:s", "copy"]),  # already SubRip — copy verbatim
            ("mov_text", ".srt", ["-c:s", "srt"]),  # convert (clean for these codecs)
            ("webvtt", ".srt", ["-c:s", "srt"]),
            ("", ".srt", ["-c:s", "srt"]),  # unknown → safe default
        ],
    )
    def test_reference_extract_spec_keeps_ass_native(self, codec, suffix, codec_args):
        """An ASS embedded track is extracted as .ass (copied), NOT converted to srt — ffmpeg's srt
        conversion injects <font>/<b> tags that make alass-cli exit 1 (the ep02-late root cause)."""
        resync = _import_resync()
        assert resync._reference_extract_spec(codec) == (suffix, codec_args)

    def test_split_penalty_adds_the_alass_flag(self):
        """resync_split_penalty threads to alass's --split-penalty (lower → splits more at the OP)."""
        resync = _import_resync()
        with patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get):
            cmd, tool = resync._resync_command(
                Path("/ref.ass"), Path("/s.srt"), Path("/o.srt"), split_penalty=3
            )
        assert cmd == ["/b/alass", "--split-penalty", "3", "/ref.ass", "/s.srt", "/o.srt"]
        assert tool == "alass"

    def test_split_penalty_omitted_when_unset(self):
        resync = _import_resync()
        with patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get):
            cmd, _tool = resync._resync_command(Path("/ref.ass"), Path("/s.srt"), Path("/o.srt"))
        assert "--split-penalty" not in cmd  # None → alass built-in default

    def test_split_penalty_ignored_by_ffsubsync(self):
        resync = _import_resync()
        with patch("overlay.mpvio.discover.find_tool", side_effect={"uvx": "/b/uvx"}.get):
            cmd, _tool = resync._resync_command(
                Path("/v.mkv"), Path("/s.srt"), Path("/o.srt"), split_penalty=3
            )
        assert "--split-penalty" not in cmd  # ffsubsync has no such knob

    def test_failure_message_surfaces_alass_stdout(self, tmp_path):
        """alass-cli writes its parse error to STDOUT — the ResyncFailed message must include it, or the
        failure is a blank 'exit 1' (which is exactly what hid the unparseable reference live)."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src = tmp_path / "ep01.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nT\n", encoding="utf-8")
        run = MagicMock(returncode=1, stdout=b"error: parse error at line 1164", stderr=b"")
        with (
            patch(
                "overlay.mpvio.discover.find_tool", side_effect={"alass-cli": "/b/alass-cli"}.get
            ),
            patch("subprocess.run", return_value=run),
            pytest.raises(resync.ResyncFailed, match="parse error at line 1164"),
        ):
            resync.resync(video, src, tmp_path / "out.srt")

    def test_neither_tool_raises_resync_unavailable(self, tmp_path):
        """When neither alass/alass-cli nor uvx exists, ResyncUnavailable is raised."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src_srt = tmp_path / "ep01.srt"
        src_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        out_srt = tmp_path / "ep01.synced.srt"

        with (
            patch("overlay.mpvio.discover.find_tool", return_value=None),
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
            patch("subprocess.run", return_value=MagicMock(returncode=1, stdout=b"", stderr=b"")),
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
            patch("subprocess.run", return_value=MagicMock(returncode=1, stdout=b"", stderr=b"")),
        ):
            result = resync.maybe_resync(video, src_srt, enabled=True)

        assert result == src_srt


class TestResyncTelemetry:
    """The shift a resync applied is telemetry: a ~0 offset (tool no-op / already aligned) must be
    distinguishable from a real sync, since a silent fallback-to-raw ships mistimed subs (#100 live)."""

    def test_first_cue_shift_ms_measures_the_applied_offset(self, tmp_path):
        resync = _import_resync()
        src = tmp_path / "raw.srt"
        out = tmp_path / "raw.synced.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nT\n", encoding="utf-8")
        out.write_text("1\n00:00:03,500 --> 00:00:04,500\nT\n", encoding="utf-8")
        assert resync._first_cue_shift_ms(src, out) == 2500

    def test_first_cue_shift_ms_is_none_when_unreadable_or_cueless(self, tmp_path):
        resync = _import_resync()
        src = tmp_path / "raw.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nT\n", encoding="utf-8")
        assert (
            resync._first_cue_shift_ms(src, tmp_path / "missing.srt") is None
        )  # fail-soft, no raise
        cueless = tmp_path / "cueless.srt"
        cueless.write_text("no timestamps here", encoding="utf-8")
        assert resync._first_cue_shift_ms(src, cueless) is None

    def test_maybe_resync_logs_the_shift_it_applied(self, tmp_path, caplog, monkeypatch):
        # A successful sync must surface the offset it moved the subs by — the number that would have
        # revealed the ep03 no-op (ffsubsync "offset seconds: 0.000", raw shipped as "synced").
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src = tmp_path / "ep01.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nT\n", encoding="utf-8")

        def fake_run(cmd, **_kw):
            Path(cmd[-1]).write_text(  # the tool writes its shifted output to the final arg
                "1\n00:00:03,500 --> 00:00:04,500\nT\n", encoding="utf-8"
            )
            return MagicMock(returncode=0, stderr=b"")

        monkeypatch.setattr(
            resync.shutil, "which", lambda t: "/usr/bin/alass" if t == "alass" else None
        )
        monkeypatch.setattr(resync.subprocess, "run", fake_run)
        with caplog.at_level("INFO"):
            result = resync.maybe_resync(video, src, enabled=True)
        assert result.name.endswith(".synced.srt")
        assert "+2500ms" in caplog.text  # the applied shift is visible in the log

    def test_cue_starts_ms_fingerprints_the_first_k_cue_starts(self, tmp_path):
        resync = _import_resync()
        p = tmp_path / "a.srt"
        p.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:03,500 --> 00:00:04,000\nB\n",
            encoding="utf-8",
        )
        assert resync._cue_starts_ms(p) == [1000, 3500]  # integers only — no cue text
        assert resync._cue_starts_ms(p, 1) == [1000]  # bounded to k
        assert resync._cue_starts_ms(tmp_path / "missing.srt") == []  # fail-soft

    def test_cue_starts_ms_parses_an_ass_reference(self, tmp_path):
        """The embedded reference is extracted as native .ass — the fingerprint must read ASS Dialogue
        start times (centiseconds → ms), else ref_cue_ms is silently blank for every embedded sync."""
        resync = _import_resync()
        p = tmp_path / "reference.ass"
        p.write_text(
            "[Events]\nFormat: Layer, Start, End, Style, Text\n"
            "Dialogue: 0,0:00:41.41,0:00:42.05,D,Ah\n"
            "Dialogue: 0,0:00:44.44,0:00:46.94,D,Hm\n",
            encoding="utf-8",
        )
        assert resync._cue_starts_ms(p) == [41410, 44440]  # cc×10 → ms

    def test_resync_reports_tool_and_reference_and_ref_fingerprint_in_details(
        self, tmp_path, monkeypatch
    ):
        """The out-param the span reads: the aligner, the reference KIND, and the reference's cue
        fingerprint — captured before the embedded-ref workdir is cleaned up."""
        resync = _import_resync()
        video = tmp_path / "ep01.mkv"
        video.touch()
        src = tmp_path / "ep01.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nJP\n", encoding="utf-8")
        ref = tmp_path / "reference.eng.srt"  # reference.<lang>.<ext> → language is recoverable
        ref.write_text("1\n00:00:08,000 --> 00:00:09,000\nEN\n", encoding="utf-8")
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: ref)
        details: dict = {}
        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            resync.resync(video, src, tmp_path / "out.srt", details=details, split_penalty=5)
        assert details == {
            "tool": "alass",
            "reference": "embedded",
            "reference_fmt": ".srt",
            "reference_lang": "eng",
            "split_penalty": 5,
            "ref_cue_ms": [8000],
        }

    def test_pick_reference_stream_prefers_english_over_a_fuller_other_language(self):
        """The reference is the ENGLISH dialogue track (what the top-subs show), not whatever ties for
        'fullest' — a real release tied por/ita at 278 cues and we were aligning to Portuguese."""
        resync = _import_resync()
        streams = [
            {
                "index": 2,
                "codec_name": "ass",
                "tags": {"language": "eng", "NUMBER_OF_FRAMES": "271"},
            },
            {
                "index": 3,
                "codec_name": "ass",
                "tags": {"language": "por", "NUMBER_OF_FRAMES": "278"},
            },
            {
                "index": 9,
                "codec_name": "ass",
                "tags": {"language": "ita", "NUMBER_OF_FRAMES": "278"},
            },
        ]
        assert (
            resync._pick_reference_stream(streams)["index"] == 2
        )  # eng, though por/ita have more cues

    def test_pick_reference_stream_falls_back_to_fullest_without_english(self):
        resync = _import_resync()
        streams = [
            {
                "index": 3,
                "codec_name": "ass",
                "tags": {"language": "por", "NUMBER_OF_FRAMES": "150"},
            },
            {
                "index": 9,
                "codec_name": "ass",
                "tags": {"language": "ita", "NUMBER_OF_FRAMES": "278"},
            },
        ]
        assert resync._pick_reference_stream(streams)["index"] == 9  # no English → fullest (ita)

    def test_span_carries_trigger_outcome_and_cue_fingerprints(self, tmp_path, monkeypatch):
        """A report can attribute each Ctrl+Shift+T press (trigger=retry) and replay the alignment from
        the src/out fingerprints — a no-op would show as identical src_cue_ms/out_cue_ms."""
        resync = _import_resync()
        recorded = _patch_recording_traced(monkeypatch)
        video = tmp_path / "ep01.mkv"
        video.touch()
        src = tmp_path / "ep01.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nJP\n", encoding="utf-8")

        def fake_run(cmd, **_kw):
            Path(cmd[-1]).write_text(  # the aligner shifts the cue by +2.5s
                "1\n00:00:03,500 --> 00:00:04,500\nJP\n", encoding="utf-8"
            )
            return MagicMock(returncode=0, stderr=b"")

        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: None)  # audio ref
        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get),
            patch("subprocess.run", side_effect=fake_run),
        ):
            resync.maybe_resync(video, src, enabled=True, trigger="retry")
        (span,) = recorded
        assert span.attrs["trigger"] == "retry"
        assert span.attrs["outcome"] == "synced"
        assert span.attrs["tool"] == "alass"
        assert span.attrs["reference"] == "audio"  # no embedded track → aligned to the video
        assert span.attrs["src_cue_ms"] == [1000]
        assert span.attrs["out_cue_ms"] == [3500]  # ≠ src ⇒ a real shift, not a silent no-op

    def test_span_marks_a_silent_no_op_when_out_equals_src(self, tmp_path, monkeypatch):
        """The negative control for the fingerprint oracle: an aligner that copies src through unchanged
        (ffsubsync's 0.000-offset no-op) leaves out_cue_ms == src_cue_ms — the report signal for the
        class that shipped mistimed subs as 'synced'."""
        resync = _import_resync()
        recorded = _patch_recording_traced(monkeypatch)
        video = tmp_path / "ep01.mkv"
        video.touch()
        src = tmp_path / "ep01.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nJP\n", encoding="utf-8")

        def fake_run(cmd, **_kw):
            Path(cmd[-1]).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")  # no shift
            return MagicMock(returncode=0, stderr=b"")

        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: None)
        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"uvx": "/b/uvx"}.get),
            patch("subprocess.run", side_effect=fake_run),
        ):
            resync.maybe_resync(video, src, enabled=True)
        (span,) = recorded
        assert span.attrs["tool"] == "uvx ffsubsync"
        assert span.attrs["trigger"] == "auto"
        assert span.attrs["src_cue_ms"] == span.attrs["out_cue_ms"] == [1000]  # no-op detectable

    def test_failed_span_carries_the_aligner_error_and_reference_format(
        self, tmp_path, monkeypatch
    ):
        """A failed resync span records WHY (the aligner's own stdout) and the reference FORMAT — the
        exact pair that root-caused ep02 (alass-cli exit 1 parsing an srt-converted .ass reference)."""
        resync = _import_resync()
        recorded = _patch_recording_traced(monkeypatch)
        video = tmp_path / "ep02.mkv"
        video.touch()
        src = tmp_path / "ep02.srt"
        src.write_text("1\n00:00:06,106 --> 00:00:16,116\nJP\n", encoding="utf-8")
        ref = tmp_path / "reference.ass"
        ref.write_text("[Events]\nDialogue: 0,0:00:41.41,0:00:42.05,D,Ah\n", encoding="utf-8")
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: ref)
        run = MagicMock(returncode=1, stdout=b"error: parse error at line 1164", stderr=b"")
        with (
            patch(
                "overlay.mpvio.discover.find_tool", side_effect={"alass-cli": "/b/alass-cli"}.get
            ),
            patch("subprocess.run", return_value=run),
        ):
            resync.maybe_resync(video, src, enabled=True, trigger="retry")
        (span,) = recorded
        assert span.attrs["outcome"] == "failed"
        assert "parse error at line 1164" in span.attrs["fail_reason"]
        assert span.attrs["reference"] == "embedded"
        assert span.attrs["reference_fmt"] == ".ass"
        assert span.attrs["ref_cue_ms"] == [41410]  # the reference fingerprint, even on failure


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


class TestResyncWindow:
    """resync_window re-times only the segment from the playhead onward — for a source that DRIFTS
    across the episode, where one whole-file offset can't be right both before and after the OP."""

    @staticmethod
    def _fake_aligner(resync, delta_s: float):
        """A fake alass that shifts every windowed cue by *delta_s* (what a real align would compute)."""
        from overlay.app.sub_index import SubCue, parse_srt

        def fake_run(cmd, **_kw):
            inp, outp = Path(cmd[-2]), Path(cmd[-1])
            cues = parse_srt(inp.read_text(encoding="utf-8"))
            resync._write_srt(
                outp, [SubCue(c.start + delta_s, c.end + delta_s, c.text) for c in cues]
            )
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        return fake_run

    def _sub_with_cues(self, tmp_path, starts):
        sub = tmp_path / "ep02.ja.srt"
        from overlay.app.resync import _write_srt
        from overlay.app.sub_index import SubCue

        _write_srt(sub, [SubCue(s, s + 1.0, "JP") for s in starts])
        return sub

    def test_shifts_only_cues_from_the_playhead_window_leaving_earlier_cues(
        self, tmp_path, monkeypatch
    ):
        resync = _import_resync()
        sub = self._sub_with_cues(tmp_path, [1.0, 5.0, 40.0, 50.0, 60.0])
        ref = tmp_path / "reference.eng.ass"
        ref.write_text("[Events]\nDialogue: 0,0:00:40.00,0:00:41.00,D,x\n", encoding="utf-8")
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: ref)
        # playhead 45s, lookback 20 → boundary 25s → window = [40, 50, 60]; aligner pulls them 3s earlier
        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get),
            patch("subprocess.run", side_effect=self._fake_aligner(resync, -3.0)),
        ):
            out = resync.resync_window(tmp_path / "ep02.mkv", sub, start_s=45.0, lookback_s=20.0)
        assert out is not None and out.name == "ep02.ja.win.srt"
        # head cues untouched (1,5); windowed tail shifted −3s (40→37, 50→47, 60→57)
        assert resync._cue_starts_ms(out) == [1000, 5000, 37000, 47000, 57000]

    def test_returns_sub_unchanged_when_the_window_is_already_aligned(self, tmp_path, monkeypatch):
        resync = _import_resync()
        sub = self._sub_with_cues(tmp_path, [1.0, 40.0, 50.0])
        ref = tmp_path / "reference.eng.ass"
        ref.write_text("[Events]\nDialogue: 0,0:00:40.00,0:00:41.00,D,x\n", encoding="utf-8")
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: ref)
        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get),
            patch("subprocess.run", side_effect=self._fake_aligner(resync, 0.0)),  # zero offset
        ):
            out = resync.resync_window(tmp_path / "ep02.mkv", sub, start_s=45.0)
        assert out == sub  # no net shift → keep current subs, distinct from a hard failure (None)

    def test_returns_none_when_the_window_has_too_few_cues(self, tmp_path):
        resync = _import_resync()
        sub = self._sub_with_cues(tmp_path, [1.0, 5.0])  # nothing at/after the playhead window
        out = resync.resync_window(tmp_path / "ep02.mkv", sub, start_s=200.0, lookback_s=20.0)
        assert out is None  # hard failure → caller falls back to a whole-file re-sync

    def test_span_records_the_window_delta_and_trigger(self, tmp_path, monkeypatch):
        resync = _import_resync()
        recorded = _patch_recording_traced(monkeypatch)
        sub = self._sub_with_cues(tmp_path, [1.0, 40.0, 50.0, 60.0])
        ref = tmp_path / "reference.eng.ass"
        ref.write_text("[Events]\nDialogue: 0,0:00:40.00,0:00:41.00,D,x\n", encoding="utf-8")
        monkeypatch.setattr(resync, "_embedded_sub_reference", lambda _v, _w: ref)
        with (
            patch("overlay.mpvio.discover.find_tool", side_effect={"alass": "/b/alass"}.get),
            patch("subprocess.run", side_effect=self._fake_aligner(resync, -3.0)),
        ):
            resync.resync_window(tmp_path / "ep02.mkv", sub, start_s=45.0, lookback_s=20.0)
        (span,) = recorded
        assert span.attrs["trigger"] == "window"
        assert span.attrs["outcome"] == "synced"
        assert span.attrs["window_cues"] == 3  # 40, 50, 60
        assert span.attrs["window_delta_ms"] == -3000
        assert span.attrs["reference_lang"] == "eng"
