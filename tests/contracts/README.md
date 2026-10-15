# Portable behavior corpus

The corpus lets another Saitenka implementation prove observable compatibility without importing the
Python package. Each `corpus/*-v0.jsonl` file contains one scenario per line. `corpus/manifest.json`
locks scenario IDs, nested vectors, answer-free inputs, and expected outputs against silent changes.

`protocol.py` sends scenarios to any executable adapter over JSON Lines and judges the returned boundary
observations. The reference implementation is `default_adapter.py`; `examples/` contains a minimal shell
adapter proving that the transport is language-independent.

```console
uv run python tests/contracts/protocol.py --corpus journey -- ./path/to/adapter
```

The runner begins with a capability handshake, then sends `run` requests without `expect` or
`expect_contains`. An adapter returns the same protocol and scenario ID, `status: "ok"`, and normalized
observations. Diagnostics go to stderr. A replacement may implement one capability at a time.

Session expectations use either exact equality or ordered containment. Session observations are external
effects only: mpv commands, visible surfaces, clipboard writes, Anki writes, and bounded termination.
Symbolic IDs replace process IDs, addresses, timestamps, and implementation names.

| Corpus | Observable contract |
| --- | --- |
| `analysis` | Whole-track metrics, cache identity, and visible lifecycle |
| `cli` | Command inventory, parsing, config precedence, launch arguments, and profile commands |
| `config` | Run/attach normalization, editor behavior, persistent policy, and profile switching |
| `dictionary` | Ordered lookup, readings, phrases, profile language, and mining projection |
| `history` | Backlog persistence and playback-session summaries |
| `journey` | Cue → coloring → hover → dictionary → capture → Anki wiring |
| `picker` | Visible rows, pointer actions, scrolling, selection, and closure |
| `session` | Transport recovery, cue identity, commands, surfaces, navigation, and mining |
| `sidebar` | Cue windows, manual-scroll expiry, seeking, and playback neutrality |
| `subtitle` | Track selection, provider policy, artifacts, navigation, and render inputs |
| `tooltip` | Geometry, hit testing, interaction routing, and pause ownership |
| `wordstate` | FSRS retrievability and frequency/rareness laws |

Real sockets and subprocesses, deterministic worker schedules, storage migrations, font rasterization,
and open-ended registration checks remain native tests because they cover distinct boundary or concurrency
faults that finite portable scenarios cannot express.
