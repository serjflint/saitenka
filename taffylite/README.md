# taffylite

A deliberately thin [PyO3](https://pyo3.rs) binding of the [taffy](https://github.com/DioxusLabs/taffy)
flexbox layout engine, packaged as the optional **layout engine** behind saitenka's `LayoutBackend`
seam (`overlay/render/layout_backend.py`, issue #146).

It is a *fixed-size* geometry solver: every leaf's width/height is supplied by the caller (Pillow has
already measured the text on the Python side), so taffylite has **no measure callback and no Rust-side
cache** — just a pure flexbox solve. Its one job is robustness: a mature CSS engine in place of
hand-rolled offset arithmetic. Perf is a wash with the pure-Python default backend (both µs-scale,
dominated by Pillow raster), so it is **opt-in and parity-gated**, never the default.

## Why a Rust extension is opt-in

`DefaultLayoutBackend` (pure Python, zero-dependency) is always available and byte-identical. taffylite
is additive: it needs a per-platform prebuilt wheel (free-threaded `cp314t` wheels are still niche) or a
Rust toolchain to build from source. It builds and loads cleanly on free-threaded CPython (`pyo3 0.25`,
no abi3, `gil_used = false`) — that is *not* a blocker, just packaging friction the pure-Python core
avoids.

## API

```python
import taffylite

# Row-stack geometry (the LayoutBackend.cumulative primitive):
starts, ends = taffylite.column(heights, gaps, top_pad)

# A generic fixed-size flex tree (used by the conformance fixtures):
t = taffylite.Tree()
a = t.add_leaf(width=40, height=20)
b = t.add_leaf(width=40, height=20)
root = t.add_flex([a, b], direction="column", gap=8, padding=(0, 12, 0, 0))
t.set_root(root)
rects = t.compute()          # [(x, y, w, h), …] index-aligned to handles
```

The measure-callback / Rust-cache design for the *future* intra-block text-layout track (taffy driving
line wrap, calling back into Pillow) lives in the `experiment/layout-engine-bakeoff` worktree; it is out
of scope here (a non-goal of #146) and intentionally not ported.

## Licence

`MIT OR Apache-2.0`, matching taffy and pyo3. See `LICENSE` and `NOTICE`.
