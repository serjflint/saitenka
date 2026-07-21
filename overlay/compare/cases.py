"""Comparison cases: same word/timestamp captured in SubMiner, re-rendered by Saitenka.

Each case ties a real subtitle line (from the Nippon Sangoku episode) at a timestamp to the word
hovered, plus the SubMiner reference screenshot and a crop box (fractions of the ref: x0,y0,x1,y1)
that isolates its Yomitan tooltip. Add a case by dropping a SubMiner screenshot in refs/ and a row here.
"""

CASES = [
    {
        "word": "本命", "surface": "本命", "reading": "ほんめい", "lemma": "本命", "pos": "名詞",
        "line": "ですが これが本命", "ts": "≈10:00",
        "ref": "honmei_subminer.jpeg", "crop": (0.56, 0.30, 1.00, 0.80),
        "expect_chain": [],
    },
    {
        "word": "聞こえる", "surface": "聞こえてた", "reading": "きこえてた", "lemma": "聞こえる", "pos": "動詞",
        "line": "せやけど よう遠くまで聞こえてたがー", "ts": "11:02",
        "ref": "kikoeru_subminer.jpeg", "crop": (0.585, 0.53, 0.855, 0.82),
        "expect_chain": ["-て", "-いる", "-た"],
    },
    {
        "word": "預ける", "surface": "預けた", "reading": "あずけた", "lemma": "預ける", "pos": "動詞",
        "line": "仮に 守衛殿に 奉書を預けたとしても—", "ts": "10:13",
        "ref": "azukeru_subminer.jpeg", "crop": (0.51, 0.52, 0.80, 0.82),
        "expect_chain": ["-た"],
    },
]
