from __future__ import annotations

import sys
from pathlib import Path

PROTOCOL = "saitenka.journey/v0"
CAPABILITY = "journey-v0"
ANSWER_KEYS = frozenset({"expect"})


def scenario_request(scenario: dict) -> dict:
    if set(scenario) != {"scenario_id", "given", "when", "expect"}:
        raise ValueError("invalid journey-v0 scenario")
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
        or set(request) != {"type", "protocol", "scenario_id", "given", "when"}
    ):
        raise ValueError("invalid journey-v0 request envelope")


def evaluate(request: dict) -> list[dict]:
    validate_request(request)
    tests = Path(__file__).resolve().parents[1]
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))

    from driver import Driver
    from saitenka_card import CardData, MineConfig
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.fsrs import KnownSnap
    from saitenka_wordstate.known import KnownWords
    from session_builder import build_session
    from util import FakeIPC

    from saitenka.app import bindings
    from saitenka.app.config import ReaderOptions
    from saitenka.app.features.mining import miner
    from saitenka.app.scoring import Coloring
    from saitenka.app.session.factory import SessionServices
    from saitenka.panel import Definition, Entry

    given = request["given"]
    dictionary = _Dictionary(given["dictionary"], CardData, Definition, Entry)
    anki = _Anki()
    scorer = Coloring(
        Scorer(
            known=KnownWords.from_set(given["word_state"]["known"]),
            fsrs_snap=KnownSnap.of(given["word_state"]["fsrs"]),
            enable_freq=False,
            enable_jlpt=False,
            enable_n_plus_one=False,
        )
    )
    ipc = FakeIPC()
    ipc.props.update(given["mpv_properties"])
    reader = build_session(
        ipc,
        services=SessionServices(
            anki=anki,
            mining=MineConfig(**given["mining"]["config"]),
            dictionaries=dictionary,
            scorer=scorer,
        ),
        options=ReaderOptions().with_overrides(prefetch=False, scan_delay=0.0),
    )
    reader.graph.screen.osd = tuple(given["viewport"])
    captured: list[dict] = []
    original_capture = miner.capture_media

    def capture(_ports, _base, video, **_kwargs):
        media = given["mining"]["media"]
        captured.append({"video": video, **media})
        return media["screenshot"], media["sentence_audio"]

    miner.capture_media = capture
    observations: list[dict] = []
    driver = Driver(reader)
    try:
        for action in request["when"]:
            if action["action"] == "present-cue":
                reader.graph.cue.set_subtitle(given["cue"])
                state = reader.graph.subtitle_presentation.cue.current
                observations.append(
                    {
                        "surface": "subtitle",
                        "tokens": [
                            {
                                "text": token.surface,
                                "role": style.tag,
                                "color": list(style.color),
                            }
                            for token, style in zip(state.tokens, state.styles or (), strict=True)
                        ],
                    }
                )
            elif action["action"] == "hover-token":
                driver.move_to_word(action["index"])
                view = reader.graph.tooltip.surface_state().view
                panel = view.state
                assert panel is not None
                observations.append(
                    {
                        "surface": "tooltip",
                        "selected": reader.graph.tooltip.observation().selected,
                        "reading": panel.reading,
                        "link_targets": sorted(
                            {box.query for box in panel.windowed.link_boxes() if box.query}
                        ),
                        "definition_visible": any(
                            given["dictionary"]["visible_marker"] in box.text
                            for box in panel.windowed.scan_boxes()
                        ),
                    }
                )
            elif action["action"] == "mine-hovered":
                reader.command(bindings.MINE_MSG)
            elif action["action"] == "await-note":
                for _ in range(action["max_steps"]):
                    reader.pump(0.01)
                    if anki.added:
                        break
                else:
                    raise RuntimeError("journey did not reach the Anki boundary")
                observations.append({"boundary": "media", "captures": captured})
                observations.append({"boundary": "anki", "note": anki.added[0]})
            else:
                raise ValueError(f"unknown journey action: {action['action']!r}")
        return observations
    finally:
        miner.capture_media = original_capture
        reader.close()


class _Dictionary:
    def __init__(self, data, card_type, definition_type, entry_type) -> None:
        self.data = data
        self._card_type = card_type
        self._definition_type = definition_type
        self._entry_type = entry_type

    def entry_for(self, _token, inflected=None, *, extra_terms=()):  # noqa: ARG002
        return self._entry_type(
            headword=self.data["headword"],
            reading=self.data["reading"],
            defs=[
                self._definition_type(item["source"], item["content"])
                for item in self.data["definitions"]
            ],
        )

    def cards_for(self, _token, *, extra_terms=()):  # noqa: ARG002
        card = self.card_for(_token)
        return [card] if card.glossary_html else []

    def card_for(self, _token, *, extra_terms=()):  # noqa: ARG002
        if _token.surface != self.data["expression"]:
            return self._card_type(_token.lemma or _token.surface, _token.reading, "")
        return self._card_type(
            self.data["expression"],
            self.data["reading"],
            self.data["glossary_html"],
            glosses=tuple(self.data["glosses"]),
        )

    def has_term(self, *forms):
        return self.data["expression"] in forms

    def terms_exist(self, forms):
        return {form for form in forms if form == self.data["expression"]}

    def rareness_rank(self, _token):
        return None

    def frequency_field(self, _token):
        return "", ""

    def pitch_field(self, _token):
        return self.data["pitch_html"], self.data["pitch_positions"]


class _Anki:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def find_notes(self, _query: str) -> list[int]:
        return []

    def notes_info(self, _ids: list[int]) -> list[dict]:
        return []

    def can_add(self, _note: dict) -> bool:
        return True

    def add_note(self, note: dict) -> int:
        self.added.append(note)
        return len(self.added)

    def store_media(self, name: str, _path: object) -> str:
        return name
