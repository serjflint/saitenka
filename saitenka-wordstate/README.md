# saitenka-wordstate

What a learner already knows, and what that makes each token of a line.

Two sources: the **known-word set** read from Anki (via AnkiConnect, cached in SQLite and reconciled
by note mod-time, so a restart costs about a millisecond rather than a couple of hundred), and an
**FSRS retrievability snapshot** read from a *copy* of `collection.anki2` — never the live database.
Both are reading-aware, so a card teaching 床/ゆか does not mark 床/とこ as known.

`Scorer` combines them into a `TokenVerdict` per token: known, forgotten, learning, young; the single
unknown word of its sentence (N+1); its JLPT level; its frequency band.

```python
verdict = scorer.verdict(token)
verdict.is_n_plus_one, verdict.fsrs_state, verdict.jlpt, verdict.freq_band
```

## No colours

A verdict is a classification, never a colour — that is the whole reason this is a library. The
consumer owns the palette. Frequency and JLPT tables arrive as protocols (`FrequencyTable`,
`LevelTable`), so this reads the user's collection without depending on a dictionary.

Extracted from [Saitenka](https://github.com/serjflint/saitenka), which is its first consumer.
