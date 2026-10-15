from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROTOCOL = "saitenka.history/v0"
CAPABILITY = "history-v0"
ANSWER_KEYS = frozenset({"expect"})
MODES = frozenset({"backlog", "session-stats"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "cases", "expect"}
        or scenario.get("mode") not in MODES
    ):
        raise ValueError("invalid history-v0 scenario")
    return {
        "type": "run",
        "protocol": PROTOCOL,
        **{key: value for key, value in scenario.items() if key not in ANSWER_KEYS},
    }


def validate_request(request: dict) -> None:
    if (
        ANSWER_KEYS & request.keys()
        or request.get("type") != "run"
        or request.get("protocol") != PROTOCOL
        or request.get("mode") not in MODES
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid history-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    evaluator = _backlog if request["mode"] == "backlog" else _session_stats
    return [evaluator(case) for case in request["cases"]]


def _capture(root: Path, spec: dict):
    from saitenka.app.backlog import Capture

    return Capture(
        video_path=str(root / spec["video"]),
        cue_start=spec.get("start", 1.0),
        cue_end=spec.get("end", 3.0),
        jp_text=spec.get("jp", "猫です"),
        en_text=spec.get("en", "It is a cat."),
        subtitle_track=spec.get("track", {"jp_sid": 1, "en_sid": 2}),
        hovered_surface=spec.get("surface", "猫"),
        hovered_lemma=spec.get("lemma", "猫"),
    )


def _backlog(case: dict) -> object:
    from saitenka.app.backlog import BacklogStore, normalize_match_name

    if case["op"] == "normalize":
        return [normalize_match_name(path) for path in case["paths"]]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db = root / "backlog.sqlite"
        op = case["op"]
        if op == "persistence":
            with BacklogStore(db, clock=lambda: 10.0) as store:
                ids = [store.toggle_capture(_capture(root, spec)).id for spec in case["captures"]]
            with BacklogStore(db, clock=lambda: 20.0) as store:
                return {
                    "ids": ids,
                    "texts": [store.entry(item).jp_text for item in ids],
                    "media_count": len(store.all_media()),
                }
        with BacklogStore(db, clock=iter(case.get("ticks", [10.0] * 20)).__next__) as store:
            if op == "move-match":
                original = root / case["original"]
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(b"video")
                entry = store.toggle_capture(_capture(root, {"video": case["original"]}))
                result = store.match(root / case["moved"])
                assert result.media is not None
                return {
                    "kind": result.kind,
                    "source": result.source,
                    "original_basename": result.media.original_basename,
                    "last_known_name": Path(result.media.last_known_path).name,
                    "entry_ids": [item.id for item in store.entries_for_path(root / case["moved"])],
                    "saved_id": entry.id,
                }
            if op == "metadata":
                video = root / case["video"]
                video.write_bytes(case["content"].encode())
                media = store.ensure_media(video)
                video.unlink()
                saved = store.media(media.id)
                return {
                    "file_size": saved.file_size,
                    "has_mtime": saved.mtime is not None,
                    "stem": saved.original_stem,
                    "basename": saved.original_basename,
                }
            if op == "text-export":
                video = root / case["capture"]["video"]
                video.write_bytes(b"video")
                entry = store.toggle_capture(_capture(root, case["capture"]))
                video.unlink()
                return store.text_export(entry.id)
            if op == "relink":
                entry = store.toggle_capture(_capture(root, {"video": case["original"]}))
                target = root / case["renamed"]
                before = store.match(target)
                empty = store.entries_for_path(target)
                linked = store.relink(entry.media_id, target)
                return {
                    "before": before.kind,
                    "before_entries": len(empty),
                    "original_basename": linked.original_basename,
                    "last_known_name": Path(linked.last_known_path).name,
                    "after_entries": [item.id for item in store.entries_for_path(target)],
                }
            if op == "ambiguous":
                common = root / case["common"]
                media = [store.ensure_media(root / name) for name in case["videos"]]
                for item, alias in zip(media, case["aliases"], strict=True):
                    store.relink(item.id, root / alias)
                result = store.match(common)
                return {
                    "kind": result.kind,
                    "choices": [item.original_basename for item in result.choices],
                    "entries": len(store.entries_for_path(common)),
                }
            if op == "status-history":
                entry = store.toggle_capture(_capture(root, case["capture"]))
                ids = [entry.id]
                statuses = [entry.status]
                for _ in range(2):
                    toggled = store.toggle_capture(_capture(root, case["capture"]))
                    ids.append(toggled.id)
                    statuses.append(toggled.status)
                for status in case["statuses"]:
                    changed = store.set_status(entry.id, status)
                    ids.append(changed.id)
                    statuses.append(changed.status)
                return {
                    "ids_same": len(set(ids)) == 1,
                    "statuses": statuses,
                    "history": store.status_history(entry.id),
                }
            if op == "summary":
                store.toggle_capture(_capture(root, case["capture"]))
                return store.summary()
    raise ValueError(f"unknown backlog operation: {case['op']!r}")


class _Writer:
    def __init__(self) -> None:
        self.snapshots = []
        self.closed = False

    def submit(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def close(self, _timeout: float = 2.0) -> None:
        self.closed = True


def _session_stats(case: dict) -> object:
    from saitenka.app.session_stats import SessionRecorder, analysis_snapshot, start, summary

    op = case["op"]
    if op == "analysis-snapshot":
        return json.loads(analysis_snapshot(SimpleNamespace(**case["result"])))
    if op == "disabled":
        touched = False

        def path():
            nonlocal touched
            touched = True
            return "/video.mkv"

        return {
            "recorder": start(current=None, enabled=False, path=path, arm=lambda _seconds: None)
            is not None,
            "path_touched": touched,
        }
    clocks = iter(case["monotonic"])
    walls = iter(case["wall"])
    writer = _Writer()
    recorder = SessionRecorder(
        case["media"], clock=clocks.__next__, wall_clock=walls.__next__, writer=writer
    )
    if op == "revise":
        recorder.record_cue(tuple(case["first"]))
        recorder.revise_cue(tuple(case["revised"]))
        recorder.record_cue(tuple(case["revised"]))
        recorder.finish()
        return recorder.snapshot.cue_count
    for event in case["events"]:
        kind = event["op"]
        if kind == "accrue":
            recorder.accrue(paused=event["paused"], language=event["language"])
        elif kind == "cue":
            recorder.record_cue(tuple(event["identity"]))
        elif kind == "lookup":
            recorder.record_lookup()
        elif kind == "capture":
            recorder.record_capture()
        elif kind == "mined":
            recorder.record_mined(event["count"])
    result = recorder.finish()
    return {
        "media_name": result.media_name,
        "title": result.title,
        "title_match": result.title_match,
        "episode": result.episode,
        "ended_at": result.ended_at,
        "completed": result.completed,
        "watch_seconds": result.watch_seconds,
        "jp_seconds": result.jp_seconds,
        "en_seconds": result.en_seconds,
        "cue_count": result.cue_count,
        "lookup_count": result.lookup_count,
        "capture_count": result.capture_count,
        "mined_count": result.mined_count,
        "writer_closed": writer.closed,
        "summary": summary(result),
    }
