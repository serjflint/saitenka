"""Bounded reads of untrusted local diagnostic artifacts; never extract an archive."""

from __future__ import annotations

import stat
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 256


def read_file(path: Path, *, limit: int = MAX_MEMBER_BYTES) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("diagnostic member exceeds byte limit")
    return raw.decode("utf-8", errors="replace")


def _safe_name(name: str) -> bool:
    parts = name.rstrip("/").split("/")
    return (
        bool(name)
        and not name.startswith("/")
        and not any(part in {"", "..", "."} or ":" in part or "\\" in part for part in parts)
    )


def _validate_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ValueError("diagnostic archive exceeds member limit")
    if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
        raise ValueError("diagnostic archive exceeds expanded byte limit")
    names = [item.filename for item in members]
    if len(names) != len(set(names)):
        raise ValueError("ambiguous duplicate diagnostic member")
    if any(
        not _safe_name(item.filename) or stat.S_ISLNK(item.external_attr >> 16) for item in members
    ):
        raise ValueError("unsafe diagnostic member path")
    return members


def read_member(source: Path, name: str, *, limit: int = MAX_MEMBER_BYTES) -> str | None:
    if not _safe_name(name):
        raise ValueError("unsafe diagnostic member path")
    if source.is_dir():
        path = source / name
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("diagnostic member escapes source directory")
        return read_file(path, limit=limit) if path.is_file() else None
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("diagnostic archive exceeds byte limit")
    try:
        with zipfile.ZipFile(source) as archive:
            members = _validate_members(archive)
            matches = [
                item
                for item in members
                if item.filename == name or item.filename.endswith("/" + name)
            ]
            if not matches:
                return None
            if len(matches) != 1:
                raise ValueError("ambiguous diagnostic member suffix")
            member = matches[0]
            if member.file_size > limit:
                raise ValueError("diagnostic member exceeds byte limit")
            with archive.open(member) as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("diagnostic member exceeds byte limit")
            return raw.decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError("not a valid report archive") from error
