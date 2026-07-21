# Frequency dictionaries (user-supplied)

Drop Yomitan word-**frequency** zips into this dir (or point `--freq-dir` / `$SAITENKA_FREQ_DIR`
elsewhere). The tools **harmonic-blend** every `*.zip` here by default — no `--freq-dict` needed.

**Not shipped with the repo** — the zips here are gitignored. A good blend mixes a general corpus,
a JP-media / anime list, and a formal-written baseline.

Consumed by:
- **`anki_rank_dicts.py`** — blends all `*.zip` here to rank the Reactivate / Learn-new i+1 lists.
- **`anime_chooser.py`** — uses a freq zip here for rarity-based difficulty.

Use word-**frequency** dicts only (`term_meta` `freq`) — the loaders filter to `freq`, but keep this
dir clean. Add more freq zips (e.g. genre lists) to widen the blend. Keep only freely-redistributable
frequency data here.
