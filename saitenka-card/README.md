# saitenka-card

What a mined flashcard is made of: which fields the note type has, what goes in each, and how a
`{marker}` template renders.

A note comes out as a plain dict, so nothing here knows how it reaches Anki. Two ways to describe a
card are supported and they are exclusive: a logical→real field map (`expression` → `Expression`),
or a Yomitan-style template per field, which wins wholesale when set.

```python
from saitenka_card import CardData, MineConfig, build_note

build_note(MineConfig.from_preset("Lapis"), CardData("読む", "よむ", "<ol><li>to read</li></ol>"))
```

## What it does not do

No dictionary lookup, no media capture, no network. `CardData` arrives already looked up, and the
animated-clip spec is a value the host's encoder reads — this package describes the clip, it does
not make one.

Extracted from [Saitenka](https://github.com/serjflint/saitenka), which is its first consumer.
