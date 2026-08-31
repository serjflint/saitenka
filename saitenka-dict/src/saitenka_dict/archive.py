from __future__ import annotations

import copy
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

log = logging.getLogger(__name__)

_META_BANK = re.compile(r"term_meta_bank_\d+\.json$")
_TERM_BANK = re.compile(r"term_bank_\d+\.json$")
PRIMARY_ORDER = ("dict", "pitch", "freq")
#: What a Yomitan `img` node can reference. Everything else under the archive root is documentation,
#: licences, fonts or styling — never something the renderer draws.
MEDIA_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")


class DictionaryArchiveError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_files: int = 50_000
    max_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_json_bank_bytes: int = 512 * 1024 * 1024


class DictionaryArchive:
    """Validated, non-extracting reader for one Yomitan dictionary archive."""

    def __init__(self, path: str | Path, limits: ArchiveLimits | None = None):
        self.path = Path(path)
        self.limits = limits or ArchiveLimits()
        self._zip = zipfile.ZipFile(self.path)
        try:
            self._validate_members()
            self.index_name = self._find_index()
            self.root = self.index_name.removesuffix("index.json")
            self.index = self.read_object(self.index_name)
            self._media: tuple[tuple[str, bytes], ...] | None = None
            self._validate_index()
        except Exception:
            self._zip.close()
            raise

    def close(self) -> None:
        self._zip.close()

    def _validate_index(self) -> None:
        """A titleless ``index.json`` falls back to the archive's filename rather than failing.

        Yomitan requires the field, but dictionaries in the wild omit it and were importable for
        years under that fallback; refusing them now would strand a working dictionary over a
        cosmetic field. The title only has to be unique enough to key the import.
        """
        title = self.index.get("title")
        if not isinstance(title, str) or not title.strip():
            log.debug("%s: index.json has no title, using the filename", self.path.name)
            self.index["title"] = self.path.stem

    def __enter__(self) -> DictionaryArchive:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _validate_members(self) -> None:
        members = self._zip.infolist()
        if len(members) > self.limits.max_files:
            raise DictionaryArchiveError("archive contains too many files")
        total = 0
        for info in members:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise DictionaryArchiveError(f"unsafe archive member: {info.filename!r}")
            if info.flag_bits & 1:
                raise DictionaryArchiveError("encrypted dictionary archives are not supported")
            total += info.file_size
            if total > self.limits.max_uncompressed_bytes:
                raise DictionaryArchiveError(
                    "archive uncompressed size exceeds the configured limit"
                )

    def _find_index(self) -> str:
        candidates = [
            info.filename
            for info in self._zip.infolist()
            if not info.is_dir() and PurePosixPath(info.filename).name == "index.json"
        ]
        if not candidates:
            raise DictionaryArchiveError("archive has no index.json")
        candidates.sort(key=lambda name: (name.count("/"), name))
        shallowest = candidates[0].count("/")
        if sum(name.count("/") == shallowest for name in candidates) > 1:
            raise DictionaryArchiveError("archive contains multiple dictionary roots")
        return candidates[0]

    def names(self, bank: str) -> tuple[str, ...]:
        pattern = re.compile(rf"^{re.escape(self.root + bank)}_\d+\.json$")
        return tuple(sorted(name for name in self._zip.namelist() if pattern.fullmatch(name)))

    def read_object(self, name: str) -> dict[str, Any]:
        value = self._read_json(name)
        if not isinstance(value, dict):
            raise DictionaryArchiveError(f"{name} must contain a JSON object")
        return value

    def read_bank(self, name: str) -> list[Any]:
        value = self._read_json(name)
        if not isinstance(value, list):
            raise DictionaryArchiveError(f"{name} must contain a JSON array")
        return value

    def _read_json(self, name: str) -> Any:
        info = self._zip.getinfo(name)
        if info.file_size > self.limits.max_json_bank_bytes:
            raise DictionaryArchiveError(f"JSON bank exceeds the configured limit: {name}")
        try:
            return json.loads(self._read_member(info))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DictionaryArchiveError(f"invalid JSON in {name}: {exc}") from exc

    def media(self) -> tuple[tuple[str, bytes], ...]:
        """Every member a Yomitan ``img`` node could reference, decompressed.

        Suffix-filtered before reading, not after: an archive also carries READMEs, licences, fonts
        and stylesheets under the same root, and a caller that stores no images at all should not pay
        to decompress them into memory first.
        """
        if self._media is not None:
            return self._media
        banks = {self.index_name}
        for kind in ("term_bank", "term_meta_bank", "kanji_bank", "kanji_meta_bank", "tag_bank"):
            banks.update(self.names(kind))
        result: list[tuple[str, bytes]] = []
        for info in self._zip.infolist():
            if info.is_dir() or info.filename in banks or not info.filename.startswith(self.root):
                continue
            if not info.filename.lower().endswith(MEDIA_SUFFIXES):
                continue
            result.append((info.filename.removeprefix(self.root), self._read_member(info)))
        self._media = tuple(result)
        return self._media

    def _read_member(self, info: zipfile.ZipInfo) -> bytes:
        try:
            return self._zip.read(info)
        except zipfile.BadZipFile:
            return self._zip.read(_without_crc(info))


def _without_crc(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    copied = copy.copy(info)
    cast("Any", copied).CRC = None  # zipfile uses None to skip CRC validation
    return copied


def read_json_bank(archive: zipfile.ZipFile, name: str):
    """Decode one bank, tolerating exporters that wrote a bad CRC over intact data."""
    try:
        return json.loads(archive.read(name))
    except zipfile.BadZipFile:
        try:
            return json.loads(archive.read(_without_crc(archive.getinfo(name))))
        except (zipfile.BadZipFile, ValueError):
            return None
    except ValueError:
        return None


def _meta_modes(archive: zipfile.ZipFile) -> set[str]:
    modes: set[str] = set()
    names = sorted(name for name in archive.namelist() if _META_BANK.match(name))[:2]
    for name in names:
        for entry in read_json_bank(archive, name) or []:
            if len(entry) >= 2 and isinstance(entry[1], str):
                modes.add(entry[1])
        if modes:
            break
    return modes & {"freq", "pitch"}


def _has_glossary_terms(archive: zipfile.ZipFile) -> bool:
    names = sorted(name for name in archive.namelist() if _TERM_BANK.match(name))[:2]
    return any(
        len(entry) >= 6 and entry[5]
        for name in names
        for entry in (read_json_bank(archive, name) or [])
    )


def zip_roles(path: str | Path) -> frozenset[str]:
    roles: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            roles |= _meta_modes(archive)
            if _has_glossary_terms(archive):
                roles.add("dict")
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError, ValueError, TypeError):
        return frozenset({"dict"})
    return frozenset(roles) or frozenset({"dict"})


def classify_zip(path: str | Path) -> str:
    roles = zip_roles(path)
    return next(kind for kind in PRIMARY_ORDER if kind in roles)


def title_of(archive: zipfile.ZipFile, fallback: str) -> str:
    try:
        return json.loads(archive.read("index.json")).get("title", fallback) or fallback
    except Exception:  # malformed legacy archives fall back to their filename
        log.debug("index.json title read failed", exc_info=True)
        return fallback
