"""Subtitle resync via alass (preferred) or ffsubsync (fallback via uvx).

Flow:
  1. Check for a ``<out>.synced`` marker — if present the file was already synced, skip.
  2. Try ``alass``/``alass-cli`` (on PATH); if absent try ``uvx ffsubsync``.
  3. On success, write the marker so the next run skips the tool.
  4. On failure (non-zero exit, timeout, tool absent) raise the appropriate exception so the
     caller can toast + fall back gracefully.

The public entry point for the jimaku path is :func:`maybe_resync` — it swallows
:exc:`ResyncUnavailable` and :exc:`ResyncFailed` and returns the original path, matching the
"graceful fallback" requirement.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import statistics
import subprocess
from typing import TYPE_CHECKING

from saitenka.app import subtitle_artifact

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

# Text subtitle codecs we can extract as an alignment reference (image subs — pgs/dvdsub — can't align).
_TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}


# The reference language, in preference order: the ENGLISH dialogue track is what the overlay already
# shows as top-subs and is present in virtually every multi-sub release — a deterministic, meaningful
# choice, unlike "the fullest text track" which ties arbitrarily to whatever language ffprobe lists
# first (a real release tied English/Italian/Portuguese at 278 cues → we were aligning to Portuguese).
_REF_PREFER_LANGS = ("eng", "en")


def _cue_count(s: dict) -> int:
    try:
        return int(s.get("tags", {}).get("NUMBER_OF_FRAMES", 0))
    except (TypeError, ValueError):
        return 0


def _pick_reference_stream(text: list[dict]) -> dict:
    """Choose the embedded text track to align against: the fullest ENGLISH track (matching the
    top-subs), or — when the release carries no English — the fullest track overall (most cues ⇒ a
    dialogue track, not a signs/songs one). All candidate tracks are co-timed against this encode, so
    the choice is about determinism + consistency with what's displayed, not timing per se."""
    for want in _REF_PREFER_LANGS:
        matches = [
            s for s in text if str(s.get("tags", {}).get("language", "")).lower().startswith(want)
        ]
        if matches:
            return max(matches, key=_cue_count)
    return max(text, key=_cue_count)


def _embedded_sub_reference(
    video: Path, workdir: Path, *, details: dict | None = None
) -> Path | None:
    """Extract a co-timed EMBEDDED subtitle track from *video* to use as the alignment reference.

    A multi-sub release (e.g. Crunchyroll's en/pt/es/… tracks) carries subtitles authored against THIS
    exact encode, so their cue timing is ground truth. Aligning the fetched JP subs to one of them is
    sub-to-sub — deterministic, and free of the audio-VAD / framerate guesswork that mistimes a
    different-broadcast source (found live: an AT-X rip was 30s out; via the embedded track it lands
    dead-on). Prefers the ENGLISH track the overlay already shows as top-subs (see
    :func:`_pick_reference_stream`). The extracted file is named ``reference.<lang>.<ext>`` so the log +
    telemetry show which track drove the sync. Returns the path in *workdir*, or None when there are no
    embedded text subs / the tools are missing.

    Records WHY it returned None into ``details['embedded_ref']`` (``tools-missing`` / ``probe-failed`` /
    ``no-text-subs`` / ``extract-failed`` / ``ok``) so a report shows why resync fell back to audio VAD —
    the alass "failed to extract voice segments" path, useless on some encodes. ``tools-missing`` is the
    attach-minimal-PATH signature (ffprobe/ffmpeg unresolved) vs a genuinely subs-less file."""
    from saitenka.mpvio.discover import find_tool

    def _note(reason: str) -> None:
        if details is not None:
            details["embedded_ref"] = reason

    ffprobe, ffmpeg = find_tool("ffprobe"), find_tool("ffmpeg")
    if not ffprobe or not ffmpeg:
        _note("tools-missing")
        log.info(
            "resync: no embedded reference — ffprobe=%s ffmpeg=%s not both on PATH "
            "(a minimal attach PATH is the usual cause); resync will fall back to audio VAD",
            bool(ffprobe),
            bool(ffmpeg),
        )
        return None

    # Fully fail-soft: a missing tool, an odd container, or a probe hiccup just means "no reference" —
    # resync falls back to the video's audio. It must never raise into the resync path.
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "s", "-show_entries",
             "stream=index,codec_name:stream_tags=language,NUMBER_OF_FRAMES", "-of", "json", str(video)],
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
        )  # fmt: skip
        if probe.returncode != 0:
            _note("probe-failed")
            return None
        text = [
            s
            for s in json.loads(probe.stdout).get("streams", [])
            if s.get("codec_name") in _TEXT_SUB_CODECS
        ]
        if not text:
            _note("no-text-subs")
            return None
        chosen = _pick_reference_stream(text)
        lang = str(chosen.get("tags", {}).get("language", "") or "und").lower()
        suffix, codec_args = subtitle_artifact.extract_spec(chosen.get("codec_name", ""))
        ref = workdir / f"reference.{lang}{suffix}"
        extracted = subprocess.run(
            [ffmpeg, "-y", "-i", str(video), "-map", f"0:{chosen['index']}",
             *codec_args, str(ref)],
            capture_output=True, check=False, timeout=120,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        log.debug("embedded-sub reference extraction failed", exc_info=True)
        _note("extract-failed")
        return None
    if extracted.returncode == 0 and ref.exists():
        _note("ok")
        return ref
    _note("extract-failed")
    return None


_BOM = "﻿"  # UTF-8 byte-order mark some jimaku sources prepend


def _looks_ass(text: str) -> bool:
    """ASS/SSA content sniff. Some jimaku sources ship an ASS body under a ``.srt`` name (often + a BOM);
    alass keys the format off the EXTENSION and rejects it as SubRip ('parse error at line 0'), and
    ``_parse_cues`` would mis-route it to the SRT parser. Detect by content, not the filename."""
    return text.lstrip(_BOM).lstrip().startswith(("[Script Info]", "[V4", "[Events]"))


_CUE_START = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->")  # SRT: HH:MM:SS,mmm -->
_ASS_START = re.compile(
    r"(?m)^Dialogue:[^,]*,(\d+):(\d\d):(\d\d)\.(\d\d)"
)  # ASS Dialogue start, cc


def _cue_starts_ms(path: Path, k: int = 8) -> list[int]:
    """First up-to-*k* cue start times (ms) — a compact, TEXT-FREE timing fingerprint of a subtitle
    file. Integers only: safe to put in a shared trace (no copyrighted cue text) and enough to see the
    shape of an alignment — a no-op shows as src==out while the reference differs — and to replay the
    case as an offline regression from the vectors alone. Handles SRT and ASS (the native format an
    embedded reference is extracted in). Fail-soft: unreadable/cueless → []."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    starts: list[int] = []
    for h, m, s, ms in (map(int, g.groups()) for g in _CUE_START.finditer(text)):
        starts.append(((h * 60 + m) * 60 + s) * 1000 + ms)
        if len(starts) >= k:
            return starts
    if starts:
        return starts
    for h, m, s, cs in (map(int, g.groups()) for g in _ASS_START.finditer(text)):  # cc → ms (×10)
        starts.append(((h * 60 + m) * 60 + s) * 1000 + cs * 10)
        if len(starts) >= k:
            break
    return starts


def _first_cue_start_ms(path: Path) -> int | None:
    """Start time (ms) of a subtitle file's first cue, or None if it has none / can't be read — the
    anchor for the shift a resync applied. Fail-soft: this feeds telemetry only, never gates resync."""
    starts = _cue_starts_ms(path, 1)
    return starts[0] if starts else None


def _first_cue_shift_ms(src: Path, out: Path) -> int | None:
    """How far resync moved the first cue (out − src, ms). ~0 means the tool computed no offset — subs
    already aligned OR a silent VAD no-op — the difference between right and mistimed subs, so it's
    worth surfacing rather than hiding behind an indistinguishable 'synced' result."""
    a, b = _first_cue_start_ms(src), _first_cue_start_ms(out)
    return None if a is None or b is None else b - a


class ResyncUnavailable(RuntimeError):
    """Neither alass nor uvx is on PATH — resync cannot be performed."""


class ResyncFailed(RuntimeError):
    """The resync tool ran but returned a non-zero exit code or timed out."""


def _marker(out: Path) -> Path:
    """Path of the cache marker for ``out``."""
    return out.with_suffix(out.suffix + ".synced")


#: alass ships its binary as ``alass`` from some builds and ``alass-cli`` from others (Homebrew's
#: ``alass`` formula installs ``alass-cli``) — check both, else a working alass install silently loses
#: to ffsubsync, whose single-offset sync can't fix the per-scene drift alass split-aligns (found live).
_ALASS_BINS = ("alass", "alass-cli")


def _resync_command(
    reference: Path, src: Path, out: Path, *, split_penalty: float | None = None
) -> tuple[list[str], str]:
    """The aligner command + a label, preferring alass (split/framerate-aware, far better on anime)
    over ``uvx ffsubsync`` (single global offset). ``reference`` is what ``src`` is aligned TO — a
    video (audio sync) OR a subtitle file (sub-to-sub); both tools accept either as the first arg.
    ``split_penalty`` (when set) tunes alass's ``--split-penalty`` — lower splits more readily at an
    OP/scene boundary; ffsubsync has no equivalent, so it's ignored there.
    Raises :exc:`ResyncUnavailable` if neither exists."""
    from saitenka.mpvio.discover import find_tool

    for name in _ALASS_BINS:
        alass = find_tool(name)  # find_tool augments PATH for a GUI-launched (minimal-PATH) process
        if alass:
            penalty = [] if split_penalty is None else ["--split-penalty", f"{split_penalty:g}"]
            return [alass, *penalty, str(reference), str(src), str(out)], name
    uvx = find_tool("uvx")
    if uvx:
        return [uvx, "ffsubsync", str(reference), "-i", str(src), "-o", str(out)], "uvx ffsubsync"
    raise ResyncUnavailable(
        "subtitle resync requires alass or ffsubsync; install alass (brew install alass)"
        " or ensure uvx is on PATH"
    )


def _aligned_suffix(src: Path) -> str:
    """The extension alass's output actually deserves. ASS/SSA go in as a normalized SRT (see
    :func:`_alass_ready_source`) and come back as SRT, so only a genuine SRT keeps its own name."""
    return ".srt" if src.suffix.casefold() in {".ass", ".ssa"} else src.suffix


def _alass_ready_source(src: Path, workdir: Path) -> Path:
    """Give alass a source it can parse. alass keys the format off the extension and does NO format
    conversion, so an ASS body saved under a ``.srt`` name (some jimaku sources — found live: a NanakoRaws
    pick → 'parse error at line 0', menu re-time defeated) has to be normalized to a real SRT first. We
    reparse it (BOM-tolerant) and write a clean ``source.srt`` in *workdir*; a genuine SRT passes through
    untouched (returns *src*, so the common path writes nothing). The persisted cache / what mpv shows is
    unchanged — only the aligner's input is normalized."""
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return src
    if not _looks_ass(text):
        return src
    from saitenka_subtitles import parse_ass

    cues = parse_ass(text.lstrip(_BOM))
    if not cues:  # unparseable → let alass try the original and surface its own error
        return src
    ready = workdir / "source.srt"
    _write_srt(ready, cues)
    return ready


def _select_alignment_reference(video: Path, workdir: Path, details: dict | None) -> Path:
    """The alignment reference: a co-timed embedded text track (sub-to-sub, exact-encode timing) when
    available, else the video itself (audio VAD). ``details`` captures WHY embedded was skipped
    (tools-missing/no-text-subs/…) so a report explains an audio run — the alass 'failed to extract
    voice segments' path, useless on some encodes."""
    ref = _embedded_sub_reference(video, workdir, details=details) or video
    if ref is video:
        log.info(
            "resync: using audio VAD (no embedded reference: %s) — alass may fail to extract voice "
            "segments on some encodes; an embedded text track aligns far more reliably",
            (details or {}).get("embedded_ref", "unknown"),
        )
    return ref


def resync(
    video: Path,
    src: Path,
    out: Path,
    *,
    timeout: int | None = None,
    force: bool = False,
    details: dict | None = None,
    split_penalty: float | None = None,
) -> Path:
    """Synchronise ``src`` to ``video`` and write the result to ``out``.

    Uses ``alass``/``alass-cli`` if on PATH, else ``uvx ffsubsync``.  Writes a ``<out>.synced``
    marker on success so subsequent calls are no-ops; ``force`` re-runs even when the marker exists
    (the user's re-sync shortcut).

    Parameters
    ----------
    video:
        The reference video file (for audio-based sync).
    src:
        The subtitle file to resync.
    out:
        Destination path for the resynced subtitle.
    timeout:
        Maximum seconds to wait for the subprocess (``None`` resolves the ``resync_timeout``
        config value, default 300).

    Returns
    -------
    Path
        ``out`` (the resynced subtitle path).

    Raises
    ------
    ResyncUnavailable
        Neither ``alass`` nor ``uvx`` is found on PATH.
    ResyncFailed
        The tool exited with a non-zero code or timed out.
    """
    if timeout is None:
        from saitenka.app.config import resolve_resync_timeout

        timeout = resolve_resync_timeout()
    if split_penalty is None:
        from saitenka.app.config import resolve_resync_split_penalty

        split_penalty = resolve_resync_split_penalty()
    marker = _marker(out)
    if not force and marker.exists() and out.exists():
        log.debug("resync: cache hit for %s — skipping", out.name)
        return out

    workdir = out.parent / f".{out.stem}.refwork"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        ref = _select_alignment_reference(video, workdir, details)
        cmd, tool = _resync_command(
            ref, _alass_ready_source(src, workdir), out, split_penalty=split_penalty
        )
        if (
            details is not None
        ):  # captured before the workdir (holding the embedded ref) is cleaned up
            details["tool"] = tool
            details["reference"] = "audio" if ref is video else "embedded"
            details["reference_fmt"] = (
                "" if ref is video else ref.suffix
            )  # .ass vs .srt (the codec bug)
            # reference.<lang>.<ext> → the track's language (eng/por/…), so a report shows which we used
            details["reference_lang"] = (
                ref.suffixes[0].lstrip(".") if ref is not video and len(ref.suffixes) >= 2 else ""
            )
            details["split_penalty"] = split_penalty
            details["ref_cue_ms"] = _cue_starts_ms(ref)
        log.info(
            "resync: running %s on %s (ref=%s, split_penalty=%s)",
            tool, src.name, ref.name, split_penalty,
        )  # fmt: skip
        try:
            result = subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ResyncFailed(f"resync timed out after {timeout}s ({tool})") from exc
        if result.returncode != 0:
            # alass writes its parse errors to STDOUT, not stderr — capture both, else the failure is a
            # blank message (live: "resync failed (exit 1) via alass-cli:" hid an unparseable reference).
            detail = " ".join(
                s.decode(errors="replace").strip() for s in (result.stdout, result.stderr) if s
            ).strip()
            raise ResyncFailed(
                f"resync failed (exit {result.returncode}) via {tool}: {detail[:300]}"
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    marker.touch()
    log.debug("resync: wrote %s", out.name)
    return out


def _record_resync_details(span, details: dict, *, src: Path, out: Path | None) -> None:
    """Attach the aligner (tool + reference kind) and a text-free cue fingerprint of src/out/reference
    to the resync span, so a report can (a) show which tool/reference actually ran and (b) be replayed
    as an offline regression from integer cue vectors alone — no copyrighted cue text. All optional:
    an unavailable run (nothing selected) simply has no tool/reference in *details*."""
    if details.get("tool"):
        span.set("tool", details["tool"])
    if details.get("reference"):
        span.set("reference", details["reference"])
    if details.get("embedded_ref"):  # why embedded was/wasn't used (tools-missing/no-text-subs/ok)
        span.set("embedded_ref", details["embedded_ref"])
    if details.get("reference_fmt"):
        span.set("reference_fmt", details["reference_fmt"])
    if details.get("reference_lang"):
        span.set("reference_lang", details["reference_lang"])
    if details.get("split_penalty") is not None:
        span.set("split_penalty", details["split_penalty"])
    span.set("ref_cue_ms", details.get("ref_cue_ms", []))
    span.set("src_cue_ms", _cue_starts_ms(src))
    span.set("out_cue_ms", _cue_starts_ms(out) if out is not None else [])


def maybe_resync(
    video: Path,
    src: Path,
    *,
    enabled: bool = True,
    timeout: int | None = None,
    force: bool = False,
    trigger: str = "auto",
) -> Path:
    """Resync ``src`` to ``video`` if *enabled*, returning the synced path.

    Swallows :exc:`ResyncUnavailable` and :exc:`ResyncFailed` — in both cases the original
    ``src`` is returned so the caller can proceed with unsynced subtitles.  A warning is
    logged so the issue is visible in the rotating log without crashing the overlay.

    The output path is placed next to ``src`` with the stem extended by ``.synced``
    (e.g. ``ep01.srt`` → ``ep01.synced.srt``). ``force`` re-runs even past the ``.synced`` marker.
    ``trigger`` (``auto`` initial fetch / ``retry`` the manual re-sync key) tags the span so repeated
    presses are attributable in a report.
    """
    if not enabled:
        return src

    from saitenka import otel_metrics

    # The aligner's OUTPUT format, not the source's: `_alass_ready_source` hands alass an SRT for an
    # ASS body (alass does no conversion), so naming the result `.ass` produced SubRip text under an
    # extension that lies — accepted by the geometry's suffix check, then unparseable. Honest `.srt`
    # instead: the typesetting is still lost, but the file says so and the fallback path sees it.
    out = src.with_name(src.stem + ".synced" + _aligned_suffix(src))
    # A span + INFO log per resync so a report shows whether it ran, its outcome, the shift it applied,
    # and — via the cue fingerprints — enough to replay it offline. Without this a silent fallback-to-raw
    # (tool missing/failed) and a real-but-zero-offset sync are indistinguishable — the ep03 "synced"
    # cache was byte-identical to raw, mistimed subs.
    with otel_metrics.traced("subtitle.resync") as span:
        span.set("trigger", trigger)
        details: dict = {}
        try:
            result = resync(video, src, out, timeout=timeout, force=force, details=details)
        except ResyncUnavailable as exc:
            span.set("outcome", "unavailable")
            log.warning("subtitle resync unavailable — using original (install alass): %s", exc)
            return src
        except ResyncFailed as exc:
            span.set("outcome", "failed")
            span.set(
                "fail_reason", str(exc)[:200]
            )  # the aligner's own error (alass writes it to stdout)
            _record_resync_details(span, details, src=src, out=None)
            log.warning("subtitle resync failed — using original: %s", exc)
            return src
        shift_ms = _first_cue_shift_ms(src, result)
        span.set("outcome", "synced")
        span.set("shift_ms", shift_ms if shift_ms is not None else 0)
        _record_resync_details(span, details, src=src, out=result)
        log.info(
            "resync: %s → shift %+dms (0 = tool applied no offset)", result.name, shift_ms or 0
        )
        return result


def _fmt_srt_ts(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(path: Path, cues) -> None:
    """Serialize cues to an SRT file (the format both alass and mpv read unconditionally)."""
    blocks = [
        f"{i}\n{_fmt_srt_ts(c.start)} --> {_fmt_srt_ts(c.end)}\n{c.text}\n"
        for i, c in enumerate(cues, 1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _parse_cues(path: Path):
    from saitenka_subtitles import parse_ass, parse_srt

    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_ass(text) if path.suffix.lower() == ".ass" or _looks_ass(text) else parse_srt(text)


def _persist_windowed(sub: Path, tmp: Path) -> Path:
    """Write the re-timed result to a stable ``<base>.win<ext>`` sibling (idempotent across repeated
    presses — a second window on an already-windowed file reuses the same name, never ``.win.win``).
    Never clobbers the original cache/``--sub-file``, so a bad window is one keypress from undone.

    The extension follows *tmp*, i.e. the format the re-time actually produced. Hardcoding ``.srt``
    silently downgraded an ASS source to SubRip on every press."""
    stem = sub.stem.removesuffix(".win")
    dest = sub.with_name(f"{stem}.win{tmp.suffix}")
    shutil.copy2(str(tmp), str(dest))
    return dest


def _retimed_document(sub: Path, cues, *, delta: float, boundary: float, workdir: Path) -> Path:
    """The re-timed document to persist, in the SOURCE's own format.

    An ASS source is shifted in place, so its styles, fonts and override tags survive a re-time —
    serializing its cues to SRT discarded the typesetting the source was chosen for and produced a
    body its own extension no longer described. Falls back to SRT for a document this cannot
    round-trip, which is visible (geometry declines an `.srt`) rather than silent.
    """
    from saitenka_subtitles import Cue

    if sub.suffix.casefold() == ".ass":
        from saitenka_subtitles import UnsupportedAssEvent, shift_ass_dialogue

        try:
            shifted = shift_ass_dialogue(
                sub.read_text(encoding="utf-8-sig"),
                delta_ms=round(delta * 1000),
                from_ms=round(boundary * 1000),
            )
        except (UnsupportedAssEvent, OSError, UnicodeDecodeError, ValueError) as error:
            log.info("resync: ASS re-time falls back to SRT (%s)", error)
        else:
            dest = workdir / "result.ass"
            dest.write_text(shifted, encoding="utf-8")
            return dest
    tmp = workdir / "result.srt"
    _write_srt(
        tmp,
        [Cue(c.start + delta, c.end + delta, c.text) if c.start >= boundary else c for c in cues],
    )
    return tmp


def _windowed_align(
    video: Path,
    window: list,
    workdir: Path,
    span,
    *,
    timeout: int | None,
    split_penalty: float | None,
) -> list | None:
    """Extract the reference, align *window* to it, and return alass's aligned cues (1:1 with *window*)
    for the caller to derive the offset from — or None on any failure, tagging *span* with tool/reference
    + a ``fail_reason``. The whole windowed alignment lives here so :func:`resync_window` stays the thin
    parse→offset→persist orchestration."""
    ref_details: dict = {}
    ref = _embedded_sub_reference(video, workdir, details=ref_details)
    if ref is None:  # windowed alignment needs a reference; audio VAD on a slice is unreliable
        reason = ref_details.get("embedded_ref", "unknown")
        span.set("embedded_ref", reason)
        span.set("fail_reason", f"no embedded reference ({reason})")
        return None
    win_srt, out_srt = workdir / "window.srt", workdir / "window.synced.srt"
    _write_srt(win_srt, window)
    try:
        cmd, tool = _resync_command(ref, win_srt, out_srt, split_penalty=split_penalty)
    except ResyncUnavailable as exc:
        span.set("fail_reason", str(exc))
        return None
    span.set("tool", tool)
    span.set("reference", "embedded")
    span.set("reference_lang", ref.suffixes[0].lstrip(".") if len(ref.suffixes) >= 2 else "")
    if split_penalty is not None:
        span.set("split_penalty", split_penalty)
    try:
        result = subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)
    except subprocess.TimeoutExpired:
        span.set("fail_reason", "timeout")
        return None
    if result.returncode != 0 or not out_srt.exists():
        detail = " ".join(
            s.decode(errors="replace").strip() for s in (result.stdout, result.stderr) if s
        )
        span.set("fail_reason", detail[:200])
        return None
    aligned = _parse_cues(out_srt)
    if len(aligned) != len(window):  # alass should preserve cue count; bail if it didn't
        span.set("fail_reason", "cue count changed")
        return None
    return aligned


# Coherence guards on a windowed alignment. A local slice must shift as a near-rigid block, so a wide
# per-cue spread (or an absurd absolute shift) is proof alass mis-correlated it — a short cold-open window
# matching a far region (live: a 12-cue slice → +718s, 929s spread) or a slice straddling a cut. Bail
# without changing the selected track: a different automatic strategy is not proof of the right timing.
_WINDOW_MAX_DRIFT_S = (
    5.0  # per-cue spread within the slice (continuous drift over a minute is sub-second)
)
_WINDOW_MAX_SHIFT_S = (
    300.0  # a "sync from here" is seconds, not minutes — a huge offset = wrong region
)


def resync_window(
    video: Path,
    sub: Path,
    *,
    start_s: float,
    lookback_s: float = 20.0,
    lookahead_s: float = 40.0,
    timeout: int | None = None,
    split_penalty: float | None = None,
) -> Path | None:
    """Re-time the CURRENT subtitle tail against the embedded reference, deriving the offset from a LOCAL
    slice around the playhead (``[start_s - lookback_s, start_s + lookahead_s]``, ~a minute) — for a
    source that DRIFTS across the episode (a whole-file offset that's right after the OP is early before
    it; live: NanakoRaws ran +4.7s pre-OP → +11s post-OP). Correlating locally keeps the far side of a
    drift from dragging the offset off the region you're watching (a whole-tail median did exactly that).
    Applies the single offset to every cue from ``start_s - lookback_s`` on, leaving earlier cues
    untouched — press again after the next drift point (e.g. once past the OP). Returns the re-timed
    ``<base>.win.srt`` path; ``sub`` unchanged when already aligned (no shift); or None on a hard failure
    (no reference / too few cues / tool failure)."""
    from saitenka import otel_metrics

    if timeout is None:
        from saitenka.app.config import resolve_resync_timeout

        timeout = resolve_resync_timeout()
    if split_penalty is None:
        from saitenka.app.config import resolve_resync_split_penalty

        split_penalty = resolve_resync_split_penalty()

    with otel_metrics.traced("subtitle.resync") as span:
        span.set("trigger", "window")
        span.set("window_start_s", round(start_s, 1))
        boundary = max(0.0, start_s - lookback_s)
        horizon = start_s + lookahead_s
        try:
            cues = _parse_cues(sub)
        except OSError:
            span.set("outcome", "failed")
            return None
        window = [
            c for c in cues if boundary <= c.start <= horizon
        ]  # LOCAL slice, not the whole tail
        span.set("window_cues", len(window))
        if len(window) < 2:  # too little to correlate a reliable offset
            span.set("outcome", "failed")
            span.set("fail_reason", "too few cues in window")
            return None

        workdir = sub.parent / f".{sub.stem}.winwork"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            aligned = _windowed_align(
                video, window, workdir, span, timeout=timeout, split_penalty=split_penalty
            )
            if aligned is None:
                span.set("outcome", "failed")
                return None
            # ONE offset from the local slice, applied to the whole tail — NOT a whole-tail median (the
            # post-OP majority dragged that off, mistiming the pre-OP cues you're watching: live
            # NanakoRaws ep04 gave −11s vs the −5s the region needed). A drift source is a press per side
            # of the OP; a large drift range means the slice straddled a cut → the offset is a compromise.
            shifts = [a.start - w.start for a, w in zip(aligned, window, strict=True)]
            delta = statistics.median(shifts)
            drift_range = max(shifts) - min(shifts)
            span.set("window_delta_ms", round(delta * 1000))
            span.set("window_delta_range_ms", round(drift_range * 1000))
            if drift_range > _WINDOW_MAX_DRIFT_S or abs(delta) > _WINDOW_MAX_SHIFT_S:
                # Incoherent alignment (mis-correlation / straddled a cut): keep the current track
                # rather than apply a meaningless median or guess with another matcher scope.
                span.set("outcome", "failed")
                span.set(
                    "fail_reason",
                    f"incoherent window (shift {delta:.0f}s, drift range {drift_range:.0f}s)",
                )
                return None
            if abs(delta) < 0.001:  # already aligned here → sub unchanged (distinct from failure)
                span.set("outcome", "synced")
                return sub
            out_path = _persist_windowed(
                sub,
                _retimed_document(sub, cues, delta=delta, boundary=boundary, workdir=workdir),
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        span.set("outcome", "synced")
        span.set("src_cue_ms", _cue_starts_ms(sub))
        span.set("out_cue_ms", _cue_starts_ms(out_path))
        log.info(
            "resync: windowed from %.1fs → shifted %d cue(s) by %+dms (drift range %dms)",
            start_s, len(window), round(delta * 1000), round((max(shifts) - min(shifts)) * 1000),
        )  # fmt: skip
        return out_path
