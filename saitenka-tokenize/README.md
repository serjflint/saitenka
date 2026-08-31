# saitenka-tokenize

Segment a line of text into tokens carrying surface, lemma, reading, part of speech and character
offsets — the first of the capabilities a reading service would own.

Japanese runs on fugashi + unidic-lite; the lemma is what makes an inflected surface resolve for
lookup, so de-inflection comes for free. A Latin-script strategy ships alongside it, and a profile
selects one by name, so a language family can share a strategy and a new language can point at an
existing one with no code.

```python
from saitenka_tokenize import get_tokenizer

tokenizer = get_tokenizer("unidic")
[t.surface for t in tokenizer.tokenize("本を読む")]  # ['本', 'を', '読む']
```

## What it does not do

No dictionary and no scoring. Two operations — probing multi-token phrase terms, and merging a span
that is an attested headword — need to ask whether a form exists; both take that as a **callable**,
so this package stays dictionary-free and the search strategy stays swappable.

Extracted from [Saitenka](https://github.com/serjflint/saitenka), which is its first consumer.
