# Licensing

This repository is **mixed-license**. Read this before redistributing.

| Path | License | Notes |
|---|---|---|
| `overlay/` (`saitenka`) | **Apache-2.0** | The core: renderer, reader, mining, CLI, installers. |
| `tools/`, `install/` | **Apache-2.0** | Anki/FSRS engine + installers. |
| `deinflect/` (`saitenka-deinflect`) | **GPL-3.0-or-later** | Derived from [Yomitan](https://github.com/yomidevs/yomitan) — see `deinflect/NOTICE`. |
| `taffylite/` (`taffylite`) | **MIT OR Apache-2.0** | Optional layout engine — a PyO3 binding of [taffy](https://github.com/DioxusLabs/taffy) (MIT) + [pyo3](https://github.com/PyO3/pyo3) (Apache-2.0/MIT). Permissive, so the `layout-engine` extra keeps the install Apache-2.0-clean — unlike `deinflect`. See `taffylite/NOTICE`. |
| `resvglite/` (`resvglite`) | **MPL-2.0** | Optional SVG rasterizer — a PyO3 binding vendoring [resvg](https://github.com/linebender/resvg) (MPL-2.0). MPL is *file-level* copyleft: it does not infect the larger work, so the `images` extra keeps the Apache-2.0 core clean — but the `resvglite` wheel itself carries MPL obligations. See `resvglite/NOTICE`. |

The top-level [`LICENSE`](LICENSE) is Apache-2.0 and governs everything **except** `deinflect/`,
which carries its own [`deinflect/LICENSE`](deinflect/LICENSE) (GPL-3.0). `taffylite/` is permissively
licensed (MIT/Apache-2.0), so it does not change that boundary. `resvglite/` vendors MPL-2.0 code, whose
copyleft is *file-scoped* — combining it (the `images` extra) leaves the Apache-2.0 core unaffected; only
the separately-published `resvglite` wheel must honour MPL (offer resvg's source, keep its notices).

## Why the split — and what it means for you

The inflection-chain feature (🧩 `-て « -いる « -た`) is a **port of Yomitan** code + a verbatim dump of
Yomitan's transform data. Yomitan is GPL-3.0, so that derived code **must** stay GPL-3.0 — it can't be
relicensed as Apache. It lives in its own package, `deinflect/`, kept **separate on purpose**:

- **The Apache-2.0 core does not depend on it.** `saitenka` installs and runs without
  `deinflect/`; it simply won't draw the inflection chain (`overlay.app.dictionary` falls back to an
  empty chain). So the default install and its distribution are Apache-2.0-clean.
- **Installing the add-on makes the combined work GPL-3.0.** If you `pip install saitenka[deinflect]`
  (or otherwise combine the two), the **whole, as distributed, is governed by GPL-3.0**. Apache-2.0 is
  one-way compatible with GPLv3, so the combination is legal — the Apache-licensed files keep their own
  notices — but you must offer the combined work under GPL-3.0 terms.

In short: **core alone = Apache-2.0; core + `deinflect` = GPL-3.0.**

## Vendored third-party assets

- **Noto Sans / Noto Sans JP** (`overlay/src/overlay/assets/fonts/`) — SIL Open Font License 1.1.
- **Kanji Stroke Order Font** (`overlay/src/overlay/assets/fonts/KanjiStrokeOrders.ttf`, the numbered
  stroke-order kanji headword) — BSD-3-Clause; stroke data © Ulrich Apel / the AAAA and Wadoku projects,
  font assembled by Tim Eyre. Redistributed with the notice in the adjacent `KanjiStrokeOrders-LICENSE.txt`.
- **Frequency dictionaries** (`tools/freq/*.zip`) — **not shipped** (gitignored, user-supplied); each
  keeps its upstream terms.
- **Dictionaries** shown in the panel are the user's own imported data (not shipped here); some carry
  attribution requirements (e.g. CC-BY-SA licensed data) — attribute them if you redistribute
  screenshots of their content.

*This is a description of the engineering setup, not legal advice.*
