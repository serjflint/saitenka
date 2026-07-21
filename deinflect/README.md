# saitenka-overlay-deinflect (GPL-3.0)

Optional Japanese **deinflection chain** for [saitenka-overlay](../overlay) — the
🧩 `-て « -いる « -た` breakdown Yomitan shows under a headword.

> **License: GPL-3.0-or-later.** This package is a derivative work of
> [Yomitan](https://github.com/yomidevs/yomitan) (`engine.py` ports its
> `language-transformer.js`; `data/japanese_transforms.json` is a verbatim dump of its
> `japanese-transforms.js`). It is kept **separate from the Apache-2.0 core on purpose**:
> the overlay runs fine without it (it just won't show the inflection chain). See
> [`../LICENSING.md`](../LICENSING.md) for how the licenses fit together.

## Install (opt-in)

```bash
uv pip install saitenka-overlay[deinflect]        # once both are published
# or, from this checkout:
uv pip install ./deinflect
```

With it installed, the overlay picks it up automatically (`saitenka_deinflect`); without it,
`overlay.app.dictionary` falls back to an empty chain.

## API

```python
from saitenka_deinflect import inflection_chain
inflection_chain("聞こえてた", "聞こえる")   # -> ["-る", "-て", "-た"] (dict→surface order)
```
