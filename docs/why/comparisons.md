# How it compares

Saitenka, [SubMiner](https://github.com/ksyasuda/SubMiner),
[Autocards](https://learnjapanese.moe/autocards/), and
[Anki Miner](https://github.com/0xzerolight/anki_miner) put Japanese vocabulary into Anki.
[Migaku](https://migaku.com/) can export there too, but also ships its own study system. Together they
approach immersion from five different angles.

- **Saitenka** is a grounded, FSRS-driven engine composited inside mpv — colored subtitles, a hover
  dictionary tooltip, and one-key mining, all drawn into mpv's own video surface.
- **SubMiner** is a feature-broad Electron app with a large integration surface and turn-key desktop
  installers (AniList scrobbling, Jellyfin integration, a media launcher, cross-machine sync).
- **Autocards** is a retroactive media back-filler: it doesn't create cards or show dictionaries — it
  batch-attaches a screenshot and sentence audio to text-only cards you made elsewhere, by matching each
  card's Sentence field against the subtitle's timing.
- **Anki Miner** is a batch-mining desktop GUI: point it at a folder, whole series, YouTube playlist,
  audiobook, manga, or ebook, review the candidate words in a curator, and it builds a frequency-ranked
  deck in one pass — the opposite pole from Saitenka's live, one-word-at-a-time overlay.
- **Migaku** is a commercial platform: its Chrome extension and mobile apps make
  streaming video, local files, webpages, reader documents, and camera OCR interactive. It creates
  cards in Migaku Memory; the browser extension can also send them to Anki. It trades Saitenka's narrow
  local-mpv focus for broader media coverage, built-in courses, cloud sync, and optional AI
  explanations/subtitles. Its current Anki bridge and several companion tools are open source, though
  the platform as a whole is not.

So this is a map of trade-offs across different jobs, not a scoreboard. For raw rendering and lookup
latency, see [Speed](benchmarks.md); to try it, see [Get started](../start/install.md).

## Capability matrix

| Capability | 再点火 Saitenka | SubMiner | Autocards | Anki Miner | Migaku |
|---|:---:|:---:|:---:|:---:|:---:|
| Category | live in-mpv engine | Electron mining app | retroactive enricher | batch mining GUI | commercial immersion platform |
| Runs inside mpv's **own surface** — no second window, fullscreen/airspace-safe | ✅ | ❌ | ❌ | ❌ | ❌ [older mpv add-on opens a browser UI](https://github.com/migaku-official/migaku-mpv/blob/a20b49219be1b31967138128845f8ba7f8a54eb2/migaku_mpv.py) |
| Multi-dictionary Yomitan tooltip · pitch accent · frequency | ✅ | ✅ | ❌ | ✅ | ⚠️ [dictionary](https://migaku.com/faq/features) + [frequency](https://migaku.com/blog/changelog) |
| One-key **Anki mining** with video audio + screenshot | ✅ | ✅ | ❌ | ✅ | ✅ [browser extension](https://migaku.com/blog/youtube/card-creation-from-video-content-migaku-browser-extension) |
| Bulk card creation | ✅ | ✅ | ❌ | ✅ | ⚠️ [Migaku app; surface-specific](https://migaku.com/blog/changelog) |
| **Retroactive media back-fill** onto existing text-only cards (sentence↔sub-timing match) | ❌* | ❌ | ✅ | ❌ | ❌ |
| **Reading-aware** known-word matching (homograph-safe) | ✅ | ✅ | ❌ | ❌ | ⚠️ [own word-status model](https://migaku.com/blog/youtube/supercharge-your-language-learning-tracking-learned-words) |
| **N+1** sentence targeting | ✅ | ✅ | ❌ | ✅ | ⚠️ [known-word recommendations](https://migaku.com/faq/features) |
| **Live FSRS review-state** coloring — forgotten words resurface | ✅ | ❌ | ❌ | ❌ | ⚠️ [own word-status model](https://migaku.com/blog/youtube/supercharge-your-language-learning-tracking-learned-words) |
| Account-free operation | ✅ | ✅ | ✅ | ✅ | ❌ [account required](https://migaku.com/faq/features) |
| Dictionary-sourced definitions | ✅ | ✅ | ❌ | ✅ | ✅ [real/user dictionaries](https://migaku.com/faq/features) |
| Optional AI explanations / translations | ❌ | ❌ | ❌ | ❌ | ✅ [built in](https://migaku.com/faq/features) |
| Batch folder / whole-series **frequency-ranked deck building** | ❌ | ❌ | ❌ | ✅ | ❌ |
| Manga mining (mokuro) | ❌ | ❌ | ❌ | ✅ | ❌ |
| Document/web text mining (epub/html/txt/rtf · webpages · pasted text) | ❌ | ❌ | ❌ | ⚠️ epub/txt · pasted text | ✅ [Reader formats](https://migaku.com/blog/changelog) · [web + clipboard](https://migaku.com/faq/features) |
| Camera OCR mining | ❌ | ❌ | ❌ | ❌ | ✅ [mobile OCR](https://migaku.com/faq/features) |
| Streaming video mining (YouTube · Netflix · Disney+ · Viki) | ❌ | ❌ | ❌ | ⚠️ YouTube import | ✅ [desktop sites](https://migaku.com/faq/getting-started) |
| Audiobook / **YouTube URL + playlist** batch mining (yt-dlp) | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Pre-mine word curation** — review candidates before cards | ❌ | ❌ | ❌ | ✅ | ❌ |
| Built-in courses + spaced-repetition app | ❌ | ❌ | ❌ | ❌ | ✅ [Migaku Memory](https://migaku.com/faq/features) |
| jimaku.cc · TsukiHime subtitle fetch | ✅ | ✅ | ❌ | ❌ | ❌ |
| Built-in subtitle **retiming/resync** (alass) | ✅ automatic | ⚠️ | ❌ external tool | ✅ utility | ⚠️ [manual timing offsets](https://migaku.com/blog/changelog) |
| Built-in subtitle generation (when no subs exist) | ❌ | ❌ | ❌ | ✅ local ASR | ✅ [Migaku AI service](https://migaku.com/blog/changelog) |
| Extra subtitle sources (AniMeTosho) · YouTube subs | ⚠️ [AniMeTosho via TsukiHime](../usage/features.md#jimaku-fetch) | ✅ | ❌ | ⚠️ YouTube only | ⚠️ streaming-site subs |
| AniList **progress scrobbling** | ❌ | ✅ | ❌ | ❌ | ❌ |
| Jellyfin integration · media launcher (fzf/rofi) | ❌ | ✅ | ❌ | ❌ | ❌ |
| Content comprehension analysis | ✅ | ✅ | ❌ | ✅ | ✅ [personalized difficulty](https://migaku.com/faq/features) |
| Local session history | ✅ | ✅ | ❌ | ✅ | ⚠️ [browser history + resume progress](https://migaku.com/blog/changelog) |
| Cross-machine data sync | ❌ | ✅ | ❌ | ❌ | ✅ [cloud/local sync](https://migaku.com/blog/changelog) |
| Mined-audio loudness normalization | ✅ [opt-in](../usage/configuration.md#mining-to-anki) | ✅ | ❌ | ❌ | ❌ |
| Built-in **latency profiling / OpenTelemetry traces** | ✅ | ❌ | ❌ | ❌ | ❌ |
| Free-threaded **parallel rendering** (Python 3.14t) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Account + paid plan required after trial | ❌ | ❌ | ❌ | ❌ | ✅ [10-day trial](https://migaku.com/signup); [recurring or Lifetime](https://migaku.com/faq/getting-started) |
| One-command installer / portable bundle | ✅ | ✅ | ✅ | ✅ | ⚠️ browser/mobile stores |
| Native desktop packages (AppImage · DMG · AUR · winget) | ❌ | ✅ | ❌ | ⚠️ AppImage · deb · exe | ❌ Chrome extension |
| Mobile apps | ❌ | ❌ | ❌ | ❌ | ✅ [iOS + Android](https://migaku.com/faq/getting-started) |
| Source / license | Apache-2.0 core | GPL-3.0 | GPL-3.0 | GPL-3.0 | ⚠️ [proprietary platform](https://migaku.com/terms) + [GPL-3.0 Anki add-on](https://github.com/migaku-official/Migaku-Anki-Addon/tree/1507fc054319cd94767888427de132ee9f3ff1f3) and [companion tools](https://github.com/migaku-official) |
| Linux · macOS · Windows | ✅ | ✅ | ❌ Windows | ✅ | ⚠️ Chrome desktop + mobile apps |

<sub>✅ yes · ❌ no / out of scope · ⚠️ partial or a different mechanism ·
\* [on the roadmap](https://github.com/serjflint/saitenka/issues)</sub>

Linked Migaku cells use first-party product material and public source checked on **2026-08-13**. The
current Anki add-on is active; the public mpv integration is older and opens its study UI in a browser,
so it is evidence for historical/companion capabilities rather than the current platform's architecture.

## Where it fits in your setup

Saitenka is the **study layer at the mpv playback point**. It doesn't acquire, organize, serve, or track
your library — it composes with the tools that do, rather than replacing them. If you already run a
media-server rig, this is the division of labor:

| Pipeline stage | Common tools | Saitenka |
|---|---|:---:|
| Acquire episodes | [Sonarr](https://sonarr.tv/) (PVR — monitors RSS, grabs + renames) · [Taiga](https://taiga.moe/) (RSS/torrent feeds, Windows) · Usenet/torrent clients | — |
| Identify & organize the files | [Shoko](https://shokoanime.com/) (AniDB hashing) + Shokofin | — |
| Serve & stream the library | [Jellyfin](https://jellyfin.org/) · Plex · Emby | — |
| Synchronize a watch party | [Syncplay](https://syncplay.pl/) (coordinates each friend's local player) | ✅ runs alongside |
| **Play the file** | **[mpv](https://mpv.io/)** | ✅ **attaches here** |
| Color words · dictionary · mine | **Saitenka** | ✅ its whole job |
| Spaced repetition | [Anki](https://apps.ankiweb.net/) + FSRS | ✅ mines in via AnkiConnect |
| Track list · discover · scrobble | [Taiga](https://taiga.moe/) (Windows) · [Trackma](https://github.com/z411/trackma) (cross-platform) | runs alongside |

**The one hard rule:** Saitenka draws into a **real, local mpv** process — over its IPC socket, with the
auto-start plugin. It rides the *player*, not the *server*, so it works with a Jellyfin/Shoko library only
when you play the file in mpv. Watching *inside* a Jellyfin/Plex client — web, TV, or phone — is out of
reach, because those aren't mpv.

## The short version

- Reach for **SubMiner** if you want the widest set of turn-key integrations and a packaged desktop app
  (AniList scrobbling, Jellyfin, a media launcher, cross-machine sync, native installers).
- Reach for **Autocards** if you make cards fast in a browser texthooker and want to attach the media in
  one pass afterward (Windows-only).
- Reach for **Anki Miner** if you want to batch-mine whole series, manga, ebooks, audiobooks, or YouTube
  into a frequency-ranked deck, curating the candidate words before cards are made — a tabbed desktop app,
  not a live overlay.
- Reach for **Migaku** if you want one commercial platform across streaming sites, local video, web reading,
  mobile OCR, and built-in courses/SRS — and accept an account, cloud-connected features, and a
  proprietary Chrome/mobile stack.
- Reach for **Saitenka** if you want a fast, single-surface, FSRS-grounded engine that draws straight into
  mpv — no second window, forgotten words resurfacing from your review state.

The workflows compose — Autocards' back-fill is
[on Saitenka's roadmap](https://github.com/serjflint/saitenka/issues) as an in-engine step.

## Visual parity harness

Comparison isn't only about features. Saitenka ships a visual parity harness in `tools/visual_compare/` that
renders its in-mpv tooltip side-by-side with real Yomitan and SubMiner popups for the same words, so its
dictionary UI can be checked against the tools it reproduces. (No screenshots here yet — the harness
generates them locally.)
