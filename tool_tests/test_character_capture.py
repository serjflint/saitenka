from __future__ import annotations

import pysubs2
from compare_cached_characters import request_for

from saitenka.app.subtitle_fonts import FontEnvironment


def test_whitespace_does_not_require_ink_or_remove_neighboring_token_identity():
    document = pysubs2.SSAFile()
    document.info.update(PlayResX="640", PlayResY="360")
    document.styles["Default"].fontname = "sans-serif"
    document.styles["Default"].fontsize = 40
    document.events.append(pysubs2.SSAEvent(start=1000, end=2000, text="猫　犬"))

    request = request_for(document.to_string("ass"), 1500, (640, 360), FontEnvironment())

    assert [token.surface for line in request.lines for token in line] == ["猫", "　", "犬"]
    assert {box.index for box in request.boxes} == {0, 2}
