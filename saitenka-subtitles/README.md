# saitenka-subtitles

Read a subtitle file into cues, and answer where on screen a given token was drawn — the reading
service's subtitle capability, independent of any particular player.

SRT and ASS parse into the same `Cue` model, and an interval index answers "which cue is on screen
at t". Beyond that, an ASS document can be rewritten into a *hit-map* — one colour per token — and
rendered through libass, so a token's on-screen rectangle comes from the same layout engine the
viewer actually saw, rather than from a second, guessing implementation of the same rules.

```python
from saitenka_subtitles import CueIndex, parse_cues

cues = parse_cues(Path("episode.ass").read_text(encoding="utf-8"))
CueIndex(cues).at(93.5)
```

## What it does not do

No player, no compositing, no scoring, and no rendering of its own pixels. The libass geometry
backend needs the optional `libasslite` binding at run time and is absent without it; a null backend
answers the same port so a host can run without one.

Nor does it measure itself: the backend takes a telemetry sink and defaults to a no-op, so what gets
recorded is the host's decision and this package stays free of one.

Extracted from [Saitenka](https://github.com/serjflint/saitenka), which is its first consumer.
