# taffylite `Tree` fixtures

Two fixture sets drive `test_taffylite.py`'s generic-`Tree` conformance tests. Both are absolute
`[x, y, w, h]` rects index-aligned to handle order; a node is `{"leaf":[w,h]}`,
`{"leaf":[w,h,[l,t,r,b]]}` (margin), or `{"flex":{...}}`.

## `taffy_gentest_flex.json` — the external oracle (issue #150)

Vendored from **taffy [v0.7.7](https://github.com/DioxusLabs/taffy)** (MIT), the pin in
`../../Cargo.lock`. taffy generates its conformance suite from HTML fixtures rendered in **Chrome**
and commits the browser's computed layout as `assert_eq!`s in `tests/generated/flex/*.rs`; those
baked values are what `taffylite/tools/gen_taffy_fixtures.py` transcribes here (the `__border_box`
variant — taffylite leaves `box_sizing` at taffy's `BorderBox` default). Passing them proves
taffylite reproduces a real browser's flexbox, not just our own CSS reading — the same "steal the
real corpus, don't author it" move as overlay's vendored UAX #14 `LineBreakTest.txt` (#112).

Regenerate after bumping taffy (keep `TAFFY_TAG` in the generator in sync with `Cargo.lock`):

```sh
git clone --depth 1 --branch v0.7.7 https://github.com/DioxusLabs/taffy /tmp/taffy-src
uv run taffylite/tools/gen_taffy_fixtures.py /tmp/taffy-src
```

**Excluded subset.** taffylite's `Tree` exposes only fixed-size flex: leaves with an explicit
`(w, h)` + margin, and flex containers with row/column direction, symmetric gap, padding, margin,
fixed/auto box size, and wrap. The generator keeps a taffy fixture only if **every** node maps onto
that API, dropping the ~98% (523 of 533) that use anything else — flex grow/shrink/basis, min/max,
aspect-ratio, `%`/`auto` content sizing, `align-*`/`justify-*`, position/inset, overflow, grid or
block display, content-box sizing, text/measure leaves, `*-reverse` direction/wrap, or an asymmetric
gap under wrap (taffylite forces a symmetric gap). Those belong with the features that would express
them, not here.

## `flex_cases.json` — authored, CSS-spec-verified

Hand-written cases (row gap, column gap + padding, wrapping cross gap, a nested row-in-column) that
exercise combinations the narrow vendored subset misses — taffy's equivalents set `align-*`/
`justify-*` and so are excluded above. These are the spec oracle; the vendored corpus is the
external one.
