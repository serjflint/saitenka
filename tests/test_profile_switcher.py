"""The live in-overlay profile switcher (#254 phase 5, D8): a keybind cycles the active reading profile
mid-session, re-resolving the reader's identity (tokenizer + languages + provider gating) atomically. The
default single-profile path is inert. The swap coordinates with the background episode-warm so a worker
mid-tokenize with the OLD tokenizer can't land a stale-language entry after the cache was cleared.

Default tier: the only boundary crossed is the in-process FakeIPC (the project's `integration` marker is
for a real subprocess/socket/filesystem — none touched here; mirrors test_tokenizer / test_episode_warm).
"""

from __future__ import annotations

import pytest
from util import FakeIPC, keybind_registry, press, runtime_gateway

from saitenka.app import prefetch
from saitenka.app.languages import MAIN_LANG, ReaderLanguages
from saitenka.app.profile_controller import ProfileSwitchStatus
from saitenka.app.profiles import DEFAULT_PROFILE, Profile
from saitenka.app.session_controller import SessionController
from saitenka.app.subtitle_providers import enabled_providers_for, register_provider
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.tokenize import Token
from saitenka.app.tokenizer import register_tokenizer
from saitenka.runtime.events import SubtitleSecondaryLeased
from saitenka.subtitles import CueIndex, parse_srt

_FR = Profile(name="fr", langs=ReaderLanguages(main="fr", second="en"), tokenizer="latin")
# A real French profile carries its own slang (resolve_profile derives "fr" from the language) — that is
# what makes a live cycle re-select the fr subtitle track. The bare _FR above (slang=None) keeps the
# ambient track, so the identity-focused tests above stay track-agnostic.
_FR_SUBS = Profile(
    name="fr", langs=ReaderLanguages(main="fr", second="en"), tokenizer="latin", slang="fr"
)
_BROKEN = Profile(name="de", langs=ReaderLanguages(main="de", second="en"), tokenizer="nonexistent")
_JA_FR_TRACKS = [{"type": "sub", "id": 1, "lang": "jpn"}, {"type": "sub", "id": 6, "lang": "fr"}]


class _MinimalTokenizer:
    """A no-morphology strategy identifiable by ``name``; one content token per line so a warmed cue is
    a non-empty (hence cacheable) entry."""

    def __init__(self, name: str) -> None:
        self.name = name

    def tokenize(self, line, *, strip_furigana=True, merge=True):  # noqa: ARG002
        surface = line or self.name
        return [Token(surface=surface, lemma=surface, reading="", pos="名詞", start=0, end=1)]

    def is_content(self, _token):
        return True

    def is_skippable(self, token):
        return not token.surface.strip()

    def query_token(self, _query):
        return None

    def inflected_in(self, tokens, index):
        return tokens[index].surface

    def phrase_terms(self, _tokens, _index, _has_term):
        return None

    def merge_dict_compounds(self, tokens, _exists):
        return tokens


@pytest.fixture
def _restore_tokenizer_registry():
    import saitenka.app.tokenizer as mod

    saved = dict(mod._FACTORIES)
    yield
    mod._FACTORIES.clear()
    mod._FACTORIES.update(saved)


def _headless(request, profile=None, profiles=None) -> SessionController:
    """A headless SessionController with a gateway behind it, both closed when the test ends.

    Takes `request` because the helper builds the resources, so the helper registers their teardown
    — nine call sites each remembering to close two things is nine chances to leak a session, and a
    leaked session is threads that outlive the test and exhaust the pool at `-n auto`.
    """
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)  # selection issues correlated commands
    request.addfinalizer(gateway.close)
    reader = SessionController(ipc, profile=profile, renderer=NullRenderer())
    request.addfinalizer(
        reader.close
    )  # LIFO: the SessionController closes before the gateway it publishes to
    if profiles is not None:
        reader.profile_controller.configure_cycle(profiles)
    reader.osd = (1280, 720)
    return reader


# --- default path: a single configured profile → the switcher is inert (characterization) ----------


def test_cycle_is_a_noop_with_a_single_profile(request):
    """The default path (no ``[profiles.*]``): the key is registered (always-register), but pressing it
    changes nothing — same tokenizer, same languages, same profile. Byte-identical to pre-#254."""
    reader = _headless(request)  # profiles defaults to (DEFAULT_PROFILE,)
    reader._register_keybinds()
    before = (
        reader.profile_controller.tokenizer.name,
        reader.profile_controller.langs.main,
        reader.profile_controller.profile,
    )

    reader.cycle_profile()

    assert (
        reader.profile_controller.tokenizer.name,
        reader.profile_controller.langs.main,
        reader.profile_controller.profile,
    ) == before


def test_profile_cycle_key_is_registered_even_on_the_default_path():
    """Always-register: the switcher binding exists regardless of how many profiles are configured, so
    the handler (not the registration) is what no-ops."""
    ipc = FakeIPC()
    reader = SessionController(ipc, renderer=NullRenderer())
    reader._register_keybinds()
    assert keybind_registry(ipc).get(reader.keys.profile_cycle_key) == "saitenka-cycle-profile"


# --- cycling: the key rotates the identity observably (tokenizer + langs + provider gating) ---------


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_pressing_the_key_cycles_the_reading_identity(monkeypatch):
    """Two configured profiles → the key flips tokenizer AND language, and wraps back. Driven through
    the REAL keybind dispatch (press → _handle → the command table → cycle_profile), so it proves the wire, not
    just the method. Provider gating follows ``reader.langs.main`` (the D5 capability gate)."""
    import saitenka.app.subtitle_providers as prov

    monkeypatch.setattr(prov, "_REGISTRY", {})
    register_provider(_stub_provider("jimaku", frozenset({"jp"})))
    register_provider(_stub_provider("universal", frozenset()))  # language-agnostic
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    flags = (("jimaku", True), ("universal", True))

    ipc = FakeIPC()
    reader = SessionController(ipc, profile=DEFAULT_PROFILE, renderer=NullRenderer())
    reader.profile_controller.configure_cycle([DEFAULT_PROFILE, _FR])
    reader.osd = (1280, 720)
    reader._register_keybinds()
    assert (
        reader.profile_controller.tokenizer.name == "unidic"
        and reader.profile_controller.langs.main == "jp"
    )
    assert enabled_providers_for(reader.profile_controller.langs.main, flags) == (
        "jimaku",
        "universal",
    )

    press(reader, ipc, reader.keys.profile_cycle_key)  # → fr

    assert (
        reader.profile_controller.tokenizer.name == "latin"
        and reader.profile_controller.langs.main == "fr"
    )
    assert reader.profile_controller.profile is _FR
    assert enabled_providers_for(reader.profile_controller.langs.main, flags) == (
        "universal",
    )  # jp-only dropped

    press(reader, ipc, reader.keys.profile_cycle_key)  # wraps → back to the JP default

    assert (
        reader.profile_controller.tokenizer.name == "unidic"
        and reader.profile_controller.langs.main == "jp"
    )
    assert enabled_providers_for(reader.profile_controller.langs.main, flags) == (
        "jimaku",
        "universal",
    )


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_clears_the_token_cache_so_stale_segmentation_cannot_leak(request):
    """A profile swap must not serve the previous language's cached tokenization."""
    from saitenka.app.token_cache import TokenizedCue

    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE, _FR])
    tok = Token(surface="本", lemma="本", reading="ほん", pos="名詞", start=0, end=1)
    reader.token_cache.put("本", TokenizedCue(lines=[[tok]], tokens=[tok], styles=None))
    assert len(reader.token_cache) == 1

    reader.cycle_profile()

    assert len(reader.token_cache) == 0


# --- the track re-selection that makes the cycle a FULL switch (the reported gap) ------------------


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_selects_the_new_profiles_language_track(request, monkeypatch):
    """The reported bug: cycling to French swapped the engine but left mpv on the JP track, so lookups
    missed. A profile with its own slang now re-selects THAT language's track (into the target slot, so
    it colors + scans) exactly as a ``--profile french`` launch does."""
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE, _FR_SUBS])
    reader.ipc.props["track-list"] = _JA_FR_TRACKS
    retokenized: list[bool] = []
    monkeypatch.setattr(reader, "_retokenize_current_cue", lambda: retokenized.append(True))

    reader.cycle_profile()  # → fr

    assert ("set_property", "sid", 6) in reader.ipc.commands  # the fr track is now primary
    assert reader.subtitle_slang == "fr"
    assert (
        reader.subtitle_language == MAIN_LANG
    )  # target role → colored + scanned, not the secondary
    assert retokenized == []


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_to_a_language_without_a_track_keeps_the_current_track_and_swaps_the_engine(
    request, monkeypatch
):
    """No tagged track for the new language → keep the current track (don't grab an unrelated one via the
    untagged fallback) and warn, while the reading engine still switches. select_initial is never reached,
    so no ``sid`` is set."""
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE, _FR_SUBS])
    reader.ipc.props["track-list"] = [{"type": "sub", "id": 1, "lang": "jpn"}]
    retokenized: list[bool] = []
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(reader, "_retokenize_current_cue", lambda: retokenized.append(True))
    monkeypatch.setattr(
        reader, "toast", lambda text, kind="ok", _seconds=2.8: notices.append((text, kind))
    )

    outcome = reader.profile_controller.switch_to(1)  # fr, but the file has no fr track

    assert reader.profile_controller.langs.main == "fr"  # the engine switched
    assert reader.subtitle_slang == "ja,jpn,jp"  # ...the track was left untouched
    assert not any(cmd[:2] == ("set_property", "sid") for cmd in reader.ipc.commands)
    assert retokenized == [True]
    assert outcome.status is ProfileSwitchStatus.DEGRADED
    assert any("no 'fr' subtitle track" in text and kind == "warn" for text, kind in notices)


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_same_track_switch_retokenizes_the_current_cue(request, monkeypatch):
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE, _FR])
    retokenized: list[bool] = []
    monkeypatch.setattr(reader, "_retokenize_current_cue", lambda: retokenized.append(True))

    reader.cycle_profile()

    assert retokenized == [True]


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_back_to_the_default_reselects_its_track_via_base_slang(request):
    """Wrapping back to the slang-less JP default re-selects its track using the launcher's base slang,
    proving that the fallback is not hard-coded to the default string."""
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)  # selection issues correlated commands
    request.addfinalizer(gateway.close)
    reader = SessionController(ipc, profile=DEFAULT_PROFILE, renderer=NullRenderer())
    request.addfinalizer(reader.close)
    reader.profile_controller.configure_cycle([DEFAULT_PROFILE, _FR_SUBS], base_slang="jpn")
    reader.osd = (1280, 720)
    reader.ipc.props["track-list"] = _JA_FR_TRACKS

    reader.cycle_profile()  # → fr (sid 6)
    assert reader.subtitle_slang == "fr"

    reader.cycle_profile()  # wraps → JP default; effective slang = base_slang "jpn" → the jpn track

    assert reader.subtitle_slang == "jpn"
    assert ("set_property", "sid", 1) in reader.ipc.commands


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_that_switches_tracks_clears_the_translation_secondary_mirror(request):
    """A live cycle re-runs configure(), which resets mpv's secondary-sid. The reader's mirror must be
    nulled with it, else the EN translation reveal stays stuck off — setup_secondary's ``mirror == sid``
    guard would skip re-issuing secondary-sid, so the reveal never comes back (P2 from review)."""
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE, _FR_SUBS])
    reader.ipc.props["track-list"] = _JA_FR_TRACKS
    reader.ipc.props["secondary-sid"] = 6  # the EN translation is currently revealed
    reader.declare_subtitle(SubtitleSecondaryLeased(6))

    reader.cycle_profile()  # → fr, re-selects the track (configure runs mid-session)

    assert reader._translation_secondary_sid is None  # mirror cleared → reveal can re-establish
    assert ("set_property", "secondary-sid", "no") in reader.ipc.commands


# --- atomicity: an unresolvable profile leaves the old one intact ----------------------------------


def test_cycle_reverts_atomically_when_the_new_tokenizer_is_unknown(request):
    """The new tokenizer is resolved BEFORE any state is swapped, so a profile naming an unregistered
    tokenizer keeps the old profile fully intact — no half-applied identity."""
    reader = _headless(request, profile=DEFAULT_PROFILE, profiles=[DEFAULT_PROFILE, _BROKEN])

    reader.cycle_profile()  # _BROKEN.tokenizer 'nonexistent' is not registered

    assert reader.profile_controller.profile is DEFAULT_PROFILE  # unchanged
    assert (
        reader.profile_controller.tokenizer.name == "unidic"
        and reader.profile_controller.langs.main == "jp"
    )
    assert (
        reader.profile_controller.profile_index == 0
    )  # cursor did not advance past the failed switch


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_cycle_reverts_atomically_when_dictionary_rescope_fails(request):
    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE)
    original_dicts = object()
    reader.profile_controller.replace_dictionary_set(original_dicts)

    def fail_scope(_profile):
        raise RuntimeError("broken dictionary")

    reader.profile_controller.configure_cycle([DEFAULT_PROFILE, _FR], fail_scope)

    reader.cycle_profile()

    assert reader.profile_controller.profile is DEFAULT_PROFILE
    assert reader.profile_controller.tokenizer.name == "unidic"
    assert reader.profile_controller.dict_set is original_dicts
    assert reader.profile_controller.profile_index == 0


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_late_dependency_result_cannot_overwrite_selected_profile(request):
    from saitenka.app.mining_controller import MiningIdentity
    from saitenka.app.reader_deps import DependencyBundle

    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE)
    active_dicts = object()
    late_launch_dicts = object()
    reader.profile_controller.configure_cycle([DEFAULT_PROFILE, _FR], lambda _p: active_dicts)
    reader.cycle_profile()
    reader.profile_dependencies.select(_FR)

    reader._apply_deps(
        DependencyBundle(MiningIdentity(DEFAULT_PROFILE.name, 0), dictionaries=late_launch_dicts)
    )

    assert reader.profile_controller.profile is _FR
    assert reader.profile_controller.dict_set is active_dicts


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_profile_environment_refuses_out_of_order_dependency_publication(request, monkeypatch):
    from saitenka.app import miner
    from saitenka.app.anki import MineConfig
    from saitenka.app.bindings import MINE_MSG
    from saitenka.app.mining_controller import MiningIdentity, MiningSpec, MiningTarget
    from saitenka.app.reader_deps import DependencyBundle

    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    reader = _headless(request, profile=DEFAULT_PROFILE)
    submissions: list[tuple[object, object]] = []

    def load(_port, cfg, _build, *, identity, **_kwargs):
        submissions.append((identity, cfg))

    monkeypatch.setattr("saitenka.app.reader_deps.load_deps_async", load)

    def spec_for(profile, identity):
        return MiningSpec(
            identity,
            {"deck": f"Deck::{profile.name}", "model": "Lapis"},
        )

    reader.configure_profiles(
        [DEFAULT_PROFILE, _FR],
        dependency_builder_for=lambda profile, _identity: (
            {"profile": profile.name},
            lambda: (),
        ),
        mining_spec_for=spec_for,
    )

    reader.cycle_profile()

    selected = reader.mining_controller.desired_spec.identity
    assert selected.profile == _FR.name
    assert reader.mining_controller.active_target is None
    assert submissions == [(selected, {"profile": _FR.name})]

    stale = MiningTarget(
        MiningIdentity(DEFAULT_PROFILE.name, 0),
        object(),
        MineConfig(deck=f"Deck::{DEFAULT_PROFILE.name}"),
    )
    reader._apply_deps(DependencyBundle(stale.identity, mining=stale))

    assert submissions == [(selected, {"profile": _FR.name})]
    assert reader.mining_controller.active_target is None

    class Anki:
        def __init__(self) -> None:
            self.added: list[dict] = []

        def find_notes(self, _query):
            return []

        def can_add(self, _note):
            return True

        def add_note(self, note):
            self.added.append(note)
            return 41

        def store_media(self, name, _path):
            return name

    anki = Anki()
    target = MiningTarget(selected, anki, MineConfig(deck=f"Deck::{_FR.name}"))
    reader._apply_deps(DependencyBundle(selected, mining=target))

    assert reader.mining_controller.active_target is target
    reader._apply_deps(DependencyBundle(stale.identity, mining=stale))
    assert submissions == [(selected, {"profile": _FR.name})]
    assert reader.mining_controller.active_target is target
    monkeypatch.setattr(miner, "capture_media", lambda *_args, **_kwargs: ("", ""))
    reader.set_subtitle("chat")

    reader._handle(MINE_MSG)

    assert anki.added[0]["deckName"] == f"Deck::{_FR.name}"


@pytest.mark.usefixtures("_restore_tokenizer_registry")
def test_invalid_profile_mining_spec_disables_the_old_target(request):
    from saitenka.app.anki import MineConfig
    from saitenka.app.mining_controller import MiningSpec

    register_tokenizer("latin", lambda: _MinimalTokenizer("latin"))
    config = MineConfig(deck="Deck::default")
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    reader = SessionController(
        ipc,
        profile=DEFAULT_PROFILE,
        renderer=NullRenderer(),
        anki=object(),
        mine_cfg=config,
    )
    request.addfinalizer(reader.close)

    def spec_for(profile, identity):
        if profile is _FR:
            raise ValueError("invalid profile mining config")
        return MiningSpec(identity, {"deck": config.deck, "model": config.model})

    reader.configure_profiles(
        [DEFAULT_PROFILE, _FR],
        dependency_builder_for=lambda _profile, _identity: ({}, lambda: ()),
        mining_spec_for=spec_for,
    )
    assert reader.mining_controller.active_target is not None

    reader.cycle_profile()

    assert reader.profile_controller.profile is _FR
    assert reader.mining_controller.desired_spec.enabled is False
    assert reader.mining_controller.active_target is None
    assert reader.mining_controller.index_snapshot().values == set()


def test_matching_failed_dependency_bundle_clears_the_active_mining_target(request):
    from saitenka.app.anki import MineConfig
    from saitenka.app.mining_controller import SeedStatus
    from saitenka.app.reader_deps import DependencyBundle

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    reader = SessionController(
        ipc,
        renderer=NullRenderer(),
        anki=object(),
        mine_cfg=MineConfig(deck="Deck::default"),
    )
    request.addfinalizer(reader.close)
    identity = reader.mining_controller.desired_spec.identity
    reader.mining_controller.record_mined_expression("old")

    reader._apply_deps(DependencyBundle(identity, failed=True))

    snapshot = reader.mining_controller.index_snapshot()
    assert reader.mining_controller.active_target is None
    assert snapshot.values == set()
    assert snapshot.seed_status is SeedStatus.DEGRADED


# --- the cache-clear vs episode-warm race (the carried P2) -----------------------------------------

_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\nAAA\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nBBB\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\nCCC\n"
)


class _ExistsDS:
    def terms_exist(self, _forms):
        return set()


class _SwapMidWarmTokenizer(_MinimalTokenizer):
    """Reproduces the race deterministically: while tokenizing the FIRST warmed cue it performs the
    profile swap (``use_tokenizer``), so the warm's ``put`` for that cue lands AFTER the cache was
    cleared+bumped — exactly the window a background worker hits."""

    def __init__(self, reader: SessionController, replacement) -> None:
        super().__init__("old")
        self._reader, self._replacement, self._swapped = reader, replacement, False

    def tokenize(self, line, *, strip_furigana=True, merge=True):
        toks = super().tokenize(line, strip_furigana=strip_furigana, merge=merge)
        if not self._swapped:
            self._swapped = True
            self._reader.profile_controller.use_tokenizer(
                self._replacement
            )  # the swap lands mid-warm
        return toks


def _warm_reader(request) -> SessionController:
    reader = _headless(request)
    reader.profile_controller.replace_dictionary_set(_ExistsDS())
    reader.episode.sub_index = CueIndex(parse_srt(_SRT))
    return reader


def test_swap_during_warm_drops_the_stale_language_entry(request):
    """The P2 guard: a worker mid-``_tokenize_cue`` with the OLD tokenizer, when the swap clears+bumps
    the cache before its ``put``, must NOT leave a stale entry behind — the generation gate drops it.
    Without the gate this cue's put would survive (len == 1), so this asserts the guard has teeth."""
    reader = _warm_reader(request)
    new = _MinimalTokenizer("new")
    reader.profile_controller.use_tokenizer(_SwapMidWarmTokenizer(reader, new))  # active = OLD

    prefetch._warm_episode_loop(
        reader.episode.sub_index, ports=reader.warm_ports.loop
    )  # swaps to NEW mid-loop

    assert reader.profile_controller.tokenizer is new  # the swap took effect
    assert len(reader.token_cache) == 0  # the OLD-tokenizer cue never landed


def test_warm_under_the_new_generation_stores_cleanly_after_a_swap(request):
    """Positive control: once the swap has settled, warming under the current generation caches every
    cue normally — the generation gate drops only the stale in-flight put, not all future work."""
    reader = _warm_reader(request)
    reader.profile_controller.use_tokenizer(_MinimalTokenizer("new"))  # settled generation
    reader._warmed_index = None

    prefetch._warm_episode_loop(reader.episode.sub_index, ports=reader.warm_ports.loop)

    assert len(reader.token_cache) == 3  # all three cues warmed under the new generation


def _stub_provider(name, languages):
    from saitenka.app.subtitle_providers import ProviderContext, SubtitleProvider

    def candidates(_video, _ctx: ProviderContext):
        return [], []

    def fetch_attempt(_video, _ctx: ProviderContext):
        return lambda: (None, f"{name}: stub")

    return SubtitleProvider(
        name=name, languages=languages, candidates=candidates, fetch_attempt=fetch_attempt
    )
