"""Persistent deferred captures for uninterrupted viewing."""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from saitenka import otel_metrics
from saitenka.app import paths
from saitenka.app.jimaku import parse_filename
from saitenka.app.languages import MAIN_LANG
from saitenka.sqlite_pool import close_when_collected, open_owner_connection

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.tokenize import Token

Status = Literal["open", "reviewed", "mined", "archived"]
MatchKind = Literal["matched", "ambiguous", "candidate", "unmatched"]
STATUSES: tuple[Status, ...] = ("open", "reviewed", "mined", "archived")
_DB_PATH_OVERRIDE: Path | None = None


def db_path() -> Path:
    return _DB_PATH_OVERRIDE or paths.data_dir() / "backlog.sqlite"


def normalize_match_name(path: str | Path) -> str:
    """Normalize a full basename without discarding release-identifying details."""
    return unicodedata.normalize("NFC", Path(path).name).casefold()


def normalize_title(title: str) -> str:
    """Fold a parsed title to its match key; SSOT so #24 stats and #64 backlog agree."""
    return unicodedata.normalize("NFC", title).casefold().strip()


def _path_text(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _track_metadata(tracks: list[object], sid: int | None) -> dict[str, object]:
    raw = next((item for item in tracks if isinstance(item, dict) and item.get("id") == sid), {})
    track = cast("dict[str, object]", raw)
    keys = ("id", "lang", "title", "codec", "external-filename")
    return {key: track[key] for key in keys if key in track}


def _cue_languages(primary: str, secondary: str, language: str) -> tuple[str, str]:
    """A bookmark's (japanese, english) pair. Which side the ON-SCREEN cue is depends on which track
    is primary — with English primary the roles swap, and storing them by position rather than by
    language would file an English line as the Japanese one."""
    if language == MAIN_LANG:
        return primary, secondary
    return secondary, primary


@dataclass(frozen=True)
class Capture:
    video_path: str
    cue_start: float
    cue_end: float
    jp_text: str = ""
    en_text: str = ""
    subtitle_track: dict[str, object] | None = None
    hovered_surface: str | None = None
    hovered_lemma: str | None = None


@dataclass(frozen=True)
class MediaRecord:
    id: int
    original_path: str
    original_basename: str
    last_known_path: str
    match_name: str
    original_stem: str
    file_size: int | None
    mtime: float | None
    title: str
    episode: int | None


@dataclass(frozen=True)
class BacklogEntry:
    id: int
    media_id: int
    cue_start: float
    cue_end: float
    jp_text: str
    en_text: str
    subtitle_track: dict[str, object]
    hovered_surface: str | None
    hovered_lemma: str | None
    status: Status
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class MatchResult:
    kind: MatchKind
    media: MediaRecord | None = None
    choices: tuple[MediaRecord, ...] = ()
    source: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.kind == "matched" and self.media is not None


class BacklogStore:
    """One SQLite connection owned by the SessionController thread."""

    def __init__(self, path: str | Path | None = None, *, clock: Callable[[], float] = time.time):
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._con = open_owner_connection(self.path)
        close_when_collected(self, self._con)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY,
                original_path TEXT NOT NULL,
                original_basename TEXT NOT NULL,
                last_known_path TEXT NOT NULL,
                match_name TEXT NOT NULL,
                original_stem TEXT NOT NULL,
                file_size INTEGER,
                mtime REAL,
                title TEXT NOT NULL,
                title_match TEXT NOT NULL,
                episode INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS media_original_path ON media(original_path);
            CREATE INDEX IF NOT EXISTS media_match_name ON media(match_name);
            CREATE INDEX IF NOT EXISTS media_episode ON media(title_match, episode);

            CREATE TABLE IF NOT EXISTS media_alias (
                media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                match_name TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (media_id, match_name)
            );
            CREATE INDEX IF NOT EXISTS media_alias_match_name ON media_alias(match_name);

            CREATE TABLE IF NOT EXISTS entry (
                id INTEGER PRIMARY KEY,
                media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                cue_start REAL NOT NULL,
                cue_end REAL NOT NULL,
                jp_text TEXT NOT NULL,
                en_text TEXT NOT NULL,
                subtitle_track_json TEXT NOT NULL,
                hovered_surface TEXT,
                hovered_lemma TEXT,
                status TEXT NOT NULL CHECK(status IN ('open', 'reviewed', 'mined', 'archived')),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(media_id, cue_start, cue_end, jp_text, en_text)
            );
            CREATE INDEX IF NOT EXISTS entry_media_status ON entry(media_id, status);

            CREATE TABLE IF NOT EXISTS status_history (
                id INTEGER PRIMARY KEY,
                entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('open', 'reviewed', 'mined', 'archived')),
                changed_at REAL NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> BacklogStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _media(row: sqlite3.Row) -> MediaRecord:
        return MediaRecord(
            id=row["id"],
            original_path=row["original_path"],
            original_basename=row["original_basename"],
            last_known_path=row["last_known_path"],
            match_name=row["match_name"],
            original_stem=row["original_stem"],
            file_size=row["file_size"],
            mtime=row["mtime"],
            title=row["title"],
            episode=row["episode"],
        )

    @staticmethod
    def _entry(row: sqlite3.Row) -> BacklogEntry:
        return BacklogEntry(
            id=row["id"],
            media_id=row["media_id"],
            cue_start=row["cue_start"],
            cue_end=row["cue_end"],
            jp_text=row["jp_text"],
            en_text=row["en_text"],
            subtitle_track=json.loads(row["subtitle_track_json"]),
            hovered_surface=row["hovered_surface"],
            hovered_lemma=row["hovered_lemma"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def media(self, media_id: int) -> MediaRecord:
        row = self._con.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            raise KeyError(media_id)
        return self._media(row)

    def all_media(self) -> list[MediaRecord]:
        rows = self._con.execute("SELECT * FROM media ORDER BY id").fetchall()
        return [self._media(row) for row in rows]

    def _records_for_name(self, name: str) -> list[MediaRecord]:
        rows = self._con.execute(
            """
            SELECT DISTINCT m.* FROM media AS m
            LEFT JOIN media_alias AS a ON a.media_id = m.id
            WHERE m.match_name = ? OR a.match_name = ?
            ORDER BY m.id
            """,
            (name, name),
        ).fetchall()
        return [self._media(row) for row in rows]

    def match(self, path: str | Path, *, update_last_known: bool = True) -> MatchResult:
        current = _path_text(path)
        exact = self._con.execute(
            "SELECT * FROM media WHERE original_path = ? OR last_known_path = ? ORDER BY id",
            (current, current),
        ).fetchall()
        if len(exact) == 1:
            return MatchResult("matched", self._media(exact[0]), source="path")
        if len(exact) > 1:
            choices = tuple(self._media(row) for row in exact)
            return MatchResult("ambiguous", choices=choices, source="path")

        by_name = self._records_for_name(normalize_match_name(current))
        if len(by_name) == 1:
            media = by_name[0]
            if update_last_known and media.last_known_path != current:
                now = self._clock()
                self._con.execute(
                    "UPDATE media SET last_known_path = ?, updated_at = ? WHERE id = ?",
                    (current, now, media.id),
                )
                self._con.commit()
                media = self.media(media.id)
            return MatchResult("matched", media, source="basename")
        if len(by_name) > 1:
            return MatchResult("ambiguous", choices=tuple(by_name), source="basename")

        title, episode = parse_filename(current)
        rows = self._con.execute(
            "SELECT * FROM media WHERE title_match = ? AND episode IS ? ORDER BY id",
            (normalize_title(title), episode),
        ).fetchall()
        if rows:
            return MatchResult(
                "candidate", choices=tuple(self._media(row) for row in rows), source="title_episode"
            )
        return MatchResult("unmatched")

    def _create_media(self, path: str | Path) -> MediaRecord:
        current = _path_text(path)
        p = Path(current)
        try:
            stat = p.stat()
        except OSError:
            size = mtime = None
        else:
            size, mtime = stat.st_size, stat.st_mtime
        title, episode = parse_filename(p.name)
        now = self._clock()
        cur = self._con.execute(
            """
            INSERT INTO media (
                original_path, original_basename, last_known_path, match_name, original_stem,
                file_size, mtime, title, title_match, episode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current,
                p.name,
                current,
                normalize_match_name(p),
                p.stem,
                size,
                mtime,
                title,
                normalize_title(title),
                episode,
                now,
                now,
            ),
        )
        self._con.commit()
        media_id = cur.lastrowid
        assert media_id is not None
        return self.media(media_id)

    def ensure_media(self, path: str | Path) -> MediaRecord:
        result = self.match(path)
        if result.confirmed:
            assert result.media is not None
            return result.media
        return self._create_media(path)

    def relink(self, media_id: int, path: str | Path) -> MediaRecord:
        """Explicitly confirm a renamed candidate without changing its original identity."""
        self.media(media_id)  # loud failure before writing an orphan alias
        current = _path_text(path)
        now = self._clock()
        self._con.execute(
            "INSERT OR IGNORE INTO media_alias VALUES (?, ?, ?, ?)",
            (media_id, Path(current).name, normalize_match_name(current), now),
        )
        self._con.execute(
            "UPDATE media SET last_known_path = ?, updated_at = ? WHERE id = ?",
            (current, now, media_id),
        )
        self._con.commit()
        return self.media(media_id)

    def toggle_capture_result(self, capture: Capture) -> tuple[BacklogEntry, bool]:
        media = self.ensure_media(capture.video_path)
        row = self._con.execute(
            """
            SELECT * FROM entry
            WHERE media_id = ? AND cue_start = ? AND cue_end = ? AND jp_text = ? AND en_text = ?
            """,
            (
                media.id,
                capture.cue_start,
                capture.cue_end,
                capture.jp_text,
                capture.en_text,
            ),
        ).fetchone()
        now = self._clock()
        if row is not None:
            status: Status = "archived" if row["status"] == "open" else "open"
            self._set_status(row["id"], status, now)
            return self.entry(row["id"]), False
        cur = self._con.execute(
            """
            INSERT INTO entry (
                media_id, cue_start, cue_end, jp_text, en_text, subtitle_track_json,
                hovered_surface, hovered_lemma, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                media.id,
                capture.cue_start,
                capture.cue_end,
                capture.jp_text,
                capture.en_text,
                json.dumps(capture.subtitle_track or {}, ensure_ascii=False, sort_keys=True),
                capture.hovered_surface,
                capture.hovered_lemma,
                now,
                now,
            ),
        )
        entry_id = cur.lastrowid
        assert entry_id is not None
        self._con.execute(
            "INSERT INTO status_history(entry_id, status, changed_at) VALUES (?, 'open', ?)",
            (entry_id, now),
        )
        self._con.commit()
        return self.entry(entry_id), True

    def toggle_capture(self, capture: Capture) -> BacklogEntry:
        entry, _created = self.toggle_capture_result(capture)
        return entry

    def _set_status(self, entry_id: int, status: Status, now: float) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown backlog status: {status}")
        cur = self._con.execute(
            "UPDATE entry SET status = ?, updated_at = ? WHERE id = ?", (status, now, entry_id)
        )
        if cur.rowcount != 1:
            raise KeyError(entry_id)
        self._con.execute(
            "INSERT INTO status_history(entry_id, status, changed_at) VALUES (?, ?, ?)",
            (entry_id, status, now),
        )
        self._con.commit()

    def set_status(self, entry_id: int, status: Status) -> BacklogEntry:
        self._set_status(entry_id, status, self._clock())
        return self.entry(entry_id)

    def entry(self, entry_id: int) -> BacklogEntry:
        row = self._con.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return self._entry(row)

    def status_history(self, entry_id: int) -> list[Status]:
        rows = self._con.execute(
            "SELECT status FROM status_history WHERE entry_id = ? ORDER BY id", (entry_id,)
        ).fetchall()
        return [row["status"] for row in rows]

    def entries_for_media(self, media_id: int) -> list[BacklogEntry]:
        rows = self._con.execute(
            "SELECT * FROM entry WHERE media_id = ? ORDER BY cue_start, id", (media_id,)
        ).fetchall()
        return [self._entry(row) for row in rows]

    def entries_for_path(self, path: str | Path) -> list[BacklogEntry]:
        """Return cue content only after an exact, alias, or unique-basename match."""
        result = self.match(path)
        if not result.confirmed or result.media is None:
            return []
        return self.entries_for_media(result.media.id)

    def text_export(self, entry_id: int) -> str:
        """Media-independent text payload for clipboard/export UI."""
        entry = self.entry(entry_id)
        return "\n".join(text for text in (entry.jp_text, entry.en_text) if text)

    def summary(self) -> list[dict[str, object]]:
        """Global counts expose identity/status, never cue text."""
        rows = self._con.execute(
            """
            SELECT m.id, m.original_basename, e.status, COUNT(e.id) AS count
            FROM media AS m LEFT JOIN entry AS e ON e.media_id = m.id
            GROUP BY m.id, e.status ORDER BY m.id, e.status
            """
        ).fetchall()
        return [dict(row) for row in rows]


@dataclass(frozen=True, slots=True)
class CapturePorts:
    """What one bookmark toggle samples the cue from, and where it reports.

    Everything but `store` is read once, at the top: the whole point of the value is that the cue
    it writes is the cue that was on screen when the key was pressed. `store` stays a callable
    because opening the SQLite handle is lazy — a session that never bookmarks never opens one.
    """

    video: object
    #: mpv reports cue bounds as numbers, `None` before one is on screen.
    start: float | None
    end: float | None
    text: str
    secondary_text: str
    language: str
    tokens: list[Token]
    hover: int
    jp_sid: int | None
    en_sid: int | None
    tracks: list
    store: Callable[[], BacklogStore]
    toast: Callable[..., None]
    record_capture: Callable[[], None]


def capture_current(ports: CapturePorts) -> BacklogEntry | None:
    """Sample current cue metadata, then perform one local transaction."""
    if not ports.video or ports.start is None or ports.end is None or not ports.text.strip():
        return None  # `mine_intents` owns the eligibility decision and its announcement

    jp_text, en_text = _cue_languages(ports.text, ports.secondary_text, ports.language)
    hovered = ports.tokens[ports.hover] if 0 <= ports.hover < len(ports.tokens) else None
    main = ports.language == MAIN_LANG
    capture = Capture(
        video_path=str(ports.video),
        cue_start=float(ports.start),
        cue_end=float(ports.end),
        jp_text=jp_text,
        en_text=en_text,
        subtitle_track={
            "language": ports.language,
            "primary_sid": ports.jp_sid if main else ports.en_sid,
            "secondary_sid": ports.en_sid if main else ports.jp_sid,
            "jp_sid": ports.jp_sid,
            "en_sid": ports.en_sid,
            "jp_track": _track_metadata(ports.tracks, ports.jp_sid),
            "en_track": _track_metadata(ports.tracks, ports.en_sid),
        },
        hovered_surface=hovered.surface if hovered else None,
        hovered_lemma=hovered.lemma if hovered else None,
    )
    try:
        with otel_metrics.traced("backlog_write", op="toggle"):  # main-thread SQLite on a bookmark
            entry, created = ports.store().toggle_capture_result(capture)
    except (OSError, sqlite3.Error, ValueError) as exc:
        ports.toast(f"bookmark failed: {exc}", "err")
        return None
    state = "saved" if entry.status == "open" else entry.status
    if created:
        ports.record_capture()
    ports.toast(f"bookmark {state}")
    return entry
