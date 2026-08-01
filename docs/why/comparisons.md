# How it compares

Saitenka, [SubMiner](https://github.com/ksyasuda/SubMiner), and
[Autocards](https://learnjapanese.moe/autocards/) all put Japanese vocabulary from video into Anki, but
from three different angles.

- **Saitenka** is a grounded, FSRS-driven engine composited inside mpv — colored subtitles, a hover
  dictionary tooltip, and one-key mining, all drawn into mpv's own video surface.
- **SubMiner** is a feature-broad Electron app with a large integration surface and turn-key desktop
  installers (AniList scrobbling, Jellyfin integration, a media launcher, cross-machine sync).
- **Autocards** is a retroactive media back-filler: it doesn't create cards or show dictionaries — it
  batch-attaches a screenshot and sentence audio to text-only cards you made elsewhere, by matching each
  card's Sentence field against the subtitle's timing.

So this is a map of trade-offs across different jobs, not a scoreboard. For raw rendering and lookup
latency, see [Speed](benchmarks.md); to try it, see [Get started](../start/install.md).

## Capability matrix

| Capability | 再点火 Saitenka | SubMiner | Autocards |
|---|:---:|:---:|:---:|
| Category | live in-mpv engine | Electron mining app | retroactive enricher |
| Runs inside mpv's **own surface** — no second window, fullscreen/airspace-safe | ✅ | ❌ | ❌ |
| Multi-dictionary Yomitan tooltip · pitch accent · frequency | ✅ | ✅ | ❌ |
| One-key **+ bulk Anki mining** (audio, screenshot, reading, freq) | ✅ | ✅ | ❌ |
| **Retroactive media back-fill** onto existing text-only cards (sentence↔sub-timing match) | ❌* | ❌ | ✅ |
| **Reading-aware** known-word matching (homograph-safe) | ✅ | ✅ | ❌ |
| **N+1** sentence targeting | ✅ | ✅ | ❌ |
| **Live FSRS review-state** coloring — forgotten words resurface | ✅ | ❌ | ❌ |
| Grounded / local-first (readings never from an LLM) | ✅ | ✅ | ✅ |
| jimaku.cc · TsukiHime subtitle fetch | ✅ | ✅ | ❌ |
| Built-in subtitle **retiming/resync** (alass) | ✅ automatic | ⚠️ | ❌ external tool |
| Extra subtitle sources (Animetosho) · YouTube subs | ❌* | ✅ | ❌ |
| AniList **progress scrobbling** | ❌ | ✅ | ❌ |
| Jellyfin integration · media launcher (fzf/rofi) | ❌ | ✅ | ❌ |
| Episode analysis · local session history | ✅ | ✅ | ❌ |
| Cross-machine stats/history **sync** | ❌ | ✅ | ❌ |
| Mined-audio loudness normalization | ❌* | ✅ | ❌ |
| Built-in **latency profiling / OpenTelemetry traces** | ✅ | ❌ | ❌ |
| Free-threaded **parallel rendering** (Python 3.14t) | ✅ | ❌ | ❌ |
| One-command installer / portable bundle | ✅ | ✅ | ✅ |
| Native desktop packages (AppImage · DMG · AUR · winget) | ❌ | ✅ | ❌ |
| Core license | Apache-2.0 | GPL-3.0 | GPL-3.0 |
| Linux · macOS · Windows | ✅ | ✅ | ❌ Windows |

<sub>✅ yes · ❌ no / out of scope · ⚠️ partial · \* [on the roadmap](https://github.com/serjflint/saitenka/issues)</sub>

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
- Reach for **Saitenka** if you want a fast, single-surface, FSRS-grounded engine that draws straight into
  mpv — no second window, forgotten words resurfacing from your review state.

The workflows compose — Autocards' back-fill is
[on Saitenka's roadmap](https://github.com/serjflint/saitenka/issues) as an in-engine step.

## Visual parity harness

Comparison isn't only about features. Saitenka ships a visual parity harness in `overlay/compare/` that
renders its in-mpv tooltip side-by-side with real Yomitan and SubMiner popups for the same words, so its
dictionary UI can be checked against the tools it reproduces. (No screenshots here yet — the harness
generates them locally.)
