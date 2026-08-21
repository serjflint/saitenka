"""Locked source-delivery and mpv-transition oracle for Gate B (#354)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class AssHashes:
    document: str
    extradata: str
    events: str


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    id: str
    expectation: str
    decision: str
    ass: AssHashes | None
    attachments: tuple[tuple[str, str], ...]
    mpv_input_sha256: str | None
    shadow_input_sha256: str | None
    container_sha256: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FrameSample:
    native_pixels: int
    generated_pixels: int

    def __post_init__(self) -> None:
        if self.native_pixels < 0 or self.generated_pixels < 0:
            raise ValueError("pixel counts must be non-negative")


@dataclass(frozen=True, slots=True)
class TransitionAssessment:
    passed: bool
    blank_indices: tuple[int, ...]
    duplicate_indices: tuple[int, ...]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_key_set_hash(source_ids: Iterable[str]) -> str:
    return sha256("\n".join(sorted(source_ids)).encode())


def contract_hash(manifest: dict[str, Any]) -> str:
    payload = {key: manifest[key] for key in ("schema", "sources", "render_inputs", "transition")}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode())


def load_manifest(path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("unsupported manifest schema")
    sources = manifest.get("sources")
    denominator = manifest.get("denominator")
    if not isinstance(sources, list) or not isinstance(denominator, dict):
        raise TypeError("manifest must contain sources and denominator")
    if len(sources) != denominator.get("source_count"):
        raise ValueError("manifest source count changed")
    if source_key_set_hash(str(source["id"]) for source in sources) != denominator.get(
        "source_key_set_sha256"
    ):
        raise ValueError("manifest source identities changed")
    if contract_hash(manifest) != denominator.get("contract_sha256"):
        raise ValueError("manifest execution contract changed")
    if len({source["id"] for source in sources}) != len(sources):
        raise ValueError("manifest source identities are not unique")
    transition = manifest.get("transition")
    if not isinstance(transition, dict) or transition.get(
        "selected_contract"
    ) not in transition.get("candidates", ()):
        raise ValueError("selected transition contract is not a candidate")
    duplicate_minimum_coverage = transition.get("duplicate_minimum_coverage")
    if (
        isinstance(duplicate_minimum_coverage, bool)
        or not isinstance(duplicate_minimum_coverage, (int, float))
        or not 0 < duplicate_minimum_coverage <= 1
    ):
        raise ValueError("duplicate minimum coverage must be in (0, 1]")
    inputs = manifest.get("render_inputs")
    if not isinstance(inputs, list) or not inputs or not all(item.get("owner") for item in inputs):
        raise ValueError("every render input needs an owner")
    input_names = [str(item["name"]) for item in inputs]
    if len(inputs) != denominator.get("render_input_count"):
        raise ValueError("render input count changed")
    if source_key_set_hash(input_names) != denominator.get("render_input_key_set_sha256"):
        raise ValueError("render input identities changed")
    if len(set(input_names)) != len(input_names):
        raise ValueError("render input identities are not unique")
    required_controls = transition.get("required_controls")
    if not isinstance(required_controls, list):
        raise TypeError("transition required_controls must be a list")
    if len(required_controls) != denominator.get("required_control_count"):
        raise ValueError("required control count changed")
    if source_key_set_hash(required_controls) != denominator.get("required_control_key_set_sha256"):
        raise ValueError("required control identities changed")
    if len(set(required_controls)) != len(required_controls):
        raise ValueError("required control identities are not unique")
    root = repo_root or path.parents[2]
    for source in sources:
        if ass_path := source.get("ass"):
            _verified_fixture(path, root, ass_path, source.get("ass_sha256"))
        if source_path := source.get("source"):
            _verified_fixture(path, root, source_path, source.get("source_sha256"))
        for attachment in source.get("attachments", ()):
            _verified_fixture(path, root, attachment["path"], attachment.get("sha256"))
    _verified_fixture(
        path, root, transition["generated_ass"], transition.get("generated_ass_sha256")
    )
    return manifest


def ass_hashes(data: bytes) -> AssHashes:
    marker = b"[Events]"
    try:
        split = data.index(marker)
    except ValueError as error:
        raise ValueError("ASS document has no [Events] section") from error
    extradata = data[:split]
    events = b"\n".join(
        line.rstrip(b"\r")
        for line in data[split:].splitlines()
        if line.startswith((b"Dialogue:", b"Comment:"))
    )
    if not events:
        raise ValueError("ASS document has no authored events")
    return AssHashes(sha256(data), sha256(extradata), sha256(events))


def assess_frames(samples: Sequence[FrameSample]) -> TransitionAssessment:
    if not samples:
        raise ValueError("transition needs at least one frame sample")
    blank = tuple(
        index
        for index, sample in enumerate(samples)
        if sample.native_pixels == 0 and sample.generated_pixels == 0
    )
    duplicate = tuple(
        index
        for index, sample in enumerate(samples)
        if sample.native_pixels > 0 and sample.generated_pixels > 0
    )
    return TransitionAssessment(not blank and not duplicate, blank, duplicate)


def select_transition_contract(*, presented_frames_observed: bool) -> str:
    return "paused-only-switch" if presented_frames_observed else "native-visible"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _fixture_path(manifest_path: Path, repo_root: Path, value: str) -> Path:
    path = Path(value)
    if path.parts and path.parts[0] in {"src", "tests", "libasslite"}:
        return repo_root / path
    return manifest_path.parent / path


def _verified_fixture(
    manifest_path: Path, repo_root: Path, value: str, expected_hash: str | None
) -> Path:
    path = _fixture_path(manifest_path, repo_root, value)
    if not path.is_file():
        raise FileNotFoundError(f"fixture unavailable: {value}")
    if expected_hash is None or sha256(path.read_bytes()) != expected_hash:
        raise ValueError(f"fixture hash changed: {value}")
    return path


def _attachments(
    manifest_path: Path, repo_root: Path, values: Iterable[dict[str, str]]
) -> tuple[tuple[str, bytes], ...]:
    items = []
    for value in values:
        path = _verified_fixture(manifest_path, repo_root, value["path"], value.get("sha256"))
        items.append((path.name, path.read_bytes()))
    return tuple(items)


def _evidence(
    source: dict[str, Any],
    ass: bytes,
    attachments: tuple[tuple[str, bytes], ...],
    *,
    container: bytes | None = None,
) -> SourceEvidence:
    digest = sha256(ass)
    return SourceEvidence(
        id=str(source["id"]),
        expectation=str(source["expectation"]),
        decision="supported",
        ass=ass_hashes(ass),
        attachments=tuple((name, sha256(data)) for name, data in attachments),
        mpv_input_sha256=digest,
        shadow_input_sha256=digest,
        container_sha256=sha256(container) if container is not None else None,
    )


def _validate_synthetic_pair(source_path: Path, ass_path: Path) -> None:
    import pysubs2

    def cues(path: Path) -> tuple[tuple[int, int, str], ...]:
        subtitles = pysubs2.load(str(path), encoding="utf-8")
        return tuple((event.start, event.end, event.plaintext) for event in subtitles)

    if cues(source_path) != cues(ass_path):
        raise ValueError("synthetic source and paired ASS cues differ")


def collect_static_source_evidence(
    manifest_path: Path, repo_root: Path
) -> tuple[SourceEvidence, ...]:
    manifest = load_manifest(manifest_path, repo_root=repo_root)
    evidence = []
    for source in manifest["sources"]:
        kind = source["kind"]
        if kind == "remote-stream":
            evidence.append(
                SourceEvidence(
                    id=source["id"],
                    expectation=source["expectation"],
                    decision="fallback",
                    ass=None,
                    attachments=(),
                    mpv_input_sha256=None,
                    shadow_input_sha256=None,
                    fallback_reason=source["reason"],
                )
            )
            continue
        if kind == "embedded-ass":
            continue
        ass_path = _verified_fixture(
            manifest_path, repo_root, source["ass"], source.get("ass_sha256")
        )
        if kind == "synthetic-text":
            source_path = _verified_fixture(
                manifest_path, repo_root, source["source"], source.get("source_sha256")
            )
            _validate_synthetic_pair(source_path, ass_path)
        attachments = _attachments(manifest_path, repo_root, source.get("attachments", ()))
        evidence.append(_evidence(source, ass_path.read_bytes(), attachments))
    return tuple(evidence)


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        details = "\n".join(part for part in (error.stdout, error.stderr) if part)
        raise RuntimeError(
            f"{command[0]} failed with exit {error.returncode}\n{details}"
        ) from error


def build_embedded_delivery(
    manifest_path: Path, repo_root: Path, workspace: Path, *, ffmpeg: str = "ffmpeg"
) -> tuple[SourceEvidence, Path, Path, tuple[Path, ...]]:
    manifest = load_manifest(manifest_path, repo_root=repo_root)
    source = next(item for item in manifest["sources"] if item["kind"] == "embedded-ass")
    ass_path = _verified_fixture(manifest_path, repo_root, source["ass"], source.get("ass_sha256"))
    attachments = _attachments(manifest_path, repo_root, source["attachments"])
    container = workspace / "embedded.mkv"
    command = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=navy:s=1280x720:d=8",
        "-i",
        str(ass_path),
        "-map",
        "0:v:0",
        "-map",
        "1:s:0",
        "-c:v",
        "ffv1",
        "-c:s",
        "ass",
    ]
    for index, (name, data) in enumerate(attachments):
        font = workspace / name
        font.write_bytes(data)
        command.extend(
            (
                "-attach",
                str(font),
                f"-metadata:s:t:{index}",
                "mimetype=application/x-truetype-font",
                f"-metadata:s:t:{index}",
                f"filename={name}",
            )
        )
    command.append(str(container))
    _run(command)

    extracted_ass = workspace / "embedded.ass"
    _run([ffmpeg, "-y", "-i", str(container), "-map", "0:s:0", "-c:s", "ass", str(extracted_ass)])
    extracted_attachments = []
    for index, (name, _data) in enumerate(attachments):
        target = workspace / f"extracted-{name}"
        _run(
            [
                ffmpeg,
                f"-dump_attachment:t:{index}",
                str(target),
                "-i",
                str(container),
                "-f",
                "null",
                os.devnull,
            ]
        )
        extracted_attachments.append(target)
    extracted_fonts = tuple(
        (path.name.removeprefix("extracted-"), path.read_bytes()) for path in extracted_attachments
    )
    return (
        _evidence(
            source,
            extracted_ass.read_bytes(),
            extracted_fonts,
            container=container.read_bytes(),
        ),
        container,
        extracted_ass,
        tuple(extracted_attachments),
    )


def _wait_for(predicate, *, timeout: float, message: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise TimeoutError(message)


def _track_for_path(ipc, path: Path) -> dict[str, Any] | None:
    tracks = ipc.query("track-list") or []
    target = os.path.normcase(str(path.resolve()))
    return next(
        (
            track
            for track in tracks
            if track.get("type") == "sub"
            and track.get("external-filename")
            and os.path.normcase(str(Path(track["external-filename"]).resolve())) == target
        ),
        None,
    )


def _internal_ass_track(ipc) -> dict[str, Any] | None:
    tracks = ipc.query("track-list") or []
    candidates = [
        track
        for track in tracks
        if track.get("type") == "sub"
        and not track.get("external")
        and str(track.get("codec", "")).lower() in {"ass", "ssa"}
    ]
    if len(candidates) > 1:
        raise AssertionError("embedded source has ambiguous internal ASS tracks")
    return candidates[0] if candidates else None


def _wait_property(ipc, name: str, expected: Any, *, timeout: float = 3.0) -> dict[str, Any]:
    def observed():
        for event in ipc.drain_events():
            if (
                event.get("event") == "property-change"
                and event.get("name") == name
                and event.get("data") == expected
            ):
                return event
        return None

    return _wait_for(observed, timeout=timeout, message=f"no {name}={expected!r} event")


def _wait_subtitle_state_events(
    ipc, sid: int, text: str, *, text_contains: bool = False, timeout: float = 3.0
) -> tuple[dict[str, Any], dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}

    def observed():
        for event in ipc.drain_events():
            if event.get("event") != "property-change":
                continue
            name = event.get("name")
            if name == "sid" and event.get("data") == sid:
                seen["sid"] = event
            if name == "sub-text":
                value = event.get("data") or ""
                text_matches = text in value if text_contains else value == text
                if text_matches:
                    seen["sub-text"] = event
        return (seen["sid"], seen["sub-text"]) if len(seen) == 2 else None

    return _wait_for(observed, timeout=timeout, message=f"no sid/sub-text events for {sid}")


def _event_has_track(event: dict[str, Any], path: Path) -> bool:
    target = os.path.normcase(str(path.resolve()))
    return any(
        track.get("type") == "sub"
        and track.get("external-filename")
        and os.path.normcase(str(Path(track["external-filename"]).resolve())) == target
        for track in (event.get("data") or [])
    )


def _wait_track_event(ipc, path: Path, *, present: bool) -> dict[str, Any]:
    def observed():
        for event in ipc.drain_events():
            if (
                event.get("event") == "property-change"
                and event.get("name") == "track-list"
                and _event_has_track(event, path) is present
            ):
                return event
        return None

    return _wait_for(observed, timeout=3.0, message=f"no track-list event with present={present}")


def _probe_phase(ipc, generated_sid: int, native_sid: int, *, paused: bool) -> dict[str, Any]:
    current_pause = ipc.query("pause")
    if current_pause == paused:
        pause_observation = {"kind": "current-value", "data": current_pause}
    else:
        ipc.command("set_property", "pause", paused)
        pause_observation = _wait_property(ipc, "pause", paused)
    ipc.command("set_property", "sid", generated_sid)
    generated_sid_event, generated_text_event = _wait_subtitle_state_events(
        ipc, generated_sid, "生成した字幕"
    )
    ipc.command("set_property", "sid", native_sid)
    native_sid_event, native_text_event = _wait_subtitle_state_events(
        ipc, native_sid, "原稿の字幕", text_contains=True
    )
    return {
        "paused": paused,
        "pause_observation": pause_observation,
        "generated_sid_event": generated_sid_event,
        "generated_sub_text_event": generated_text_event,
        "native_sid_event": native_sid_event,
        "native_sub_text_event": native_text_event,
    }


def _exercise_delivery(ipc, source_id: str, path: Path) -> dict[str, Any]:
    ipc.drain_events()
    reply = ipc.command("sub-add", str(path), "auto", f"gate-b-{source_id}", "jpn")
    if reply.get("error") != "success":
        raise AssertionError(f"mpv rejected {source_id}: {reply.get('error')}")
    added_event = _wait_track_event(ipc, path, present=True)
    track = _wait_for(
        lambda: _track_for_path(ipc, path),
        timeout=5.0,
        message=f"{source_id} track identity not discovered",
    )
    sid = int(track["id"])
    ipc.drain_events()
    reply = ipc.command("sub-remove", sid)
    if reply.get("error") != "success":
        raise AssertionError(f"mpv did not remove {source_id}: {reply.get('error')}")
    removed_event = _wait_track_event(ipc, path, present=False)
    _wait_for(
        lambda: _track_for_path(ipc, path) is None,
        timeout=3.0,
        message=f"{source_id} track was not removed",
    )
    return {
        "source_id": source_id,
        "sid": sid,
        "input_sha256": sha256(path.read_bytes()),
        "track_added_event": added_event,
        "track_removed_event": removed_event,
    }


def _subtitle_mask(ipc, workspace: Path, label: str) -> set[int]:
    from PIL import Image, ImageChops

    video_path = workspace / f"{label}-video.png"
    subtitles_path = workspace / f"{label}-subtitles.png"
    for path, mode in ((video_path, "video"), (subtitles_path, "subtitles")):
        reply = ipc.command("screenshot-to-file", str(path), mode, timeout=10)
        if reply.get("error") != "success":
            raise AssertionError(f"mpv screenshot {mode} failed: {reply.get('error')}")
        _wait_for(path.is_file, timeout=5.0, message=f"mpv did not write {path.name}")
    with Image.open(video_path) as video_image, Image.open(subtitles_path) as subtitle_image:
        video = video_image.convert("RGB")
        subtitles = subtitle_image.convert("RGB")
        if video.size != subtitles.size:
            raise AssertionError("mpv screenshot dimensions changed between modes")
        difference = ImageChops.difference(video, subtitles)
        return {
            index
            for index, pixel in enumerate(difference.get_flattened_data())
            if pixel != (0, 0, 0)
        }


def _mask_sha256(mask: set[int]) -> str:
    return sha256(",".join(str(index) for index in sorted(mask)).encode())


def _mask_coverage(reference: set[int], observed: set[int]) -> float:
    if not reference:
        raise ValueError("subtitle mask must not be empty")
    return len(reference & observed) / len(reference)


def _duplicate_layers_visible(
    native: set[int], generated: set[int], duplicate: set[int], *, minimum_coverage: float = 0.99
) -> bool:
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum coverage must be in (0, 1]")
    native_only = native - generated
    generated_only = generated - native
    expected_union = native | generated
    return bool(
        native_only
        and generated_only
        and duplicate & native_only
        and duplicate & generated_only
        and _mask_coverage(native, duplicate) >= minimum_coverage
        and _mask_coverage(generated, duplicate) >= minimum_coverage
        and _mask_coverage(duplicate, expected_union) >= minimum_coverage
    )


def _sample_frame_controls(
    ipc,
    workspace: Path,
    *,
    native_sid: int,
    generated_sid: int,
    duplicate_minimum_coverage: float,
) -> dict[str, Any]:
    def sample(label: str, sid: int | str, secondary_sid: int | str = "no") -> set[int]:
        for name, value in (("sid", sid), ("secondary-sid", secondary_sid)):
            reply = ipc.command("set_property", name, value)
            if reply.get("error") != "success":
                raise AssertionError(f"mpv rejected {name}={value!r}: {reply.get('error')}")
        return _subtitle_mask(ipc, workspace, label)

    native = sample("native", native_sid)
    generated = sample("generated", generated_sid)
    blank = sample("blank", "no")
    generated_secondary = sample("generated-secondary", "no", generated_sid)
    duplicate = sample("duplicate", native_sid, generated_sid)
    restored_native = sample("restored-native", native_sid)
    if not native or not generated or not generated_secondary:
        raise AssertionError("mpv static screenshot omitted a selected subtitle")
    duplicate_native = len(duplicate & native)
    duplicate_generated = len(duplicate & generated_secondary)
    duplicate_native_coverage = _mask_coverage(native, duplicate)
    duplicate_generated_coverage = _mask_coverage(generated_secondary, duplicate)
    duplicate_union_precision = _mask_coverage(duplicate, native | generated_secondary)
    duplicate_layers_visible = _duplicate_layers_visible(
        native,
        generated_secondary,
        duplicate,
        minimum_coverage=duplicate_minimum_coverage,
    )
    return {
        "api": "screenshot-to-file",
        "native_pixels": len(native),
        "native_mask_sha256": _mask_sha256(native),
        "generated_pixels": len(generated),
        "generated_secondary_pixels": len(generated_secondary),
        "blank_pixels": len(blank),
        "duplicate_pixels": len(duplicate),
        "duplicate_native_overlap": duplicate_native,
        "duplicate_generated_overlap": duplicate_generated,
        "duplicate_native_coverage": duplicate_native_coverage,
        "duplicate_generated_coverage": duplicate_generated_coverage,
        "duplicate_union_precision": duplicate_union_precision,
        "duplicate_minimum_coverage": duplicate_minimum_coverage,
        "duplicate_layers_visible": duplicate_layers_visible,
        "restored_native_mask_sha256": _mask_sha256(restored_native),
        "native_restored_before_transition": restored_native == native,
        "blank_control_detected": not assess_frames([FrameSample(len(blank), 0)]).passed,
        "duplicate_control_detected": not assess_frames(
            [FrameSample(duplicate_native, duplicate_generated)]
        ).passed
        and duplicate_layers_visible,
    }


def run_mpv_transition_probe(
    manifest_path: Path,
    repo_root: Path,
    workspace: Path,
    *,
    mpv: str = "mpv",
    ffmpeg: str = "ffmpeg",
    media_path: Path | None = None,
    embedded_evidence: SourceEvidence | None = None,
    embedded_fonts: bool = True,
) -> dict[str, Any]:
    from saitenka.mpvio.ipc import MpvIPC, default_ipc_path

    manifest = load_manifest(manifest_path, repo_root=repo_root)
    native_source = next(source for source in manifest["sources"] if source["id"] == "external-ass")
    native = _verified_fixture(
        manifest_path, repo_root, native_source["ass"], native_source.get("ass_sha256")
    )
    generated_source = _verified_fixture(
        manifest_path,
        repo_root,
        manifest["transition"]["generated_ass"],
        manifest["transition"].get("generated_ass_sha256"),
    )
    generated = workspace / "generated.ass"
    shutil.copyfile(generated_source, generated)
    clip = media_path or workspace / "clip.mkv"
    if media_path is None:
        _run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=navy:s=1280x720:d=8",
                "-c:v",
                "ffv1",
                str(clip),
            ]
        )
    endpoint = default_ipc_path(f"gate-b-{os.getpid()}")
    mpv_log = workspace / "mpv.log"
    log_stream = mpv_log.open("wb")
    process = subprocess.Popen(
        [
            mpv,
            f"--input-ipc-server={endpoint}",
            "--no-config",
            "--sub-auto=no",
            f"--embeddedfonts={'yes' if embedded_fonts else 'no'}",
            "--vo=null",
            "--ao=null",
            "--keep-open=yes",
            "--pause",
            f"--sub-file={native}",
            str(clip),
        ],
        stdout=log_stream,
        stderr=subprocess.STDOUT,
    )
    ipc = None
    try:
        ipc = MpvIPC(endpoint).connect(timeout=15)
        for index, name in enumerate(manifest["transition"]["observed_properties"], start=1):
            assert ipc.command("observe_property", index, name).get("error") == "success"
        external_track = _wait_for(
            lambda: _track_for_path(ipc, native), timeout=5.0, message="native track not discovered"
        )
        external_sid = int(external_track["id"])
        embedded_sid = None
        if embedded_evidence is not None:
            embedded_track = _wait_for(
                lambda: _internal_ass_track(ipc),
                timeout=5.0,
                message="embedded ASS track not discovered",
            )
            embedded_sid = int(embedded_track["id"])
        native_sid = embedded_sid if embedded_sid is not None else external_sid
        ipc.command("set_property", "sid", native_sid)
        ipc.command("set_property", "time-pos", 1.0)
        ipc.drain_events()
        source_deliveries: list[dict[str, Any]] = [
            {
                "source_id": "external-ass",
                "sid": external_sid,
                "input_sha256": sha256(native.read_bytes()),
                "track_added_event": "loaded-with-player",
                "track_removed_event": "retained-as-native-visible",
            }
        ]
        if embedded_evidence is not None:
            _require(embedded_sid is not None, "embedded ASS identity was lost")
            _require(
                embedded_evidence.mpv_input_sha256 is not None,
                "embedded ASS input hash is missing",
            )
            source_deliveries.append(
                {
                    "source_id": embedded_evidence.id,
                    "sid": cast("int", embedded_sid),
                    "input_sha256": cast("str", embedded_evidence.mpv_input_sha256),
                    "container_sha256": sha256(clip.read_bytes()),
                    "attachments": dict(embedded_evidence.attachments),
                    "track_added_event": "loaded-from-container",
                    "track_removed_event": "retained-in-container",
                }
            )
        synthetic_source = next(
            source for source in manifest["sources"] if source["id"] == "synthetic-ass-from-text"
        )
        synthetic = _verified_fixture(
            manifest_path,
            repo_root,
            synthetic_source["ass"],
            synthetic_source.get("ass_sha256"),
        )
        source_deliveries.append(_exercise_delivery(ipc, "synthetic-ass-from-text", synthetic))
        ipc.drain_events()
        ipc.command("sub-add", str(generated), "auto", "saitenka-gate-b", "jpn")
        added_event = _wait_track_event(ipc, generated, present=True)
        generated_track = _wait_for(
            lambda: _track_for_path(ipc, generated),
            timeout=5.0,
            message="generated track identity not discovered",
        )
        generated_sid = int(generated_track["id"])
        _require(generated_sid != native_sid, "generated track reused the native identity")
        frame_sampling = _sample_frame_controls(
            ipc,
            workspace,
            native_sid=native_sid,
            generated_sid=generated_sid,
            duplicate_minimum_coverage=float(manifest["transition"]["duplicate_minimum_coverage"]),
        )
        phases = [
            _probe_phase(ipc, generated_sid, native_sid, paused=True),
            _probe_phase(ipc, generated_sid, native_sid, paused=False),
        ]
        before_wrong = ipc.query("sid")
        wrong_reply = ipc.command("set_property", "sid", 2_147_483_647)
        after_wrong = ipc.query("sid")
        _require(
            wrong_reply.get("error") != "success" and after_wrong == before_wrong,
            "wrong-track control did not fail closed",
        )
        ipc.drain_events()
        ipc.command("sub-remove", generated_sid)
        removed_event = _wait_track_event(ipc, generated, present=False)
        _wait_for(
            lambda: _track_for_path(ipc, generated) is None,
            timeout=3.0,
            message="generated track was not removed",
        )
        final_sid = ipc.query("sid")
        final_secondary_sid = ipc.query("secondary-sid")
        final_native_mask = _subtitle_mask(ipc, workspace, "final-native")
        native_visual_restored = (
            final_sid == native_sid
            and final_secondary_sid in {None, "no", False}
            and _mask_sha256(final_native_mask) == frame_sampling["native_mask_sha256"]
        )
        log_stream.flush()
        mpv_log_text = mpv_log.read_text(encoding="utf-8", errors="replace")
        return {
            "mpv_version": subprocess.run(
                [mpv, "--version"], check=True, capture_output=True, text=True
            ).stdout.splitlines()[0],
            "platform": platform.platform(),
            "ipc_kind": "named-pipe" if os.name == "nt" else "unix-socket",
            "native_sid": native_sid,
            "generated_sid": generated_sid,
            "source_deliveries": source_deliveries,
            "phases": phases,
            "generated_track_added_event": added_event,
            "generated_track_removed_event": removed_event,
            "wrong_track_rejected": True,
            "generated_track_removed": True,
            "final_sid": final_sid,
            "final_secondary_sid": final_secondary_sid,
            "final_native_mask_sha256": _mask_sha256(final_native_mask),
            "native_visual_restored": native_visual_restored,
            "selected_contract": select_transition_contract(presented_frames_observed=False),
            "public_frame_sampling": frame_sampling,
            "exact_presented_frame_callback": "unavailable-through-public-ipc",
            "embedded_fonts_enabled": embedded_fonts,
            "mpv_log": mpv_log_text,
        }
    except Exception as error:
        log_stream.flush()
        diagnostic = mpv_log.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"mpv transition probe failed: {error}\n{diagnostic}") from error
    finally:
        if ipc is not None:
            try:
                ipc.command("quit")
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if ipc is not None:
            ipc.close()
        log_stream.close()


def _shadow_render(
    evidence: SourceEvidence, ass_path: Path, fonts: Sequence[Path]
) -> dict[str, Any]:
    import libasslite  # noqa: TID251 -- Gate oracle exercises the boundary directly.

    renderer_type: Any = libasslite.AssRenderer  # ty: ignore[unresolved-attribute]
    renderer = renderer_type(
        ass_path.read_bytes(), [(path.name, path.read_bytes()) for path in fonts]
    )
    try:
        result = renderer.render(1_000, (1280, 720), (1280, 720))
        if not result.layers:
            raise AssertionError(f"shadow libass rendered no layers for {evidence.id}")
        digest = hashlib.sha256()
        for layer in result.layers:
            digest.update(
                json.dumps(
                    (
                        layer.width,
                        layer.height,
                        layer.stride,
                        layer.color,
                        layer.dst_x,
                        layer.dst_y,
                        layer.image_type,
                    ),
                    separators=(",", ":"),
                ).encode()
            )
            digest.update(layer.bitmap)
        return {
            "source_id": evidence.id,
            "library_version": f"0x{renderer.library_version():08x}",
            "library_path": renderer.library_path(),
            "layer_count": len(result.layers),
            "geometry_sha256": digest.hexdigest(),
            "font_attachments": [path.name for path in fonts],
        }
    finally:
        renderer.close()


def build_report(
    manifest_path: Path,
    repo_root: Path,
    *,
    mpv: str = "mpv",
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, repo_root=repo_root)
    static = list(collect_static_source_evidence(manifest_path, repo_root))
    with tempfile.TemporaryDirectory(prefix="saitenka-gate-b-") as raw_workspace:
        workspace = Path(raw_workspace)
        embedded, container, embedded_ass, fonts = build_embedded_delivery(
            manifest_path, repo_root, workspace, ffmpeg=ffmpeg
        )
        static.append(embedded)
        evidence_by_id = {item.id: item for item in static}
        source_by_id = {source["id"]: source for source in manifest["sources"]}
        shadow_renders = []
        for source_id in ("external-ass", "synthetic-ass-from-text"):
            source = source_by_id[source_id]
            ass_path = _verified_fixture(
                manifest_path, repo_root, source["ass"], source.get("ass_sha256")
            )
            shadow_renders.append(_shadow_render(evidence_by_id[source_id], ass_path, ()))
        embedded_render = _shadow_render(embedded, embedded_ass, fonts)
        embedded_without_font = _shadow_render(embedded, embedded_ass, ())
        shadow_renders.append(embedded_render)
        transition = run_mpv_transition_probe(
            manifest_path,
            repo_root,
            workspace,
            mpv=mpv,
            ffmpeg=ffmpeg,
        )
        embedded_probe = run_mpv_transition_probe(
            manifest_path,
            repo_root,
            workspace,
            mpv=mpv,
            ffmpeg=ffmpeg,
            media_path=container,
            embedded_evidence=embedded,
        )
        embedded_without_font_probe = run_mpv_transition_probe(
            manifest_path,
            repo_root,
            workspace,
            mpv=mpv,
            ffmpeg=ffmpeg,
            media_path=container,
            embedded_evidence=embedded,
            embedded_fonts=False,
        )
        embedded_delivery = next(
            item for item in embedded_probe["source_deliveries"] if item["source_id"] == embedded.id
        )
        transition["source_deliveries"].append(embedded_delivery)
        mpv_font_attachment_selected = (
            embedded_probe["public_frame_sampling"]["native_mask_sha256"]
            != embedded_without_font_probe["public_frame_sampling"]["native_mask_sha256"]
        )
        transition["embedded_source_probe"] = {
            "native_sid": embedded_probe["native_sid"],
            "with_attachment_sha256": embedded_probe["public_frame_sampling"]["native_mask_sha256"],
            "without_attachment_sha256": embedded_without_font_probe["public_frame_sampling"][
                "native_mask_sha256"
            ],
            "selected": mpv_font_attachment_selected,
            "public_frame_sampling": embedded_probe["public_frame_sampling"],
            "mpv_log": embedded_probe["mpv_log"],
        }
        workspace_path = str(workspace)
    source_order = {source["id"]: index for index, source in enumerate(manifest["sources"])}
    static.sort(key=lambda item: source_order[item.id])
    deliveries = {item["source_id"]: item for item in transition["source_deliveries"]}
    supported = [item for item in static if item.decision == "supported"]
    rendered_ids = {item["source_id"] for item in shadow_renders}
    source_bytes_match = all(
        item.mpv_input_sha256
        == item.shadow_input_sha256
        == deliveries.get(item.id, {}).get("input_sha256")
        for item in supported
    )
    embedded_delivery = deliveries.get(embedded.id, {})
    embedded_container_matches = embedded.container_sha256 == embedded_delivery.get(
        "container_sha256"
    )
    embedded_attachments_match = dict(embedded.attachments) == embedded_delivery.get("attachments")
    font_attachment_selected = (
        embedded_render["geometry_sha256"] != embedded_without_font["geometry_sha256"]
    )
    source_delivery_passed = (
        rendered_ids == {item.id for item in supported}
        and source_bytes_match
        and embedded_container_matches
        and embedded_attachments_match
        and font_attachment_selected
        and transition["embedded_source_probe"]["selected"]
    )
    frame_sampling = transition["public_frame_sampling"]
    controls = {
        "wrong-track": transition["wrong_track_rejected"],
        "blank-frame": frame_sampling["blank_control_detected"],
        "duplicate-layer": frame_sampling["duplicate_control_detected"],
        "missing-attachment": font_attachment_selected,
        "unavailable-source": any(
            item.id == "remote-source-unavailable" and item.decision == "fallback"
            for item in static
        ),
        "generated-file-cleanup": not Path(workspace_path).exists(),
    }
    transition_passed = (
        transition["selected_contract"] == manifest["transition"]["selected_contract"]
        and transition["wrong_track_rejected"]
        and transition["generated_track_removed"]
        and transition["native_visual_restored"]
    )
    required_controls = manifest["transition"]["required_controls"]
    controls_passed = set(controls) == set(required_controls) and all(
        controls[name] for name in required_controls
    )
    gate_b_passed = source_delivery_passed and transition_passed and controls_passed
    return {
        "schema": 1,
        "manifest_sha256": sha256(manifest_path.read_bytes()),
        "contract_sha256": contract_hash(manifest),
        "sources": [asdict(item) for item in static],
        "render_inputs": manifest["render_inputs"],
        "shadow_renders": shadow_renders,
        "font_attachment_control": {
            "with_attachment_sha256": embedded_render["geometry_sha256"],
            "without_attachment_sha256": embedded_without_font["geometry_sha256"],
            "selected": font_attachment_selected,
        },
        "transition": transition,
        "controls": controls,
        "controls_passed": controls_passed,
        "source_delivery_passed": source_delivery_passed,
        "transition_passed": transition_passed,
        "gate_b_passed": gate_b_passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--mpv", default="mpv")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    try:
        report = build_report(
            args.manifest,
            args.repo_root,
            mpv=args.mpv,
            ffmpeg=args.ffmpeg,
        )
    except Exception as error:
        failure = {
            "schema": 1,
            "gate_b_passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        args.output.write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        raise
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not report["gate_b_passed"]:
        raise SystemExit("Gate B source/transition contract failed")


if __name__ == "__main__":
    main()
