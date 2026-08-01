"""Keyless TsukiHime Japanese subtitle provider."""

from __future__ import annotations

import json
import lzma
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from overlay.app.jimaku import _ssl_context, parse_filename

API_BASE = "https://api.tsukihime.org/v1"
STORAGE_BASE = "https://storage.tsukihime.org"
ALLOWED_DOWNLOAD_DOMAINS = ("tsukihime.org", "animetosho.org")
DEFAULT_TIMEOUT = 15.0
DEFAULT_RESULT_CAP = 10
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_SUBTITLE_BYTES = 64 * 1024 * 1024
IMPORTED_ID_FLOOR = 1_000_000_000
XZ_MAGIC = b"\xfd7zXZ\x00"

_TEXT_CODECS = {"ass": ".ass", "ssa": ".ssa", "srt": ".srt", "subrip": ".srt"}
_JAPANESE_LANGS = {"ja", "jp", "jpn", "japanese"}


class TsukiHimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class TsukiHimeRelease:
    id: int
    name: str
    imported: bool


@dataclass(frozen=True)
class TsukiHimeAttachment:
    id: int
    extension: str
    url: str
    source_name: str


def _host_allowed(host: str | None, domains: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _validated_https(url: str, domains: tuple[str, ...]) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not _host_allowed(parsed.hostname, domains)
    ):
        raise TsukiHimeError(f"untrusted TsukiHime URL: {url}")
    return parsed


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, domains: tuple[str, ...]):
        self.domains = domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validated_https(newurl, self.domains)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(domains: tuple[str, ...]):
    return urllib.request.build_opener(
        _SafeRedirect(domains), urllib.request.HTTPSHandler(context=_ssl_context())
    )


def _read_limited(response, limit: int) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while chunk := response.read(min(64 * 1024, limit + 1 - received)):
        chunks.append(chunk)
        received += len(chunk)
        if received > limit:
            raise TsukiHimeError(f"TsukiHime response exceeds {limit} bytes")
    return b"".join(chunks)


def _decompress_limited(data: bytes, limit: int = MAX_SUBTITLE_BYTES) -> bytes:
    if not data.startswith(XZ_MAGIC):
        if len(data) > limit:
            raise TsukiHimeError(f"TsukiHime subtitle exceeds {limit} bytes")
        return data
    decompressor = lzma.LZMADecompressor()
    try:
        output = decompressor.decompress(data, max_length=limit + 1)
        while not decompressor.needs_input and not decompressor.eof and len(output) <= limit:
            output += decompressor.decompress(b"", max_length=limit + 1 - len(output))
    except lzma.LZMAError as exc:
        raise TsukiHimeError(f"invalid XZ subtitle: {exc}") from exc
    if len(output) > limit:
        raise TsukiHimeError(f"decompressed TsukiHime subtitle exceeds {limit} bytes")
    if not decompressor.eof:
        raise TsukiHimeError("truncated XZ subtitle")
    if decompressor.unused_data:
        raise TsukiHimeError("XZ subtitle contains trailing data")
    return output


def _normalized_title(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _is_japanese(lang: object) -> bool:
    normalized = str(lang or "").casefold().replace("_", "-").split("-", 1)[0]
    return normalized in _JAPANESE_LANGS


def _attachment_url(attachment_id: int, *, imported: bool) -> str:
    hex_id = f"{attachment_id:08x}"
    prefix = "/tosho/attach" if imported else "/attach"
    return f"{STORAGE_BASE}{prefix}/{hex_id}/{attachment_id}.xz"


def _release_match(raw: object, wanted_title: str, episode: int | None) -> TsukiHimeRelease | None:
    if not isinstance(raw, dict):
        return None
    release_id, name = raw.get("id"), raw.get("name")
    if not isinstance(release_id, int) or release_id <= 0 or not isinstance(name, str):
        return None
    release_title, release_episode = parse_filename(name)
    if _normalized_title(release_title) != wanted_title:
        return None
    if episode is not None and release_episode != episode:
        return None
    return TsukiHimeRelease(
        release_id,
        name,
        raw.get("animetosho") is True or release_id >= IMPORTED_ID_FLOOR,
    )


def _matching_releases(payload: object, title: str, episode: int | None, cap: int):
    if not isinstance(payload, dict):
        raise TsukiHimeError("malformed TsukiHime search response")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise TsukiHimeError("malformed TsukiHime search response")
    total = payload.get("total")
    truncated = len(raw_results) > cap or (isinstance(total, int) and total > len(raw_results))
    wanted_title = _normalized_title(title)
    matches = [
        release
        for raw in raw_results[:cap]
        if (release := _release_match(raw, wanted_title, episode)) is not None
    ]
    return matches, truncated


def _attachment_from_raw(
    raw: object, source_name: str, *, imported: bool
) -> TsukiHimeAttachment | None:
    if not isinstance(raw, dict) or raw.get("type") != 1:
        return None
    attachment_id = raw.get("id")
    info = raw.get("info")
    if not isinstance(attachment_id, int) or attachment_id <= 0 or not isinstance(info, dict):
        return None
    extension = _TEXT_CODECS.get(str(info.get("codec") or "").casefold())
    if extension is None or not _is_japanese(info.get("lang")):
        return None
    return TsukiHimeAttachment(
        attachment_id,
        extension,
        _attachment_url(attachment_id, imported=imported),
        source_name,
    )


def _file_attachments(raw_file: object, *, imported: bool) -> list[TsukiHimeAttachment]:
    if not isinstance(raw_file, dict):
        return []
    raw_attachments = raw_file.get("attachments")
    if not isinstance(raw_attachments, list):
        return []
    raw_source_name = raw_file.get("filename")
    source_name = raw_source_name if isinstance(raw_source_name, str) else ""
    return [
        attachment
        for raw in raw_attachments
        if (attachment := _attachment_from_raw(raw, source_name, imported=imported)) is not None
    ]


def _japanese_attachments(payload: object, *, imported: bool) -> list[TsukiHimeAttachment]:
    if not isinstance(payload, dict):
        raise TsukiHimeError("malformed TsukiHime release response")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise TsukiHimeError("malformed TsukiHime release response")
    release_id = payload.get("id")
    imported = (
        imported
        or payload.get("animetosho") is True
        or (isinstance(release_id, int) and release_id >= IMPORTED_ID_FLOOR)
    )
    attachments: list[TsukiHimeAttachment] = []
    for raw_file in raw_files:
        attachments.extend(_file_attachments(raw_file, imported=imported))
    return attachments


class TsukiHimeClient:
    def __init__(
        self,
        *,
        api_base: str = API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        result_cap: int = DEFAULT_RESULT_CAP,
        opener=None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_download_bytes: int = MAX_DOWNLOAD_BYTES,
        max_subtitle_bytes: int = MAX_SUBTITLE_BYTES,
    ):
        parsed = urllib.parse.urlparse(api_base.rstrip("/"))
        _validated_https(api_base.rstrip("/"), (parsed.hostname or "",))
        if not math.isfinite(timeout) or timeout <= 0 or not 1 <= result_cap <= 100:
            raise TsukiHimeError("TsukiHime timeout must be positive and result_cap must be 1..100")
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.result_cap = result_cap
        self.max_response_bytes = max_response_bytes
        self.max_download_bytes = max_download_bytes
        self.max_subtitle_bytes = max_subtitle_bytes
        self._api_domains = (parsed.hostname or "",)
        self._api_opener = opener or _opener(self._api_domains)
        self._download_opener = opener or _opener(ALLOWED_DOWNLOAD_DOMAINS)

    def _get(self, path: str, **params):
        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
        url = f"{self.api_base}{path}" + (f"?{query}" if query else "")
        _validated_https(url, self._api_domains)
        try:
            with self._api_opener.open(url, timeout=self.timeout) as response:
                _validated_https(response.geturl(), self._api_domains)
                return json.loads(_read_limited(response, self.max_response_bytes))
        except (
            urllib.error.URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise TsukiHimeError(f"TsukiHime request failed for {path}: {exc}") from exc

    def _download(self, attachment: TsukiHimeAttachment) -> bytes:
        _validated_https(attachment.url, ALLOWED_DOWNLOAD_DOMAINS)
        try:
            with self._download_opener.open(attachment.url, timeout=self.timeout) as response:
                _validated_https(response.geturl(), ALLOWED_DOWNLOAD_DOMAINS)
                return _read_limited(response, self.max_download_bytes)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TsukiHimeError(f"TsukiHime download failed: {exc}") from exc

    def fetch(self, title: str, episode: int | None, dest_dir: str | Path) -> Path:
        query = f"{title} {episode}" if episode is not None else title
        releases, truncated = _matching_releases(
            self._get("/search/torrents", q=query, limit=self.result_cap),
            title,
            episode,
            self.result_cap,
        )
        if truncated:
            raise TsukiHimeError(
                f"search is truncated at {self.result_cap} results; cannot prove a unique release"
            )
        if len(releases) != 1:
            candidates = ", ".join(release.name for release in releases) or "none"
            raise TsukiHimeError(
                f"expected one matching release, found {len(releases)}: {candidates}"
            )
        release = releases[0]
        attachments = _japanese_attachments(
            self._get(f"/torrents/{release.id}"), imported=release.imported
        )
        if len(attachments) != 1:
            raise TsukiHimeError(
                f"release {release.name!r} has {len(attachments)} Japanese text attachments"
            )
        attachment = attachments[0]
        subtitle = _decompress_limited(self._download(attachment), self.max_subtitle_bytes)
        destination = (
            Path(dest_dir) / f"tsukihime-{release.id}-{attachment.id}{attachment.extension}"
        )
        destination.write_bytes(subtitle)
        return destination
