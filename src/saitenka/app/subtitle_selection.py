"""Pure subtitle source/track/role selection policy.

Every decision here is a function of values the caller already read: the track list, the current
selection, and the active role. Nothing in this module touches mpv, the filesystem, or `SessionController` —
the adapter in `subtitle_modes` reads the facts, asks for a decision, and executes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saitenka_tokenize.languages import MAIN_LANG, SECOND_LANG, Language, looks_japanese

EN_LANGS = {"en", "eng", "en-us", "en-gb", "eng-us", "english"}
JP_LANGS = {"ja", "jpn", "jp", "japanese"}
_ISO_639_ALIASES = {
    "be": ("bel",),
    "bg": ("bul",),
    "ca": ("cat",),
    "da": ("dan",),
    "de": ("deu", "ger"),
    "el": ("ell", "gre"),
    "en": ("eng",),
    "es": ("spa",),
    "fi": ("fin",),
    "fr": ("fra", "fre"),
    "it": ("ita",),
    "jp": ("jpn", "ja", "japanese"),
    "mk": ("mkd", "mac"),
    "nb": ("nob",),
    "nl": ("nld", "dut"),
    "nn": ("nno",),
    "no": ("nor",),
    "pl": ("pol",),
    "pt": ("por",),
    "ro": ("ron", "rum"),
    "ru": ("rus",),
    "sr": ("srp",),
    "sv": ("swe",),
    "uk": ("ukr",),
}


def _tag_matches_preference(lang: str, want: str) -> bool:
    low = lang.lower()
    return low == want or low.startswith(f"{want}-")


def _equivalent_bases(base: str) -> tuple[str, ...]:
    aliases = _ISO_639_ALIASES.get(base)
    if aliases is not None:
        return (base, *aliases)
    for canonical, candidates in _ISO_639_ALIASES.items():
        if base in candidates:
            return (base, canonical, *(candidate for candidate in candidates if candidate != base))
    return (base,)


def wanted_languages(slang: str) -> list[str]:
    wanted: list[str] = []
    for part in slang.split(","):
        code = part.strip().lower()
        if not code:
            continue
        wanted.append(code)
        base = code.partition("-")[0]
        region = code.removeprefix(f"{base}-") if base != code else ""
        equivalent = _equivalent_bases(base)
        if region:
            wanted.extend(f"{alias}-{region}" for alias in equivalent[1:])
        if base != code:
            wanted.append(base)
        wanted.extend(equivalent[1:])
    return list(dict.fromkeys(wanted))


def lang_matches(lang: str | None, wants: list[str]) -> bool:
    low = (lang or "").lower()
    if not low:
        return any(wants)
    expanded = [candidate for want in wants for candidate in wanted_languages(want)]
    return any(_tag_matches_preference(low, want) for want in expanded)


@dataclass(frozen=True)
class SubtitleTracks:
    jp_sid: int | None
    en_sid: int | None


@dataclass(frozen=True)
class SubtitleStartup:
    tracks: SubtitleTracks
    active: Language | None


def matching_track(tracks: list[dict], wants: list[str]) -> dict | None:
    tagged = matching_tagged_track(tracks, wants)
    if tagged is not None:
        return tagged
    return next((track for track in tracks if lang_matches(track.get("lang"), wants)), None)


def matching_tagged_track(tracks: list[dict], wants: list[str]) -> dict | None:
    return next(
        (
            track
            for want in wants
            for track in tracks
            if track.get("lang") and _tag_matches_preference(str(track["lang"]), want)
        ),
        None,
    )


def _fallback_primary(tracks: list[dict], en: dict | None) -> dict | None:
    selected = sorted(
        (track for track in tracks if track.get("selected")),
        key=lambda track: track.get("main-selection", 0),
    )
    if selected and selected[0] is not en:
        return selected[0]
    return tracks[0] if en is None and tracks else None


def _fill_untagged_tracks(
    tracks: list[dict],
    jp: dict | None,
    en: dict | None,
    *,
    primary_matched: bool,
    allow_tagged_fallback: bool,
) -> tuple[dict | None, dict | None]:
    untagged = [track for track in tracks if not str(track.get("lang") or "").strip()]
    fallback = tracks if allow_tagged_fallback else untagged
    if jp is None:
        jp = _fallback_primary(fallback, en)
    if en is None:
        candidates = fallback if allow_tagged_fallback and not primary_matched else untagged
        en = next((track for track in candidates if track.get("id") != (jp or {}).get("id")), None)
    return jp, en


def discover(tracks: list[dict], slang: str, second_slang: str = "en") -> SubtitleTracks:
    """Classify the track list into the target-language and secondary sids."""
    primary_wants = wanted_languages(slang)
    second_wants = wanted_languages(second_slang)
    jp = matching_track(tracks, primary_wants)
    en = matching_track(tracks, second_wants)
    if jp is not None and en is not None and jp.get("id") == en.get("id"):
        en = None
    jp, en = _fill_untagged_tracks(
        tracks,
        jp,
        en,
        primary_matched=jp is not None,
        allow_tagged_fallback=bool(JP_LANGS.intersection(primary_wants))
        and bool(EN_LANGS.intersection(second_wants)),
    )
    return SubtitleTracks(
        jp_sid=jp.get("id") if jp is not None else None,
        en_sid=en.get("id") if en is not None else None,
    )


def initial(
    tracks: list[dict],
    slang: str,
    second_slang: str = "en",
    *,
    preferred_role: Language | None = None,
) -> SubtitleStartup:
    """Keep a requested live role when available, else prefer target then translation."""
    discovered = discover(tracks, slang, second_slang)
    if preferred_role == SECOND_LANG and discovered.en_sid is not None:
        return SubtitleStartup(discovered, SECOND_LANG)
    if discovered.jp_sid is not None:
        return SubtitleStartup(discovered, MAIN_LANG)
    if discovered.en_sid is not None:
        return SubtitleStartup(discovered, SECOND_LANG)
    return SubtitleStartup(discovered, None)


@dataclass(frozen=True, slots=True)
class ToggleDecision:
    """Which role the toggle switches to, and the sid that realises it."""

    target: Language
    sid: int | None

    @property
    def available(self) -> bool:
        return self.sid is not None


def toggle_target(
    tracks: SubtitleTracks, *, active_sid: object, language: Language
) -> ToggleDecision:
    if active_sid == tracks.jp_sid:
        target: Language = SECOND_LANG
    elif active_sid == tracks.en_sid or (language == MAIN_LANG and tracks.jp_sid is not None):
        target = MAIN_LANG
    elif language == SECOND_LANG and tracks.en_sid is not None:
        target = SECOND_LANG
    else:
        target = MAIN_LANG if tracks.jp_sid is not None else SECOND_LANG
    return ToggleDecision(target, tracks.en_sid if target == SECOND_LANG else tracks.jp_sid)


def primary_role(
    sid: object,
    tracks: SubtitleTracks,
    *,
    track_lang: str | None,
    sample: str,
    primary_slang: str = "ja,jpn,jp",
    second_slang: str = "en",
) -> Language:
    """Role of the track mpv just made primary. A real Japanese tag wins, then a real English tag.
    An UNTAGGED track is classified by CONTENT — Japanese script in its cues, else the on-screen
    text — exactly where a tag cannot decide (`lang_matches(None, EN_LANGS)` is a false wildcard)."""
    if sid == tracks.jp_sid:
        return MAIN_LANG
    if sid == tracks.en_sid:
        return SECOND_LANG
    if track_lang and lang_matches(track_lang, wanted_languages(primary_slang)):
        return MAIN_LANG
    if track_lang and lang_matches(track_lang, wanted_languages(second_slang)):
        return SECOND_LANG
    return MAIN_LANG if (not sample or looks_japanese(sample)) else SECOND_LANG


def language_name(lang: str | None) -> str:
    low = (lang or "").lower()
    if low in JP_LANGS:
        return "Japanese"
    if low in EN_LANGS:
        return "English"
    return lang or "unknown language"


class FetchAction(StrEnum):
    """What an arrived subtitle fetch result does to the on-screen selection."""

    REPORT_FAILURE = "report-failure"
    REPLACE = "replace"
    BACKGROUND_ADD = "background-add"


def fetch_action(
    *, path_available: bool, force_select: bool, replace: bool, language: Language
) -> FetchAction:
    """An explicit picker choice always selects; a user retry only swaps while already on the
    target language, so a retry from English keeps English until the user asks for the switch."""
    if not path_available:
        return FetchAction.REPORT_FAILURE
    if force_select:
        return FetchAction.REPLACE
    if replace and language == MAIN_LANG:
        return FetchAction.REPLACE
    return FetchAction.BACKGROUND_ADD


def selects_background_japanese(
    *,
    select_if_unchanged: bool,
    had_japanese: bool,
    current_sid: object,
    initial_sid: object,
    jp_sid: object,
) -> bool:
    """A background arrival auto-selects only when the user never had, nor touched, a target track."""
    return (
        select_if_unchanged
        and not had_japanese
        and current_sid == initial_sid
        and jp_sid is not None
    )
