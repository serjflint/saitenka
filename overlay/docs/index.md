# Saitenka

Saitenka draws a Yomitan-style study overlay directly onto **mpv**'s video: Japanese subtitles
colored by your Anki/FSRS knowledge, a hover→multi-dictionary tooltip with pitch and frequency, and
one-key sentence mining into Anki.

It runs **local-first** — readings and pitch always come from your dictionaries, never from a model —
on Python 3.13+ including the free-threaded (no-GIL) build.

## Where to go next

- **[Running & configuration](running.md)** — install, the `run`/`attach` modes, every flag, and the
  config file.
- **[Architecture](architecture.md)** — the module map and data flow: tokenizer → dictionaries →
  render → mpv IPC.
- **[Benchmarks](benchmarks.md)** — the latency budgets and measured baselines.
- **[Changelog](changelog.md)** · **[Roadmap](roadmap.md)** — what shipped and what's next.

## Install

Saitenka is distributed as a [uv](https://docs.astral.sh/uv/) tool:

```bash
uv tool install "saitenka[full]"
```

Update later with `saitenka update` (see [Running & configuration](running.md) for the Windows
self-update details).
