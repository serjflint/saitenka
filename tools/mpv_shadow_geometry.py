"""Locked mpv-versus-shadow-libass geometry oracle for Gate D (#359)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from libass_token_matrix import (
    ASS_HEADER,
    TokenKey,
    extract_token_geometry,
    support_bounds,
    support_iou,
    supports_within,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


Point = tuple[int, int]
Mask = frozenset[Point]


class ImageLayer(Protocol):
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int


class RenderResult(Protocol):
    layers: Sequence[ImageLayer]


@dataclass(frozen=True, slots=True)
class MaskAssessment:
    passed: bool
    reference_pixels: int
    support_reference_pixels: int
    observed_pixels: int
    reference_bounds: tuple[int, int, int, int]
    observed_bounds: tuple[int, int, int, int]
    outer_bounds_equal: bool
    outer_bounds_within_maximum_distance: bool
    mask_iou: float
    within_maximum_distance: bool


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_set_hash(values: Iterable[str]) -> str:
    return sha256("\n".join(sorted(values)).encode())


def contract_hash(manifest: dict[str, Any]) -> str:
    payload = {
        key: manifest[key]
        for key in (
            "schema",
            "upstream",
            "required_case_ids",
            "source_classes",
            "profiles",
            "contracts",
            "thresholds",
            "required_controls",
            "required_render_input_checks",
        )
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode())


def _verified_upstream(repo_root: Path, item: dict[str, Any], name: str) -> Path:
    path = repo_root / str(item[f"{name}_manifest"])
    if not path.is_file():
        raise FileNotFoundError(f"{name} manifest is unavailable")
    if sha256(path.read_bytes()) != item.get(f"{name}_manifest_sha256"):
        raise ValueError(f"{name} manifest hash changed")
    return path


def _validate_thresholds(thresholds: dict[str, Any]) -> None:
    iou = thresholds.get("minimum_mask_iou")
    distance = thresholds.get("maximum_chebyshev_distance_px")
    if isinstance(iou, bool) or not isinstance(iou, (int, float)) or not 0 < iou <= 1:
        raise ValueError("minimum mask IoU must be in (0, 1]")
    if isinstance(distance, bool) or not isinstance(distance, int) or distance < 0:
        raise ValueError("maximum support distance must be a non-negative integer")
    bounds_distance = thresholds.get("maximum_outer_bounds_distance_px")
    if (
        isinstance(bounds_distance, bool)
        or not isinstance(bounds_distance, int)
        or bounds_distance < 0
    ):
        raise ValueError("maximum outer-bounds distance must be a non-negative integer")


def _validate_ids(values: Any, denominator: dict[str, Any], prefix: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise TypeError(f"{prefix} identities must be a string list")
    identities = tuple(values)
    if len(identities) != denominator.get(f"{prefix}_count"):
        raise ValueError(f"{prefix} count changed")
    if len(set(identities)) != len(identities):
        raise ValueError(f"{prefix} identities must be unique")
    if key_set_hash(identities) != denominator.get(f"{prefix}_key_set_sha256"):
        raise ValueError(f"{prefix} identities changed")
    return identities


def load_manifest(path: Path, *, repo_root: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("unsupported geometry manifest schema")
    denominator = manifest.get("denominator")
    upstream = manifest.get("upstream")
    if not isinstance(denominator, dict) or not isinstance(upstream, dict):
        raise TypeError("geometry manifest needs denominator and upstream contracts")
    token_path = _verified_upstream(repo_root, upstream, "token")
    source_path = _verified_upstream(repo_root, upstream, "source")
    cases = _validate_ids(manifest.get("required_case_ids"), denominator, "case")
    sources = _validate_ids(manifest.get("source_classes"), denominator, "source")
    contracts = _validate_ids(manifest.get("contracts"), denominator, "contract")
    controls = _validate_ids(manifest.get("required_controls"), denominator, "control")
    _validate_ids(manifest.get("required_render_input_checks"), denominator, "render_input_check")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, list) or not all(isinstance(item, dict) for item in profiles):
        raise TypeError("geometry profiles must be objects")
    profile_ids = _validate_ids([str(item.get("id")) for item in profiles], denominator, "profile")
    for profile in profiles:
        _validate_profile(profile)
    _validate_thresholds(manifest.get("thresholds", {}))
    expected_matrix = len(cases) * len(sources) * len(profile_ids) * len(contracts)
    if expected_matrix != denominator.get("matrix_count"):
        raise ValueError("geometry matrix count changed")
    if contract_hash(manifest) != denominator.get("contract_sha256"):
        raise ValueError("geometry execution contract changed")
    _validate_upstream_ids(token_path, source_path, cases, sources)
    if not controls:
        raise ValueError("geometry controls cannot be empty")
    return manifest


def _validate_profile(profile: dict[str, Any]) -> None:
    for name in ("frame_size", "storage_size"):
        value = profile.get(name)
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value
            )
        ):
            raise ValueError(f"profile {name} must contain two positive integers")
    delta = profile.get("screenshot_delta_threshold")
    envelope = profile.get("screenshot_envelope_threshold")
    distance = profile.get("screenshot_envelope_distance_px")
    if isinstance(delta, bool) or not isinstance(delta, int) or not 1 <= delta <= 255:
        raise ValueError("profile screenshot delta threshold must be in [1, 255]")
    if isinstance(envelope, bool) or not isinstance(envelope, int) or not delta <= envelope <= 255:
        raise ValueError("profile screenshot envelope threshold must be in [delta, 255]")
    if isinstance(distance, bool) or not isinstance(distance, int) or distance < 0:
        raise ValueError("profile screenshot envelope distance must be non-negative")


def _validate_upstream_ids(
    token_path: Path,
    source_path: Path,
    required_cases: tuple[str, ...],
    source_classes: tuple[str, ...],
) -> None:
    token_manifest = json.loads(token_path.read_text(encoding="utf-8"))
    upstream_required = {
        str(case["id"])
        for case in token_manifest["cases"]
        if case["expectation"] == "required-core"
    }
    if set(required_cases) != upstream_required:
        raise ValueError("Gate D cases no longer equal Gate A required core")
    source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
    supported_sources = {
        str(source["id"])
        for source in source_manifest["sources"]
        if source["kind"] != "remote-stream"
    }
    if set(source_classes) != supported_sources:
        raise ValueError("Gate D sources no longer equal Gate B supported classes")


def layer_support(result: RenderResult) -> Mask:
    points: set[Point] = set()
    for layer in result.layers:
        if layer.width <= 0 or layer.height <= 0 or layer.color & 0xFF == 0xFF:
            continue
        points.update(
            (layer.dst_x + index % layer.width, layer.dst_y + index // layer.width)
            for index, coverage in enumerate(layer.bitmap)
            if coverage
        )
    return frozenset(points)


def assess_masks(
    reference: Mask,
    observed: Mask,
    *,
    minimum_iou: float,
    maximum_distance: int,
    maximum_bounds_distance: int | None = None,
    support_reference: Mask | None = None,
) -> MaskAssessment:
    spatial_reference = reference if support_reference is None else support_reference
    reference_bounds = support_bounds(spatial_reference)
    observed_bounds = support_bounds(observed)
    if not reference or not spatial_reference or not observed:
        return MaskAssessment(
            False,
            len(reference),
            len(spatial_reference),
            len(observed),
            reference_bounds,
            observed_bounds,
            False,
            False,
            0.0,
            False,
        )
    bounds_equal = reference_bounds == observed_bounds
    bounds_limit = maximum_distance if maximum_bounds_distance is None else maximum_bounds_distance
    bounds_within = all(
        abs(reference_value - observed_value) <= bounds_limit
        for reference_value, observed_value in zip(reference_bounds, observed_bounds, strict=True)
    )
    overlap = support_iou(reference, observed)
    within_distance = supports_within(spatial_reference, observed, maximum_distance)
    return MaskAssessment(
        bounds_within and overlap >= minimum_iou and within_distance,
        len(reference),
        len(spatial_reference),
        len(observed),
        reference_bounds,
        observed_bounds,
        bounds_equal,
        bounds_within,
        overlap,
        within_distance,
    )


def shifted(mask: Mask, dx: int, dy: int) -> Mask:
    return frozenset((x + dx, y + dy) for x, y in mask)


def _profile_filter_rejects_retained_overlay(
    profile: dict[str, Any], thresholds: dict[str, Any]
) -> bool:
    width = 40
    core = frozenset((x, y) for x in range(5, 15) for y in range(5, 15))
    extra = frozenset((x, y) for x in range(25, 30) for y in range(20, 25))
    envelope_delta = int(profile["screenshot_envelope_threshold"])
    minimum_delta = int(profile["screenshot_delta_threshold"])
    deltas = bytearray(width * 30)
    for x, y in core:
        deltas[y * width + x] = envelope_delta
    for x, y in extra:
        deltas[y * width + x] = minimum_delta
    if minimum_delta < envelope_delta:
        for x in range(15, 25):
            deltas[14 * width + x] = minimum_delta
        for y in range(14, 20):
            deltas[y * width + 24] = minimum_delta
    overlap, support, unsafe = _comparison_masks(
        bytes(deltas),
        width,
        minimum_delta=minimum_delta,
        envelope_delta=envelope_delta,
        envelope_distance=int(profile["screenshot_envelope_distance_px"]),
    )
    assessment = assess_masks(
        overlap,
        core,
        minimum_iou=float(thresholds["minimum_mask_iou"]),
        maximum_distance=int(thresholds["maximum_chebyshev_distance_px"]),
        maximum_bounds_distance=int(thresholds["maximum_outer_bounds_distance_px"]),
        support_reference=support,
    )
    return bool(unsafe) or not assessment.passed


def exercise_controls(
    thresholds: dict[str, Any], profiles: Sequence[dict[str, Any]] = ()
) -> dict[str, bool]:
    reference = frozenset((x, y) for x in range(20, 40) for y in range(10, 30))
    extras = frozenset((x, y) for x in range(60, 70) for y in range(40, 50))
    background = frozenset((x, y) for x in range(80) for y in range(60))
    candidates = {
        "background-change": reference | background,
        "retained-overlay": reference | extras,
        "shifted-hit-map": shifted(reference, 2, 0),
        "blank-frame": frozenset(),
        "duplicate-layer": reference | shifted(reference, 30, 0),
    }
    controls = {
        name: not assess_masks(
            reference,
            candidate,
            minimum_iou=float(thresholds["minimum_mask_iou"]),
            maximum_distance=int(thresholds["maximum_chebyshev_distance_px"]),
        ).passed
        for name, candidate in candidates.items()
    }
    controls["screenshot-filter"] = bool(profiles) and all(
        _profile_filter_rejects_retained_overlay(profile, thresholds) for profile in profiles
    )
    return controls


def build_contract_report(manifest_path: Path, repo_root: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, repo_root=repo_root)
    controls = exercise_controls(manifest["thresholds"], manifest["profiles"])
    required = set(manifest["required_controls"])
    if set(controls) != required or not all(controls.values()):
        raise AssertionError("geometry oracle controls did not all fail")
    return {
        "schema": 1,
        "manifest_sha256": sha256(manifest_path.read_bytes()),
        "matrix_count": manifest["denominator"]["matrix_count"],
        "thresholds": manifest["thresholds"],
        "controls": controls,
        "contract_ready": True,
    }


def _d_ass_bytes(events: Sequence[str]) -> bytes:
    header = ASS_HEADER.replace("Arial", "Noto Sans JP Thin")
    return (header + "\n".join(f"Dialogue: {event}" for event in events) + "\n").encode()


def _case_inputs(repo_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    token_path = repo_root / manifest["upstream"]["token_manifest"]
    token_manifest = json.loads(token_path.read_text(encoding="utf-8"))
    required = set(manifest["required_case_ids"])
    return [case for case in token_manifest["cases"] if case["id"] in required]


def _shadow_result(
    ass_data: bytes,
    fonts: tuple[Path, ...],
    timestamp_ms: int,
    frame_size: tuple[int, int],
    storage_size: tuple[int, int],
    pixel_aspect: float,
) -> tuple[Any, Any]:
    import libasslite  # noqa: TID251 -- Gate D directly verifies the wrapper boundary.

    renderer_type: Any = libasslite.AssRenderer  # ty: ignore[unresolved-attribute]
    renderer = renderer_type(
        ass_data,
        [(font.name, font.read_bytes()) for font in fonts],
        library_path=os.environ.get("LIBASSLITE_LIBRARY"),
    )
    return renderer, renderer.render(
        timestamp_ms,
        frame_size,
        storage_size,
        pixel_aspect=pixel_aspect,
    )


def _write_clip(
    workspace: Path,
    profile: dict[str, Any],
    *,
    ffmpeg: str,
) -> Path:
    from mpv_source_transition import _run

    source_size, sample_aspect_ratio = _clip_geometry(profile)
    clip = workspace / f"{profile['id']}.mkv"
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=navy:s={source_size[0]}x{source_size[1]}:d=4",
            "-vf",
            f"setsar={sample_aspect_ratio}",
            "-pix_fmt",
            "yuv444p",
            "-c:v",
            "ffv1",
            str(clip),
        ]
    )
    return clip


def _clip_geometry(profile: dict[str, Any]) -> tuple[tuple[int, int], str]:
    frame_width, frame_height = profile["frame_size"]
    storage_width, storage_height = profile["storage_size"]
    ratio = Fraction(frame_width * storage_height, frame_height * storage_width)
    return (storage_width, storage_height), f"{ratio.numerator}/{ratio.denominator}"


def _video_pixel_aspect(profile: dict[str, Any]) -> float:
    frame_width, frame_height = profile["frame_size"]
    storage_width, storage_height = profile["storage_size"]
    return float(Fraction(frame_width * storage_height, frame_height * storage_width))


def _screenshot_delta_threshold(profile: dict[str, Any]) -> int:
    return int(profile["screenshot_delta_threshold"])


def _comparison_masks(
    deltas: bytes,
    width: int,
    *,
    minimum_delta: int,
    envelope_delta: int,
    envelope_distance: int,
) -> tuple[Mask, Mask, Mask]:
    low = frozenset(
        (index % width, index // width)
        for index, delta in enumerate(deltas)
        if delta >= minimum_delta
    )
    envelope = frozenset(
        (index % width, index // width)
        for index, delta in enumerate(deltas)
        if delta >= envelope_delta
    )
    if not envelope:
        return frozenset(), frozenset(), low
    permitted = frozenset(
        (x + dx, y + dy)
        for x, y in envelope
        for dx in range(-envelope_distance, envelope_distance + 1)
        for dy in range(-envelope_distance, envelope_distance + 1)
    )
    return low & permitted, envelope, low - permitted


def _comparison_mask(
    deltas: bytes,
    width: int,
    *,
    minimum_delta: int,
    envelope_delta: int,
    envelope_distance: int,
) -> Mask:
    comparison, _, _ = _comparison_masks(
        deltas,
        width,
        minimum_delta=minimum_delta,
        envelope_delta=envelope_delta,
        envelope_distance=envelope_distance,
    )
    return comparison


def _capture_mask(
    ipc: Any,
    workspace: Path,
    label: str,
    *,
    minimum_delta: int,
    envelope_delta: int,
    envelope_distance: int,
) -> tuple[Mask, Mask, Mask, tuple[int, int], dict[str, dict[str, Any]]]:
    from mpv_source_transition import _wait_for
    from PIL import Image, ImageChops

    video_path = workspace / f"{label}-video.png"
    subtitle_path = workspace / f"{label}-subtitle.png"
    for path, mode in ((video_path, "video"), (subtitle_path, "subtitles")):
        reply = ipc.command("screenshot-to-file", str(path), mode, timeout=10)
        if reply.get("error") != "success":
            raise AssertionError(f"mpv screenshot {mode} failed: {reply.get('error')}")
        _wait_for(path.is_file, timeout=5.0, message=f"mpv did not write {path.name}")
    with Image.open(video_path) as video_image, Image.open(subtitle_path) as subtitle_image:
        video = video_image.convert("RGB")
        subtitle = subtitle_image.convert("RGB")
        if video.size != subtitle.size:
            raise AssertionError("mpv screenshot dimensions changed")
        difference = ImageChops.difference(video, subtitle)
        width, height = difference.size
        red, green, blue = difference.split()
        peak = ImageChops.lighter(red, ImageChops.lighter(green, blue))
        deltas = peak.tobytes()
        mask, support_mask, unsafe_low_delta_mask = _comparison_masks(
            deltas,
            width,
            minimum_delta=minimum_delta,
            envelope_delta=envelope_delta,
            envelope_distance=envelope_distance,
        )
        histogram = peak.histogram()
        threshold_support = {
            str(threshold): {
                "pixels": sum(histogram[threshold:]),
                "bounds": list(
                    peak.point([0] * threshold + [255] * (256 - threshold)).getbbox()
                    or (0, 0, 0, 0)
                ),
            }
            for threshold in (1, 2, 4, 8, 16)
        }
        return mask, support_mask, unsafe_low_delta_mask, (width, height), threshold_support


def _mpv_render_inputs(ipc: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for name in (
        "video-out-params",
        "options/sub-ass-override",
        "options/sub-ass-scale-with-window",
        "options/sub-scale",
        "options/sub-pos",
    ):
        reply = ipc.command("get_property", name)
        if reply.get("error") != "success":
            raise AssertionError(f"mpv did not expose required render input {name}")
        inputs[name] = reply.get("data")
    return inputs


class _MpvSession:
    def __init__(
        self,
        workspace: Path,
        clip: Path,
        fonts: tuple[Path, ...],
        *,
        mpv: str,
    ) -> None:
        from saitenka.mpvio.ipc import MpvIPC, default_ipc_path

        self.workspace = workspace
        self.endpoint = default_ipc_path(f"gate-d-{os.getpid()}-{time.monotonic_ns()}")
        command = [
            mpv,
            f"--input-ipc-server={self.endpoint}",
            "--no-config",
            "--sub-auto=no",
            "--vo=null",
            "--ao=null",
            "--keep-open=yes",
            "--pause",
            "--start=1.5",
            "--sub-ass-override=no",
            "--sub-ass-scale-with-window=no",
            "--sub-scale=1",
            "--sub-pos=100",
        ]
        if fonts:
            font_dir = _materialize_fonts(workspace, fonts)
            command.append(f"--sub-fonts-dir={font_dir}")
        command.append(str(clip))
        self.log_path = workspace / f"mpv-{time.monotonic_ns()}.log"
        self.log = self.log_path.open("wb")
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT)
        try:
            self.ipc = MpvIPC(self.endpoint).connect(timeout=15)
        except Exception as error:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.log.close()
            diagnostic = self.log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"mpv did not expose Gate D IPC: {error}\n{diagnostic}") from error

    def capture(
        self,
        ass_path: Path,
        timestamp_ms: int,
        label: str,
        *,
        minimum_delta: int,
        envelope_delta: int,
        envelope_distance: int,
    ) -> tuple[Mask, Mask, Mask, tuple[int, int], dict[str, dict[str, Any]], dict[str, Any]]:
        from mpv_source_transition import _track_for_path, _wait_for

        reply = self.ipc.command("sub-add", str(ass_path), "select", label, "jpn")
        if reply.get("error") != "success":
            raise AssertionError(f"mpv rejected Gate D ASS: {reply.get('error')}")
        track = _wait_for(
            lambda: _track_for_path(self.ipc, ass_path),
            timeout=5.0,
            message=f"mpv did not expose {label}",
        )
        sid = int(track["id"])
        try:
            for name, value in (("sid", sid), ("time-pos", timestamp_ms / 1000)):
                reply = self.ipc.command("set_property", name, value)
                if reply.get("error") != "success":
                    raise AssertionError(f"mpv rejected {name}={value!r}")
            _wait_for(
                lambda: self.ipc.command("get_property", "sub-text").get("data"),
                timeout=3.0,
                message=f"mpv did not render {label}",
            )
            mask, support_mask, unsafe_low_delta_mask, size, threshold_support = _capture_mask(
                self.ipc,
                self.workspace,
                label,
                minimum_delta=minimum_delta,
                envelope_delta=envelope_delta,
                envelope_distance=envelope_distance,
            )
            return (
                mask,
                support_mask,
                unsafe_low_delta_mask,
                size,
                threshold_support,
                _mpv_render_inputs(self.ipc),
            )
        finally:
            reply = self.ipc.command("sub-remove", sid)
            if reply.get("error") != "success":
                raise AssertionError(f"mpv did not remove Gate D track {sid}")

    def close(self) -> str:
        try:
            self.ipc.command("quit")
        except OSError:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.ipc.close()
        self.log.close()
        diagnostic = self.log_path.read_text(encoding="utf-8", errors="replace")
        _validate_mpv_exit(self.process.returncode, diagnostic)
        return diagnostic


def _validate_mpv_exit(returncode: int | None, diagnostic: str) -> None:
    if returncode != 0:
        raise RuntimeError(f"Gate D mpv exited with {returncode}\n{diagnostic}")


def _materialize_fonts(workspace: Path, fonts: tuple[Path, ...]) -> Path:
    font_dir = workspace / f"fonts-{time.monotonic_ns()}"
    font_dir.mkdir()
    for font in fonts:
        (font_dir / font.name).write_bytes(font.read_bytes())
    return font_dir


def _source_groups(repo_root: Path) -> tuple[tuple[tuple[str, ...], tuple[Path, ...]], ...]:
    font = repo_root / "src/saitenka/assets/fonts/NotoSansJP.ttf"
    return (
        (("external-ass",), ()),
        (("synthetic-ass-from-text",), ()),
        (("embedded-ass-with-font",), (font,)),
    )


def _palette(case: dict[str, Any]) -> list[tuple[int, TokenKey]]:
    return [
        (int(item["rgb"], 16), TokenKey(str(item["event_id"]), int(item["token_index"])))
        for item in case["palette"]
    ]


def _render_input_checks(
    render_inputs: dict[str, Any],
    frame_size: tuple[int, ...],
    storage_size: tuple[int, ...],
    observed_size: tuple[int, int],
    video_pixel_aspect: float,
) -> dict[str, bool]:
    video = render_inputs["video-out-params"]
    return {
        "screenshot-frame-size": observed_size == frame_size,
        "video-display-size": (video.get("dw"), video.get("dh")) == frame_size,
        "video-storage-size": (video.get("w"), video.get("h")) == storage_size,
        "video-pixel-aspect": isinstance(video.get("par"), (int, float))
        and not isinstance(video.get("par"), bool)
        and abs(float(video["par"]) - video_pixel_aspect) <= 1e-6,
        "lossless-444-capture": video.get("pixelformat") == "yuv444p",
        "authored-ass-style": render_inputs["options/sub-ass-override"] in {False, "no"},
        "ass-window-scale-disabled": render_inputs["options/sub-ass-scale-with-window"] is False,
        "subtitle-scale": render_inputs["options/sub-scale"] == 1.0,
        "subtitle-position": render_inputs["options/sub-pos"] == 100.0,
    }


def _evaluate_cell(
    session: _MpvSession,
    workspace: Path,
    case: dict[str, Any],
    profile: dict[str, Any],
    sources: tuple[str, ...],
    fonts: tuple[Path, ...],
    contract: str,
    mpv_event_key: str,
    shadow_event_key: str,
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    frame_size = tuple(profile["frame_size"])
    storage_size = tuple(profile["storage_size"])
    video_pixel_aspect = _video_pixel_aspect(profile)
    screenshot_delta = _screenshot_delta_threshold(profile)
    screenshot_envelope = int(profile["screenshot_envelope_threshold"])
    screenshot_envelope_distance = int(profile["screenshot_envelope_distance_px"])
    mpv_ass_data = _d_ass_bytes(case[mpv_event_key])
    shadow_ass_data = _d_ass_bytes(case[shadow_event_key])
    label = f"{profile['id']}-{sources[0]}-{case['id']}-{contract}"
    ass_path = workspace / f"{label}.ass"
    ass_path.write_bytes(mpv_ass_data)
    (
        mpv_mask,
        mpv_support_mask,
        unsafe_low_delta_mask,
        observed_size,
        difference_threshold_support,
        render_inputs,
    ) = session.capture(
        ass_path,
        int(case["timestamp_ms"]),
        label,
        minimum_delta=screenshot_delta,
        envelope_delta=screenshot_envelope,
        envelope_distance=screenshot_envelope_distance,
    )
    renderer, result = _shadow_result(
        shadow_ass_data,
        fonts,
        int(case["timestamp_ms"]),
        frame_size,
        storage_size,
        video_pixel_aspect,
    )
    try:
        shadow_mask = layer_support(result)
        boxes = (
            len(extract_token_geometry(result, _palette(case)))
            if contract == "interactive-styled"
            else None
        )
        assessment = assess_masks(
            mpv_mask,
            shadow_mask,
            minimum_iou=float(thresholds["minimum_mask_iou"]),
            maximum_distance=int(thresholds["maximum_chebyshev_distance_px"]),
            maximum_bounds_distance=int(thresholds["maximum_outer_bounds_distance_px"]),
            support_reference=mpv_support_mask,
        )
        input_checks = _render_input_checks(
            render_inputs,
            frame_size,
            storage_size,
            observed_size,
            video_pixel_aspect,
        )
        assessment = replace(
            assessment,
            passed=assessment.passed and not unsafe_low_delta_mask and all(input_checks.values()),
        )
        return [
            {
                "case_id": case["id"],
                "source_class": source,
                "profile_id": profile["id"],
                "contract": contract,
                "shared_render_sources": list(sources),
                "mpv_ass_sha256": sha256(mpv_ass_data),
                "shadow_ass_sha256": sha256(shadow_ass_data),
                "expected_frame_size": list(frame_size),
                "observed_frame_size": list(observed_size),
                "expected_video_pixel_aspect": video_pixel_aspect,
                "expected_renderer_pixel_aspect": video_pixel_aspect,
                "screenshot_delta_threshold": screenshot_delta,
                "screenshot_envelope_threshold": screenshot_envelope,
                "screenshot_envelope_distance": screenshot_envelope_distance,
                "frame_size_matches": input_checks["screenshot-frame-size"],
                "token_box_count": boxes,
                "mpv_difference_threshold_support": difference_threshold_support,
                "unsafe_low_delta_pixels": len(unsafe_low_delta_mask),
                "unsafe_low_delta_bounds": list(support_bounds(unsafe_low_delta_mask)),
                "mpv_render_inputs": render_inputs,
                "render_input_checks": input_checks,
                "assessment": asdict(assessment),
            }
            for source in sources
        ]
    finally:
        renderer.close()


def _run_session_matrix(
    workspace: Path,
    clip: Path,
    cases: list[dict[str, Any]],
    profile: dict[str, Any],
    sources: tuple[str, ...],
    fonts: tuple[Path, ...],
    thresholds: dict[str, Any],
    *,
    mpv: str,
) -> tuple[list[dict[str, Any]], str]:
    session = _MpvSession(workspace, clip, fonts, mpv=mpv)
    reports: list[dict[str, Any]] = []
    try:
        for case in cases:
            for contract, mpv_event_key, shadow_event_key in (
                ("native-fidelity", "visible_events", "visible_events"),
                ("interactive-styled", "visible_events", "id_events"),
            ):
                reports.extend(
                    _evaluate_cell(
                        session,
                        workspace,
                        case,
                        profile,
                        sources,
                        fonts,
                        contract,
                        mpv_event_key,
                        shadow_event_key,
                        thresholds,
                    )
                )
    finally:
        log = session.close()
    return reports, log


def run_live_matrix(
    manifest_path: Path,
    repo_root: Path,
    *,
    mpv: str = "mpv",
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, repo_root=repo_root)
    cases = _case_inputs(repo_root, manifest)
    thresholds = manifest["thresholds"]
    reports: list[dict[str, Any]] = []
    logs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="saitenka-gate-d-") as raw_workspace:
        workspace = Path(raw_workspace)
        for profile in manifest["profiles"]:
            clip = _write_clip(workspace, profile, ffmpeg=ffmpeg)
            for sources, fonts in _source_groups(repo_root):
                group_reports, log = _run_session_matrix(
                    workspace,
                    clip,
                    cases,
                    profile,
                    sources,
                    fonts,
                    thresholds,
                    mpv=mpv,
                )
                reports.extend(group_reports)
                logs.append(log)
    expected = int(manifest["denominator"]["matrix_count"])
    if len(reports) != expected:
        raise AssertionError(f"Gate D emitted {len(reports)} matrix rows, expected {expected}")
    expected_checks = set(manifest["required_render_input_checks"])
    if any(set(report["render_input_checks"]) != expected_checks for report in reports):
        raise AssertionError("Gate D render-input checks changed")
    passed = all(report["assessment"]["passed"] for report in reports)
    return {
        **build_contract_report(manifest_path, repo_root),
        "platform": platform.platform(),
        "mpv_version": subprocess.run(
            [mpv, "--version"], check=True, capture_output=True, text=True
        ).stdout.splitlines()[0],
        "matrix_passed": passed,
        "cases": reports,
        "mpv_logs": logs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mpv", default="mpv")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    try:
        report = (
            run_live_matrix(
                args.manifest,
                args.repo_root,
                mpv=args.mpv,
                ffmpeg=args.ffmpeg,
            )
            if args.live
            else build_contract_report(args.manifest, args.repo_root)
        )
    except Exception as error:
        report = {"schema": 1, "matrix_passed": False, "error": str(error)}
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        raise
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.live and not report["matrix_passed"]:
        raise SystemExit("Gate D geometry matrix failed")


if __name__ == "__main__":
    main()
