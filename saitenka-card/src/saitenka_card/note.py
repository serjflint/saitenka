"""The shape of a mined card: what the note type's fields are called, and what goes in them.

Split from the application's AnkiConnect module, which was two things at once — how to reach Anki,
and what to send it. Only the second is about cards, and only the second is reusable: a note built
here is a plain dict, so nothing in this file knows a socket exists.

The logical→real field map keeps it note-type-agnostic (Lapis by default); only mapped fields are
written, which is why an unmapped key is the silent-empty-note trap ``doctor`` validates against.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka_card.clip import AnimatedClip
from saitenka_card.markers import (
    MarkerContext,
    build_markers,
    markers_in,
    render_card_format,
)

if TYPE_CHECKING:
    from pathlib import Path

    from saitenka_card.card import CardData

log = logging.getLogger(__name__)


def strip_field_html(value: str) -> str:
    """Return the plain text used to compare or display an Anki field value."""
    return re.sub(r"<[^>]+>", "", value).strip()


# logical name -> real field on the note type (Lapis defaults). Kiku shares these names — SubMiner
# treats the two uniformly and its docs describe Kiku as inheriting Lapis's field settings.
LAPIS_FIELDS = {
    "expression": "Expression",
    "reading": "ExpressionReading",
    "sentence": "Sentence",
    "glossary": "Glossary",
    "picture": "Picture",
    "audio": "SentenceAudio",
    "misc": "MiscInfo",
    "id": "ID",
    "freq": "Frequency",
    "freq_sort": "FreqSort",
}

# The mutually-exclusive card-template markers a note type may key off (Lapis/Kiku family). One of
# these, set non-empty, selects the front/back template. card_kind -> its marker (None = mark none).
KNOWN_MARKERS = ("IsSentenceCard", "IsWordAndSentenceCard", "IsClickCard", "IsAudioCard")
_CARD_KIND_MARKER: dict[str, str | None] = {
    "sentence": "IsSentenceCard",
    "word-and-sentence": "IsWordAndSentenceCard",
    "click": "IsClickCard",
    "audio": "IsAudioCard",
    "none": None,
}
_DEFAULT_CARD_KIND = "word-and-sentence"
# Card-kind choices in offer order (the SSOT for every picker: setup wizard + `saitenka config`).
CARD_KINDS = ("word-and-sentence", "sentence", "audio", "click", "none")

# French mining preset (#254 W6): the Lapis field names minus the JP-only `reading` field. French has
# no kana reading / pitch / furigana, and those markers ground to empty for a Latin-tokenized mine — so
# a French note type simply doesn't carry a reading field. A user points [profiles.french.mine] at their
# French note type; explicit `fields` overrides win over these defaults.
FRENCH_FIELDS = {k: v for k, v in LAPIS_FIELDS.items() if k != "reading"}

# Known-good note types: (field map, default card kind). A preset spares the user spelling the map
# out; both Lapis and Kiku use the shared LAPIS_FIELDS names, differing only in card template.
PRESETS: dict[str, tuple[dict, str]] = {
    "Lapis": (LAPIS_FIELDS, _DEFAULT_CARD_KIND),
    "Kiku": (LAPIS_FIELDS, _DEFAULT_CARD_KIND),
    "French": (FRENCH_FIELDS, _DEFAULT_CARD_KIND),
}


def _flags_for(card_kind: str) -> dict:
    """The non-empty card-template marker(s) for a card kind: exactly one of :data:`KNOWN_MARKERS`
    set to ``"1"`` (mutual exclusion by construction), or ``{}`` for ``"none"``. An unrecognised kind
    warns and falls back to the default, so a ``[mine].card_kind`` typo can't silently disable mining."""
    if card_kind not in _CARD_KIND_MARKER:
        log.warning("unknown [mine].card_kind %r; using %r", card_kind, _DEFAULT_CARD_KIND)
        card_kind = _DEFAULT_CARD_KIND
    marker = _CARD_KIND_MARKER[card_kind]
    return {marker: "1"} if marker else {}


@dataclass
class MineConfig:
    deck: str = "Saitenka::Mining"
    model: str = "Lapis"
    tags: tuple[str, ...] = ("saitenka",)
    normalize_audio: bool = False  # opt-in −23 LUFS loudnorm on the mined clip
    # Opt-in animated (motion) screenshot instead of a still (config: [mine].animated_screenshot +
    # animated_height/fps/quality/max_secs/format). See media.AnimatedClip / animated_screenshot.
    animated: AnimatedClip = field(default_factory=AnimatedClip)
    # card template selector — one of _CARD_KIND_MARKER's keys. Default word-and-sentence (SubMiner's
    # default) is a deliberate change from the historical unconditional IsSentenceCard; set
    # [mine].card_kind = "sentence" to restore the old marker.
    card_kind: str = _DEFAULT_CARD_KIND
    fields: dict = field(default_factory=lambda: dict(LAPIS_FIELDS))
    # Yomitan-style field -> "{marker}" template map. When set it WINS wholesale over `fields` (only
    # these fields are written), letting one field combine markers / one entity fan out. See
    # card_markers.render_card_format / MARKERS.
    card_format: dict = field(default_factory=dict)
    # non-empty flag fields that pick a card template; derived from card_kind unless set explicitly
    flags: dict = field(default_factory=dict)
    # Opt-in word-pronunciation audio from a local yomichan/yomitan audio pack (#93, offline/grounded) —
    # ADDITIVE to the mined sentence/scene clip above, never a replacement. `word_audio_pack` is the pack
    # dir (None = feature off, resolved by mining_config.mine_config_from from [mine].word_audio_*);
    # `word_audio_field` is the note field it's written to. See word_audio.resolve.
    word_audio_pack: Path | None = None
    word_audio_field: str = "WordAudio"

    def __post_init__(self) -> None:
        if not self.flags:
            self.flags = _flags_for(self.card_kind)

    def expression_field(self) -> str:
        """The real note field holding the mined expression — the dedup key. Under ``card_format`` it's
        the field whose template references ``{expression}`` (that's what actually gets written); else
        the entity map's ``expression`` target. ``""`` when ``card_format`` never surfaces the expression
        — no reliable dedup key, so the caller allows the add rather than querying an empty field."""
        if self.card_format:
            return next(
                (
                    real
                    for real, tmpl in self.card_format.items()
                    if "expression" in markers_in(str(tmpl))
                ),
                "",
            )
        return self.fields.get("expression", "Expression")

    @classmethod
    def from_preset(cls, name: str, **overrides) -> MineConfig:
        """A :class:`MineConfig` for a known note type (Lapis/Kiku): its field map + default card
        kind. An unknown name warns and falls back to the Lapis map. ``overrides`` win over the preset."""
        if name not in PRESETS:
            log.warning("unknown mining preset %r; using the Lapis field map", name)
        fields_map, card_kind = PRESETS.get(name, (LAPIS_FIELDS, _DEFAULT_CARD_KIND))
        params: dict = {"model": name, "fields": dict(fields_map), "card_kind": card_kind}
        params.update(overrides)
        return cls(**params)


def bold_word(sentence: str, surface: str) -> str:
    """Wrap the first occurrence of the mined surface in <b> for the Sentence field.

    The surrounding context is HTML-escaped so that subtitle text containing <, >, or &
    does not inject raw HTML into the Anki card's Sentence field."""
    esc = html.escape(sentence)
    esc_surface = html.escape(surface)
    i = esc.find(esc_surface)
    if i < 0:
        return esc
    return f"{esc[:i]}<b>{esc_surface}</b>{esc[i + len(esc_surface) :]}"


@dataclass(frozen=True)
class CardContent:
    """The rendered per-card content a mine produces — the sentence/media/frequency pieces build_note
    assembles into fields (each Anki-wrapped at use). Bundled so a mine's content flows as one value."""

    sentence_html: str = ""
    picture: str = ""
    audio: str = ""
    misc: str = ""
    freq_html: str = ""
    freq_sort: str = ""


_EMPTY_CONTENT = CardContent()  # frozen → safe shared default for build_note


# The logical entities a plain ``[mine.fields]`` map may reference (its LHS keys). A key outside this
# set writes nothing — the silent-empty-note trap — so doctor validates against it. Kept in lockstep
# with :func:`_entity_values` by a test.
KNOWN_ENTITIES = frozenset(
    {
        "expression",
        "reading",
        "sentence",
        "glossary",
        "picture",
        "audio",
        "misc",
        "id",
        "freq",
        "freq_sort",
    }
)


def _entity_values(card, content: CardContent) -> dict:
    """logical entity -> content, for the default ``[mine.fields]`` map (media wrapped Anki-ready)."""
    return {
        "expression": card.expression,
        "reading": card.reading,
        "sentence": content.sentence_html,
        "glossary": card.glossary_html,
        "picture": f'<img src="{content.picture}">' if content.picture else "",
        "audio": f"[sound:{content.audio}]" if content.audio else "",
        "misc": content.misc,
        "id": card.idseq,
        "freq": content.freq_html,
        "freq_sort": content.freq_sort,
    }


def _card_format_fields(cfg, card, content: CardContent, tags, markers) -> dict:
    """Render ``cfg.card_format`` (field -> ``{marker}`` template). ``markers`` from the miner when it
    has one; otherwise a partial map from ``content`` (pitch/pos/title empty)."""
    if markers is None:
        markers = build_markers(
            MarkerContext(
                card=card,
                sentence_html=content.sentence_html,
                picture=content.picture,
                audio=content.audio,
                misc=content.misc,
                doc_title="",
                freq_html=content.freq_html,
                freq_rank=content.freq_sort,
                pos_en="",
                tags=tags,
            )
        )
    return render_card_format(cfg.card_format, markers)


def build_note(
    cfg: MineConfig,
    card: CardData,
    content: CardContent = _EMPTY_CONTENT,
    tags=(),
    *,
    allow_duplicate: bool = False,
    markers: dict | None = None,
) -> dict:
    """Assemble the AnkiConnect note dict from card data + rendered ``content``. ``tags`` are extra
    per-card tags (source/episode) added to the config's static tags. ``allow_duplicate`` lets an
    explicit "add anyway" mine a second card for an expression already in the deck (a different scene).

    ``markers`` is the full ``{marker} -> value`` map for the ``[mine.card_format]`` path — the miner
    builds it (it has the token/dict/video the pitch/pos/title markers need). Omitted, that path falls
    back to a partial map from ``content`` (pitch/pos/title empty), so ``build_note`` stays usable alone."""
    if cfg.card_format:
        note_fields = _card_format_fields(cfg, card, content, tags, markers)
    else:
        values = _entity_values(card, content)
        note_fields = {real: values.get(logical, "") for logical, real in cfg.fields.items()}
    note_fields.update(cfg.flags)
    all_tags = list(dict.fromkeys(list(cfg.tags) + list(tags)))  # dedupe, keep order
    return {
        "deckName": cfg.deck,
        "modelName": cfg.model,
        "fields": note_fields,
        "tags": all_tags,
        "options": {"allowDuplicate": allow_duplicate},
    }
