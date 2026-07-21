# Saitenka ↔ SubMiner tooltip comparison

Side-by-side of our in-mpv tooltip vs SubMiner's Yomitan popup, for the same words at the same
timestamps in the Nippon Sangoku episode. SubMiner isn't changing, so its side is a fixed reference
(real captures in `refs/`); our side re-renders from the configured dictionaries each run.

## Generate the side-by-sides

```bash
cd overlay
uv run python compare/generate.py     # → compare/out/<word>.png + _sheet.png
```

Needs `~/.config/saitenka/overlay.toml` pointing at your dictionaries (see `overlay.example.toml`).

## Parity test

```bash
uv run pytest tests/test_compare.py -v
```

Asserts our render carries the same frequency dictionaries and inflection chain SubMiner shows.
Skipped automatically when the (large, out-of-repo) dictionaries aren't present.

## Add a case

1. Screenshot SubMiner's popup for a word → save as `refs/<name>_subminer.jpeg`.
2. Add a row to `cases.py`: the word, its surface/reading/lemma/pos, the subtitle line + timestamp,
   the ref filename, and a `crop` (x0,y0,x1,y1 as fractions of the ref) isolating the popup.

## Live Yomitan reference (recommended)

`yomitan_capture.py` renders the **real Yomitan** popup (the engine SubMiner embeds) via Playwright,
reusing SubMiner's bundled extension + imported dictionaries — so the reference side regenerates for
any word instead of being screenshotted by hand. `generate.py` prefers a live capture (`out/yomitan/
<surface>.png`) over the static screenshot automatically.

```bash
uv add --dev playwright && uv run playwright install chromium   # once

# copy SubMiner's (locked) profile — its imported dicts live in IndexedDB keyed to the ext ID
EXT=jbjehhccmhejadgafflkalefjpnepkle
mkdir -p /tmp/yomitan-profile/Default/IndexedDB "/tmp/yomitan-profile/Default/Local Extension Settings"
cp -R ~/.config/SubMiner/IndexedDB/chrome-extension_${EXT}_0.indexeddb.* /tmp/yomitan-profile/Default/IndexedDB/
cp -R ~/.config/SubMiner/"Local Extension Settings"/$EXT "/tmp/yomitan-profile/Default/Local Extension Settings/"

# render Yomitan for the surface forms, then compose
uv run python compare/yomitan_capture.py 本命 聞こえてた 預けた
uv run python compare/generate.py
```

Notes: query the **surface** form (聞こえてた) so Yomitan deinflects it and shows the 🧩 chain. The
capture waits for network-idle + a stable entry height so async freq/pitch/media aren't cut. Re-copy
the profile if you re-import dictionaries in SubMiner.
