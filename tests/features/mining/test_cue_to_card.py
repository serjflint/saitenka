"""A colored subtitle, dictionary lookup, and media capture reach the Anki boundary."""

import numpy as np
import pytest
from dicthelp import load_set, meta_zip, term_zip
from driver import Driver
from PIL import Image
from saitenka_card import MineConfig
from saitenka_wordstate import Scorer
from saitenka_wordstate.fsrs import KnownSnap
from saitenka_wordstate.known import KnownWords
from util import FakeIPC, assert_golden, await_ready, overlay_frame

from saitenka.app import bindings, subtitle_adapter
from saitenka.app.config import ReaderOptions
from saitenka.app.features.mining import miner
from saitenka.app.features.tooltip import tooltip
from saitenka.app.profiles import resolve_profile
from saitenka.app.scoring import Coloring
from saitenka.app.session.factory import SessionIdentity, SessionServices
from saitenka.app.subtitle_render import SUB_ID


class Anki:
    def __init__(self):
        self.added = []
        self.stored = []

    def find_notes(self, _query):
        return []

    def notes_info(self, _ids):
        return []

    def can_add(self, _note):
        return True

    def add_note(self, note):
        self.added.append(note)
        return len(self.added)

    def store_media(self, name, path):
        assert path.is_file()
        self.stored.append(name)
        return name


@pytest.mark.timeout(5)
@pytest.mark.parametrize("preprocessed", [False, True])
def test_colored_cue_lookup_and_media_are_mined_into_the_custom_card(
    make_session, tmp_path, monkeypatch, preprocessed
):
    dictionaries = load_set(
        [
            term_zip(
                tmp_path / "terms.zip",
                "Fixture terms",
                [
                    [
                        "読む",
                        "よむ",
                        [
                            {
                                "type": "structured-content",
                                "content": [
                                    "contract dictionary definition",
                                    {"tag": "a", "href": "?query=見る", "content": "見る"},
                                ],
                            }
                        ],
                    ],
                    ["見る", "みる", ["to see"]],
                ],
            )
        ],
        pitch_zips=[
            meta_zip(
                tmp_path / "pitch.zip",
                "Fixture pitch",
                "pitch",
                [
                    ["読む", {"reading": "よむ", "pitches": [{"position": 1}]}],
                ],
            )
        ],
    )
    media = []

    def capture_image(_ipc, path, **_kwargs):
        Image.new("RGB", (320, 180), (40, 100, 180)).save(path)
        return path

    def audio(video, span, path, **_kwargs):
        media.append((video, span.start, span.end))
        path.write_bytes(b"encoded-audio")
        return path

    monkeypatch.setattr(miner, "screenshot", capture_image)
    monkeypatch.setattr(miner, "clip_audio", audio)
    anki = Anki()
    ipc = FakeIPC()
    ipc.props.update(
        {
            "path": "/video/[Grp] Show - 03 [1080p].mkv",
            "time-pos": 63,
            "aid": 1,
            "track-list": [{"id": 1, "type": "audio", "ff-index": 1, "selected": True}],
            "sid": 1,
            "sub-start": 60.0,
            "sub-end": 64.0,
            "osd-dimensions": {"w": 1920, "h": 1080},
        }
    )
    session = make_session(
        ipc,
        identity=SessionIdentity(
            profile=resolve_profile(
                {
                    "profile": {
                        "preprocess_speaker_labels": preprocessed,
                        "preprocess_annotations": preprocessed,
                        "preprocess_music": preprocessed,
                        "preprocess_join_lines": preprocessed,
                    }
                }
            )
        ),
        services=SessionServices(
            dictionaries=dictionaries,
            anki=anki,
            mining=MineConfig(
                card_format={
                    "Word": "{expression}",
                    "Reading": "{reading}",
                    "Glossary": "{glossary}",
                    "Pitch": "{pitch-accents}",
                    "Sentence": "{cloze-prefix}<b>{cloze-body}</b>{cloze-suffix}",
                    "Picture": "{screenshot}",
                    "Audio": "{sentence-audio}",
                }
            ),
            scorer=Coloring(
                Scorer(
                    known=KnownWords.from_set([]),
                    fsrs_snap=KnownSnap.of({"本": "learning", "読む": "young"}),
                    enable_freq=False,
                    enable_jlpt=False,
                    enable_n_plus_one=False,
                )
            ),
            tts=False,
        ),
        options=ReaderOptions().with_overrides(prefetch=False, scan_delay=0.0, show_preview=False),
    )
    session.start()
    raw = "太郎：［足音］本を読む\n♪" if preprocessed else "本を読む"
    ui = Driver(session).resize(1920, 1080).subtitle(raw)
    image, x, _y = overlay_frame(ipc, SUB_ID)
    assert_golden(
        image, "preprocessed_cue_authored.png" if preprocessed else "cue_to_card_subtitle.png"
    )
    copied_cues = []
    monkeypatch.setattr(subtitle_adapter, "copy_clipboard", copied_cues.append)
    session.command(bindings.COPY_LINE_MSG)
    assert copied_cues == ["本を読む"]
    pixels = np.asarray(image)
    centers = [ui.word_center(index)[0] - x for index in range(3)]
    edges = [0, int((centers[0] + centers[1]) / 2), int((centers[1] + centers[2]) / 2), image.width]
    for left, right, color in zip(
        edges[:-1],
        edges[1:],
        [(238, 212, 159, 255), (202, 211, 245, 255), (139, 213, 202, 255)],
        strict=True,
    ):
        assert np.all(pixels[:, left:right] == color, axis=2).any()
    ui.move_to_word(2)
    assert_golden(ui.read_popup()[-1], "cue_to_card_lookup.png")

    copied = []
    monkeypatch.setattr(tooltip, "copy_clipboard", copied.append)
    ui.move_into_tip().press("MBTN_RIGHT")
    assert copied == ["読む【よむ】"]
    ui.press("Ctrl+m")
    await_ready(lambda: bool(anki.added), "no Anki note arrived", pump=session.pump)

    assert len(anki.added) == 1
    picture = next(name for name in anki.stored if name.endswith(".jpg"))
    sound = next(name for name in anki.stored if name.endswith(".m4a"))
    assert media == [("/video/[Grp] Show - 03 [1080p].mkv", 60.0, 64.0)]
    assert anki.added[0] == {
        "deckName": "Saitenka::Mining",
        "modelName": "Lapis",
        "fields": {
            "Word": "読む",
            "Reading": "よむ",
            "Glossary": "<ol><li>contract dictionary definition見る</li></ol>",
            "Pitch": '<ul style="text-align:left;margin:0;padding-left:1.1em;"><li>よむ: [1]</li></ul>',
            "Sentence": "本を<b>読む</b>",
            "Picture": f'<img src="{picture}">',
            "Audio": f"[sound:{sound}]",
            "IsWordAndSentenceCard": "1",
        },
        "tags": ["saitenka", "saitenka::mined", "saitenka::source::Show", "saitenka::ep::03"],
        "options": {"allowDuplicate": False},
    }
    ui.click_link("見る")
    assert_golden(ui.read_popup()[-1], "cue_to_card_link.png")


def test_bulk_mining_command_writes_one_custom_word_note(make_session):
    anki = Anki()
    ipc = FakeIPC()
    ipc.props.update(
        {
            "path": "/x/[Grp] Show - 03 [1080p].mkv",
            "time-pos": 63,
            "sub-text": "読む",
            "sid": 1,
            "sub-start": 60.0,
            "sub-end": 64.0,
        }
    )
    session = make_session(
        ipc,
        services=SessionServices(
            anki=anki,
            mining=MineConfig(card_format={"Word": "{expression}"}),
        ),
        options=ReaderOptions().with_overrides(prefetch=False, show_preview=False),
    )
    session.start()
    session.pump()

    session.command(bindings.MINE_ALL_MSG)
    await_ready(lambda: bool(anki.added), "no Anki note arrived", pump=session.pump)

    assert anki.added == [
        {
            "deckName": "Saitenka::Mining",
            "modelName": "Lapis",
            "fields": {"Word": "読む", "IsWordAndSentenceCard": "1"},
            "tags": ["saitenka", "saitenka::mined", "saitenka::source::Show", "saitenka::ep::03"],
            "options": {"allowDuplicate": False},
        }
    ]
