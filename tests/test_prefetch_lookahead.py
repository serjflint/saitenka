"""Prefetch lookahead: WARM the next cues' words while the current line plays, and the cache-size /
RSS gauges the telemetry interval sampler reports."""

import util

from saitenka.app import prefetch
from saitenka.app.config import PerfOptions, ReaderOptions
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.tokenize import Token
from saitenka.panel import Definition, Entry
from saitenka.subtitles import CueIndex, parse_srt

_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n本を読む\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n本を書く\n\n"
    "3\n00:00:10,000 --> 00:00:12,000\n水を飲む\n"
)


class _FakeIPC(util.FakeIPC):
    def __init__(self, props=None):
        super().__init__()
        self.props.update(props or {})


class _FakeDS:
    """Records every warmed word; the entry content is irrelevant to a warm job."""

    def __init__(self):
        self.warmed = []

    def entry_for(self, tok, _inflected=None):
        self.warmed.append(tok.surface)
        return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])


def _reader(monkeypatch, *, lookahead, props=None):
    ipc = _FakeIPC(props)
    r = Reader(ipc, dict_set=_FakeDS())
    r.osd = (1280, 720)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.episode.sub_index = CueIndex(parse_srt(_SRT))
    r.prefetch_lookahead = lookahead
    return r


def _submitted_items(r):
    submitted = []
    r.prefetch_state.workers = 64
    r.prefetch_state.submitter = lambda **kwargs: submitted.append(kwargs) or True
    r._update_prefetch()
    return [entry["request"].item for entry in submitted]


def test_lookahead_warms_the_next_cues_words(monkeypatch):
    r = _reader(monkeypatch, lookahead=2)
    r.set_subtitle("本を読む")  # cue 1
    surfaces = [i.token.surface for i in _submitted_items(r)]
    # cue 2 (書く) and cue 3 (水, 飲む) get warmed; を is a particle, skipped.
    assert "書く" in surfaces and "水" in surfaces and "飲む" in surfaces


def test_lookahead_items_are_warm_only_even_while_engaged(monkeypatch):
    # Paused ⇒ the CURRENT line renders full; a future line is never engaged → always warm/unmined.
    r = _reader(monkeypatch, lookahead=1, props={"pause": True})
    r.set_subtitle("本を読む")
    items = _submitted_items(r)
    future = [i for i in items if i.token.surface == "書く"]  # only in cue 2
    assert future and all(i.full is False and i.mined is False for i in future)
    current = [i for i in items if i.token.surface == "読む"]  # only in cue 1
    assert current and all(i.full is True for i in current)


def test_lookahead_dedupes_against_the_current_line(monkeypatch):
    r = _reader(monkeypatch, lookahead=1)
    r.set_subtitle("本を読む")  # 本 is also cue 2's first word
    surfaces = [i.token.surface for i in _submitted_items(r)]
    assert surfaces.count("本") == 1  # warmed once by the current line, not again for the next


def test_no_lookahead_when_disabled(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)
    r.set_subtitle("本を読む")
    surfaces = [i.token.surface for i in _submitted_items(r)]
    assert "書く" not in surfaces and "水" not in surfaces  # only the current line queued


def test_lookahead_construction_is_bounded_before_job_admission(monkeypatch):
    r = _reader(monkeypatch, lookahead=10_000)
    calls = 0
    tokenize = r.tokenizer.tokenize

    def counted(text):
        nonlocal calls
        calls += 1
        return tokenize(text)

    monkeypatch.setattr(r.tokenizer, "tokenize", counted)
    monkeypatch.setattr(prefetch, "upcoming_cue_texts", lambda _index, n, **_kw: ["本"] * n)

    r.set_subtitle("本を読む")
    _submitted_items(r)

    assert calls <= 65  # current cue plus at most 64 future cues


def test_head_construction_bounds_scorer_work_when_no_word_is_eligible(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)
    r.set_subtitle("本を読む")
    r.head_prefetch_lookahead = 10_000
    r.prefetch_state = prefetch.PrefetchState(4)
    calls = 0

    class _Scorer:
        def score_line(self, tokens):
            nonlocal calls
            calls += 1
            return [type("Style", (), {"tag": "known"})() for _token in tokens]

    r.scorer = _Scorer()
    monkeypatch.setattr(prefetch, "upcoming_cue_texts", lambda _index, n, **_kw: ["本"] * n)

    _submitted_items(r)

    assert calls == 4


def test_head_construction_bounds_candidate_probes_within_one_long_cue(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)
    r.set_subtitle("本を読む")
    r.head_prefetch_lookahead = 1
    r.prefetch_state = prefetch.PrefetchState(4)
    tokens = [Token(f"語{i}", f"語{i}", f"ご{i}", "名詞", i, i + 1) for i in range(100)]
    probes = 0

    class _Scorer:
        def score_line(self, values):
            return [type("Style", (), {"tag": "n+1"})() for _value in values]

    def is_mined(_token):
        nonlocal probes
        probes += 1
        return False

    r.scorer = _Scorer()
    monkeypatch.setattr(r.tokenizer, "tokenize", lambda _text: tokens)
    monkeypatch.setattr(r.tokenizer, "is_content", lambda _token: True)
    monkeypatch.setattr(r, "_is_mined", is_mined)
    monkeypatch.setattr(prefetch, "upcoming_cue_texts", lambda _index, _n, **_kw: ["long"])

    heads = prefetch._head_prefetch_items(r, 1, set())

    assert len(heads) == probes == 4


def test_head_job_limit_does_not_hide_an_eligible_token_after_an_ineligible_prefix(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)
    r.set_subtitle("本を読む")
    r.head_prefetch_lookahead = 1
    r.prefetch_state = prefetch.PrefetchState(1)
    tokens = [
        Token("は", "は", "は", "助詞", 0, 1),
        Token("語", "語", "ご", "名詞", 1, 2),
    ]

    class _Scorer:
        def score_line(self, values):
            return [
                type("Style", (), {"tag": "known"})(),
                type("Style", (), {"tag": "n+1"})(),
            ][: len(values)]

    r.scorer = _Scorer()
    monkeypatch.setattr(r.tokenizer, "tokenize", lambda _text: tokens)
    monkeypatch.setattr(r.tokenizer, "is_content", lambda token: token.surface == "語")
    monkeypatch.setattr(r, "_is_mined", lambda _token: False)
    monkeypatch.setattr(prefetch, "upcoming_cue_texts", lambda _index, _n, **_kw: ["ordinary"])

    heads = prefetch._head_prefetch_items(r, 1, set())

    assert [item.token.surface for _priority, item in heads] == ["語"]


def test_upcoming_cue_texts_bounds_at_the_tail(monkeypatch):
    r = _reader(monkeypatch, lookahead=5)
    r.set_subtitle("水を飲む")  # last cue
    assert r._upcoming_cue_texts(5) == []


def test_upcoming_cue_texts_is_empty_without_an_index(monkeypatch):
    r = _reader(monkeypatch, lookahead=2)
    r.episode.sub_index = None
    r.set_subtitle("本を読む")
    assert r._upcoming_cue_texts(2) == []


def test_prefetch_lookahead_routes_through_reader_options():
    opts = ReaderOptions().with_overrides(prefetch_lookahead=3)
    assert opts.perf.prefetch_lookahead == 3
    assert PerfOptions().prefetch_lookahead == 0  # off by default


def test_telemetry_gauges_report_cache_occupancy(monkeypatch):
    r = _reader(monkeypatch, lookahead=0)

    class _Panel:
        def __init__(self, n):
            self.retained_nbytes = n

    r.tip.panel_cache.setdefault("a", _Panel(100))
    r.tip.panel_cache.setdefault("b", _Panel(250))
    monkeypatch.setattr(r.dict_set, "decoded_entry_count", lambda: 7, raising=False)
    gauges = r._telemetry_gauges()
    assert gauges["panel_cache.size"] == 2.0
    assert gauges["panel_cache.bytes"] == 350.0
    assert gauges["dict_cache.size"] == 7.0


class _Content:
    def __init__(self, skip=()):
        self.skip = set(skip)

    def is_content(self, token):
        return token.surface not in self.skip


def _tok(surface, lemma=None):
    from saitenka.app.tokenize import Token

    return Token(surface, lemma or surface, "", "名詞", 0, len(surface))


def _style(tag):
    return type("Style", (), {"tag": tag})()


def test_lookahead_reads_the_cues_after_the_one_on_screen():
    from saitenka.app.prefetch import upcoming_cue_texts
    from saitenka.subtitles import CueIndex

    index = CueIndex(parse_srt(_srt(["one", "two", "three", "four"])))

    assert upcoming_cue_texts(index, 2, text="one", preferred=-1) == ["two", "three"]


def test_lookahead_at_the_last_cue_has_nothing_to_warm():
    from saitenka.app.prefetch import upcoming_cue_texts
    from saitenka.subtitles import CueIndex

    index = CueIndex(parse_srt(_srt(["one", "two"])))

    assert upcoming_cue_texts(index, 3, text="two", preferred=-1) == []


def test_lookahead_off_the_index_warms_nothing():
    """A cue mpv is showing from a track we never indexed. Warming from index position 0 would
    decode the start of the episode while the user is in the middle of it."""
    from saitenka.app.prefetch import upcoming_cue_texts
    from saitenka.subtitles import CueIndex

    assert upcoming_cue_texts(None, 2, text="one", preferred=-1) == []
    assert upcoming_cue_texts(CueIndex([]), 2, text="one", preferred=-1) == []
    index = CueIndex(parse_srt(_srt(["one", "two"])))
    assert upcoming_cue_texts(index, 2, text="not in this file", preferred=-1) == []


def test_warming_puts_the_next_new_words_first():
    """N+1 words are the likeliest hover and mine target, so they lead — everything else follows in
    line order."""
    from saitenka.app.prefetch import _candidates

    tokens = [_tok("既知"), _tok("新出"), _tok("既知2")]
    styles = [_style("known"), _style("n+1"), _style("known")]

    assert [i for _p, i, _t in _candidates(tokens, styles, _Content())] == [1, 0, 2]


def test_warming_skips_repeats_of_a_lemma_already_queued():
    """The same word twice in one line is one warm — the cache is keyed by lemma, so the second
    would decode nothing new and displace a word that would."""
    from saitenka.app.prefetch import _candidates

    tokens = [_tok("見る", "見る"), _tok("見た", "見る"), _tok("猫")]

    assert len(_candidates(tokens, [], _Content())) == 2


def test_warming_skips_what_the_tokenizer_calls_non_content():
    from saitenka.app.prefetch import _candidates

    tokens = [_tok("は"), _tok("猫")]

    assert [t.surface for _p, _i, t in _candidates(tokens, [], _Content(skip={"は"}))] == ["猫"]


def test_warming_a_line_with_no_styles_yet_still_queues_its_words():
    """Scoring can lag the tokenization; treating a missing style row as "not N+1" keeps the warm
    running rather than dropping the line."""
    from saitenka.app.prefetch import _candidates

    assert len(_candidates([_tok("猫"), _tok("犬")], [], _Content())) == 2


def _srt(lines):
    return "".join(
        f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n{text}\n\n"
        for n, text in enumerate(lines, 1)
    )
