"""Bounded owner for the active reading environment and its switch transaction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka_tokenize.registry import Tokenizer, get_tokenizer

from saitenka import fonts
from saitenka.app.profiles import (
    DEFAULT_PROFILE,
    Profile,
    effective_slang,
    primary_font_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka_tokenize.languages import ReaderLanguages

    from saitenka.app.dictionary import DictionarySet


def _default_second_slang() -> str:
    return "en"


@dataclass(frozen=True, slots=True)
class ProfileInvalidation:
    """Cache and warm-state invalidation applied after profile preflight."""

    invalidate_tokenizer: Callable[[], None]
    invalidate_dictionary: Callable[[], None]
    reset_episode_warm: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ProfileSubtitles:
    """Subtitle facts and owner-thread mutations used by a profile switch."""

    current_subtitle_slang: Callable[[], str]
    has_subtitle_track: Callable[[str], bool]
    select_subtitle_track: Callable[[str, str], None]
    retokenize_current_cue: Callable[[], None]
    current_second_slang: Callable[[], str] = _default_second_slang


@dataclass(frozen=True, slots=True)
class ProfileAftermath:
    """Observable work performed once the active environment has committed."""

    warm_episode: Callable[[], None]
    notify: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ProfileEnvironment:
    """Optional collaborators that must follow a committed reading profile."""

    select: Callable[[Profile], None]


class ProfileSwitchStatus(StrEnum):
    REJECTED = "rejected"
    COMMITTED = "committed"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class ProfileSwitchOutcome:
    status: ProfileSwitchStatus
    profile: Profile
    index: int


class ProfileController:
    """Own the active profile facts and commit a live switch in one ordered turn."""

    def __init__(
        self,
        profile: Profile | None,
        dict_set: DictionarySet | None,
        invalidation: ProfileInvalidation,
        subtitles: ProfileSubtitles,
        aftermath: ProfileAftermath,
    ) -> None:
        self._invalidation = invalidation
        self._subtitles = subtitles
        self._aftermath = aftermath
        self._profile = profile or DEFAULT_PROFILE
        self._profiles: tuple[Profile, ...] = (self._profile,)
        self._profile_index = 0
        self._base_slang = "ja,jpn,jp"
        self._dict_scoper: Callable[[Profile], DictionarySet | None] | None = None
        self._environment: ProfileEnvironment | None = None
        self._tokenizer = get_tokenizer(self._profile.tokenizer)
        self._dict_set = dict_set
        self._apply_font_mode(self._profile)

    @property
    def profile(self) -> Profile:
        return self._profile

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return self._profiles

    @property
    def profile_index(self) -> int:
        return self._profile_index

    @property
    def langs(self) -> ReaderLanguages:
        return self._profile.langs

    @property
    def tokenizer(self) -> Tokenizer:
        return self._tokenizer

    @property
    def dict_set(self) -> DictionarySet | None:
        return self._dict_set

    def configure_cycle(
        self,
        profiles: Sequence[Profile],
        dict_scoper: Callable[[Profile], DictionarySet | None] | None = None,
        *,
        base_slang: str = "ja,jpn,jp",
        environment: ProfileEnvironment | None = None,
    ) -> None:
        self._profiles = tuple(profiles) or (self._profile,)
        self._dict_scoper = dict_scoper
        self._base_slang = base_slang
        self._environment = environment
        self._profile_index = next(
            (
                i
                for i, candidate in enumerate(self._profiles)
                if candidate.name == self._profile.name
            ),
            0,
        )

    def replace_dictionary_set(self, dict_set: DictionarySet | None) -> None:
        """Install a dependency result using the existing last-arrival-wins policy."""
        self._dict_set = dict_set

    def use_tokenizer(self, tokenizer: Tokenizer) -> None:
        self._tokenizer = tokenizer
        self._invalidation.invalidate_tokenizer()

    def switch_to(self, index: int) -> ProfileSwitchOutcome:
        target = self._profiles[index]
        try:
            tokenizer = get_tokenizer(target.tokenizer)
        except ValueError:
            self._aftermath.notify(
                f"profile {target.name!r}: unknown tokenizer {target.tokenizer!r}", "warn"
            )
            return ProfileSwitchOutcome(
                ProfileSwitchStatus.REJECTED, self._profile, self._profile_index
            )

        rescope = self._dict_scoper is not None
        dictionary_set = self._dict_set
        if rescope:
            assert self._dict_scoper is not None
            try:
                dictionary_set = self._dict_scoper(target)
            except Exception:  # noqa: BLE001 -- preserve the active environment on preflight failure
                self._aftermath.notify(
                    f"profile {target.name!r}: dictionary rescope failed", "warn"
                )
                return ProfileSwitchOutcome(
                    ProfileSwitchStatus.REJECTED, self._profile, self._profile_index
                )

        self._profile_index = index
        self._profile = target
        self._apply_font_mode(target)
        self.use_tokenizer(tokenizer)
        if rescope:
            self.replace_dictionary_set(dictionary_set)
            self._invalidation.invalidate_dictionary()
        if self._environment is not None:
            try:
                self._environment.select(target)
            except Exception:  # noqa: BLE001  # optional collaborator cannot veto reading profile
                self._aftermath.notify(
                    f"profile {target.name!r}: optional environment unavailable", "warn"
                )
        self._invalidation.reset_episode_warm()

        track = self._switch_subtitle_track(effective_slang(target, self._base_slang))
        if track is not _TrackSwitch.SWITCHED:
            self._subtitles.retokenize_current_cue()
        self._aftermath.warm_episode()
        self._aftermath.notify(f"profile: {target.name} ({target.langs.main})", "ok")
        status = (
            ProfileSwitchStatus.DEGRADED
            if track is _TrackSwitch.MISSING
            else ProfileSwitchStatus.COMMITTED
        )
        return ProfileSwitchOutcome(status, target, index)

    def _switch_subtitle_track(self, slang: str) -> _TrackSwitch:
        second_slang = self._profile.langs.second
        primary_unchanged = slang == self._subtitles.current_subtitle_slang()
        if primary_unchanged and second_slang == self._subtitles.current_second_slang():
            return _TrackSwitch.UNCHANGED
        if not primary_unchanged and not self._subtitles.has_subtitle_track(slang):
            self._aftermath.notify(
                f"profile {self._profile.name!r}: no {slang!r} subtitle track", "warn"
            )
            return _TrackSwitch.MISSING
        self._subtitles.select_subtitle_track(slang, second_slang)
        return _TrackSwitch.SWITCHED

    @staticmethod
    def _apply_font_mode(profile: Profile) -> None:
        fonts.set_primary_font(primary_font_for(profile.langs.main))


class _TrackSwitch(StrEnum):
    UNCHANGED = "unchanged"
    SWITCHED = "switched"
    MISSING = "missing"
