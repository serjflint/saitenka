# How it compares

Saitenka, [SubMiner](https://github.com/ksyasuda/SubMiner),
[Autocards](https://learnjapanese.moe/autocards/), and
[Anki Miner](https://github.com/0xzerolight/anki_miner) put Japanese vocabulary into Anki.
[Migaku](https://migaku.com/) and [JitenMPV](https://github.com/Sirush/JitenMPV) instead feed their own
study system. Together they approach immersion from six different angles.

- **Saitenka** is a grounded, FSRS-driven engine composited inside mpv — colored subtitles, a hover
  dictionary tooltip, and one-key mining, all drawn into mpv's own video surface.
- **JitenMPV** is Saitenka's closest peer in shape and its opposite in where the data lives: an mpv
  plugin that colors subtitles by word state and pops a dictionary on hover, but with parsing, the
  dictionary, and the scheduler all served by the [Jiten](https://jiten.moe) account behind an API key.
  Nothing is local, so it works on any machine you log in from and not at all offline — and mining
  goes to a Jiten study deck, never to Anki. In exchange for that account it can do the one thing a
  read-only Anki reader cannot: **grade the card from the popup** while the video is paused.
- **SubMiner** is a feature-broad Electron app with a large integration surface and turn-key desktop
  installers (AniList scrobbling, Jellyfin integration, a media launcher, cross-machine sync). Its
  per-card timing/context review happens after word selection; it is not batch candidate-word
  curation.
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

| Capability | 再点火 Saitenka | JitenMPV | SubMiner | Autocards | Anki Miner | Migaku |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Category | live in-mpv engine | live in-mpv plugin on a cloud account | Electron mining app | retroactive enricher | batch mining GUI | commercial immersion platform |
| Where the word state lives | local Anki collection | [Jiten account](https://jiten.moe) | local Anki collection | — | local Anki collection | Migaku account |
| Runs inside mpv's **own surface** — no second window, fullscreen/airspace-safe | ✅ | ⚠️ subtitles yes, [dictionary popup is a native window](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.App/Views/DictionaryPopupWindow.axaml) | ❌ | ❌ | ❌ | ❌ [older mpv add-on opens a browser UI](https://github.com/migaku-official/migaku-mpv/blob/a20b49219be1b31967138128845f8ba7f8a54eb2/migaku_mpv.py) |
| Colors **mpv's own libass render** — authored typesetting, fonts and effects survive | ✅ [opt-in `native_visible`](../usage/native-subtitles.md) | ❌ [replaces the cue with its own ASS overlay](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Rendering/OverlayRenderer.cs) | ❌ | ❌ | ❌ | ❌ |
| Multi-dictionary Yomitan tooltip · pitch accent · frequency | ✅ | ⚠️ [one server-side dictionary](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Api/JitenApiClient.cs); pitch + frequency | ✅ | ❌ | ✅ | ⚠️ [dictionary](https://migaku.com/faq/features) + [frequency](https://migaku.com/blog/changelog) |
| One-key **Anki mining** with video audio + screenshot | ✅ | ⚠️ mines to a Jiten study deck, not Anki; [media needs Jiten+](https://jiten.moe/jiten-plus) | ✅ | ❌ | ✅ | ✅ [browser extension](https://migaku.com/blog/youtube/card-creation-from-video-content-migaku-browser-extension) |
| Bulk card creation | ✅ | ❌ | ✅ | ❌ | ✅ | ⚠️ [Migaku app; surface-specific](https://migaku.com/blog/changelog) |
| **Retroactive media back-fill** onto existing text-only cards (sentence↔sub-timing match) | ❌* | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Reading-aware** known-word matching (homograph-safe) | ✅ | ✅ | ✅ | ❌ | ❌ | ⚠️ [own word-status model](https://migaku.com/blog/youtube/supercharge-your-language-learning-tracking-learned-words) |
| **N+1** sentence targeting | ✅ | ✅ | ✅ | ❌ | ✅ | ⚠️ [known-word recommendations](https://migaku.com/faq/features) |
| **Live FSRS review-state** coloring — forgotten words resurface | ✅ | ✅ [server-side scheduler](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Api/Models/FsrsState.cs) | ❌ | ❌ | ❌ | ⚠️ [own word-status model](https://migaku.com/blog/youtube/supercharge-your-language-learning-tracking-learned-words) |
| **Grade the card from the overlay** — Again/Hard/Good/Easy on the hovered word | ❌ | ✅ [source](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Interaction/InlineReviewService.cs) | ❌ | ❌ | ❌ | ⚠️ [separate Memory app](https://migaku.com/faq/features) |
| **Blur** words by state until you hover them | ❌ | ✅ [source](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Interaction/BlurHoverManager.cs) | ❌ | ❌ | ❌ | ⚠️ |
| Account-free operation | ✅ | ❌ [account + API key](https://jiten.moe/settings) | ✅ | ✅ | ✅ | ❌ [account required](https://migaku.com/faq/features) |
| Works with no network — lookup, coloring, mining | ✅ | ❌ [the track is batch-parsed server-side at file open](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Plugin/PreParseService.cs); every state change is a call | ✅ | ✅ | ✅ | ❌ |
| Dictionary-sourced definitions | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ [real/user dictionaries](https://migaku.com/faq/features) |
| Optional AI explanations / translations | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ [built in](https://migaku.com/faq/features) |
| Batch folder / whole-series **frequency-ranked deck building** | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Manga mining (mokuro) | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Document/web text mining (epub/html/txt/rtf · webpages · pasted text) | ❌ | ❌ | ❌ | ❌ | ⚠️ epub/txt · pasted text | ✅ [Reader formats](https://migaku.com/blog/changelog) · [web + clipboard](https://migaku.com/faq/features) |
| Camera OCR mining | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ [mobile OCR](https://migaku.com/faq/features) |
| Streaming video mining (YouTube · Netflix · Disney+ · Viki) | ❌ | ❌ | ❌ | ❌ | ⚠️ YouTube import | ✅ [desktop sites](https://migaku.com/faq/getting-started) |
| Audiobook / **YouTube URL + playlist** batch mining (yt-dlp) | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Pre-mine word curation** — review candidates before cards | ❌ | ⚠️ [per-card review: screenshot, waveform trim, sentence edit](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.App/Views/MiningReviewWindow.axaml) | ⚠️ [per-card timing/context review](https://github.com/ksyasuda/SubMiner/commit/84f718043ab6eadf9e21650908607ca92d5262c2) | ❌ | ✅ | ❌ |
| Built-in courses + spaced-repetition app | ❌ | ⚠️ [the Jiten web service is the SRS](https://jiten.moe) | ❌ | ❌ | ❌ | ✅ [Migaku Memory](https://migaku.com/faq/features) |
| jimaku.cc · TsukiHime subtitle fetch | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Built-in subtitle **retiming/resync** (alass) | ✅ automatic | ❌ | ⚠️ | ❌ external tool | ✅ utility | ⚠️ [manual timing offsets](https://migaku.com/blog/changelog) |
| Built-in subtitle generation (when no subs exist) | ❌ | ❌ | ❌ | ❌ | ✅ local ASR | ✅ [Migaku AI service](https://migaku.com/blog/changelog) |
| Extra subtitle sources (AniMeTosho) · YouTube subs | ⚠️ [AniMeTosho via TsukiHime](../usage/features.md#jimaku-fetch) | ❌ | ✅ | ❌ | ⚠️ YouTube only | ⚠️ streaming-site subs |
| AniList **progress scrobbling** | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Jellyfin integration · media launcher (fzf/rofi) | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Content comprehension analysis | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ [personalized difficulty](https://migaku.com/faq/features) |
| Local session history | ✅ | ❌ state is account-side | ✅ | ❌ | ✅ | ⚠️ [browser history + resume progress](https://migaku.com/blog/changelog) |
| Cross-machine data sync | ❌ | ✅ account-hosted | ✅ | ❌ | ❌ | ✅ [cloud/local sync](https://migaku.com/blog/changelog) |
| Mined-audio loudness normalization | ✅ [opt-in](../usage/configuration.md#mining-to-anki) | ❌ | ✅ | ❌ | ❌ | ❌ |
| Built-in **latency profiling / OpenTelemetry traces** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Free-threaded **parallel rendering** (Python 3.14t) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Languages beyond Japanese | ✅ [reading profiles](../usage/configuration.md#reading-profiles-a-non-japanese-main-language) | ❌ | ❌ | ❌ | ❌ | ✅ [many](https://migaku.com/faq/getting-started) |
| Account + paid plan required after trial | ❌ | ⚠️ free account required; [media mining needs Jiten+](https://jiten.moe/jiten-plus) | ❌ | ❌ | ❌ | ✅ [10-day trial](https://migaku.com/signup); [recurring or Lifetime](https://migaku.com/faq/getting-started) |
| One-command installer / portable bundle | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ browser/mobile stores |
| Native desktop packages (AppImage · DMG · AUR · winget) | ❌ | ⚠️ Windows setup program · self-contained archives | ✅ | ❌ | ⚠️ AppImage · deb · exe | ❌ Chrome extension |
| Mobile apps | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ [iOS + Android](https://migaku.com/faq/getting-started) |
| Source / license | Apache-2.0 core | ⚠️ [Apache-2.0](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/LICENSE) client, proprietary service | GPL-3.0 | GPL-3.0 | GPL-3.0 | ⚠️ [proprietary platform](https://migaku.com/terms) + [GPL-3.0 Anki add-on](https://github.com/migaku-official/Migaku-Anki-Addon/tree/1507fc054319cd94767888427de132ee9f3ff1f3) and [companion tools](https://github.com/migaku-official) |
| Linux · macOS · Windows | ✅ | ✅ | ✅ | ❌ Windows | ✅ | ⚠️ Chrome desktop + mobile apps |

<sub>✅ yes · ❌ no / out of scope · ⚠️ partial, a different mechanism, or a claim current first-party
material does not establish · \* [on the roadmap](https://github.com/serjflint/saitenka/issues)</sub>

Linked Migaku cells use first-party product material and public source checked on **2026-08-13**. The
current Anki add-on is active; the public mpv integration is older and opens its study UI in a browser,
so it is evidence for historical/companion capabilities rather than the current platform's architecture.
JitenMPV cells were read from its source at
[`6d3efe3`](https://github.com/Sirush/JitenMPV/commit/6d3efe3383c26efe46763ece126deb88f8348344) on
**2026-08-31**; its server side is not public, so every cell describes the client and the calls it makes.

### What JitenMPV does that Saitenka does not

Two mechanisms are worth taking, and neither needs Jiten's cloud:

| Mechanism | Verified signal | Why it matters here |
|---|---|---|
| The popup carries **Again / Hard / Good / Easy** (optionally a two-button pass/fail), and the same actions are bindable to keys, so a word you failed to recognize is graded where you noticed it — with the color updating in place | [`InlineReviewService`](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Interaction/InlineReviewService.cs) · [`PopupAction`](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Interaction/PopupAction.cs) | Saitenka already reads the FSRS state that colors the word; it never writes one back. AnkiConnect's `guiAnswerCard`/`answerCard` would close that loop, but grading outside a real Anki review session is a scheduling decision, not a UI one — it needs a deliberate contract before it ships |
| **Blur by state until hovered**, with a reveal delay — the subtitle stops being a crutch without being turned off | [`BlurHoverManager`](https://github.com/Sirush/JitenMPV/blob/6d3efe3383c26efe46763ece126deb88f8348344/src/JitenMPV.Core/Interaction/BlurHoverManager.cs) | A pure display policy over the coloring Saitenka already computes |

The rest of its design is the opposite trade to Saitenka's: one server parses, defines, schedules and
stores, so there is nothing to install a dictionary into, nothing that works on a plane, and no Anki
collection in the loop — but any machine you log in from is already set up.

## Adjacent mobile immersion tools

[Yomihon](https://github.com/yomihon/yomihon) and
[Chimahon](https://github.com/sohilsayed/chimahon) are active Mihon forks for language learners.
[PopLingo](https://poplingo.net/) is a separate Android overlay that works over manga readers and other
apps. They are not replacements for Saitenka's local-mpv engine, but their lookup and mining loops expose
useful adjacent prior art.

| Capability | 再点火 Saitenka | Yomihon | Chimahon | PopLingo |
|---|:---:|:---:|:---:|:---:|
| Primary surface | local mpv video | Mihon manga reader | Mihon manga · novel · video reader | system-wide Android overlay |
| Spatial image/screen OCR lookup | ❌ [planned](https://github.com/serjflint/saitenka/issues/337) | ✅ [manga pages](https://github.com/yomihon/yomihon/blob/b915c978035657c5c7d1365a42f51ea1204e4269/CHANGELOG.md) | ✅ [reader, video, other apps](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/CHANGELOG.md) | ✅ [other apps](https://poplingo.net/) |
| Yomitan-format dictionaries | ✅ | ✅ [source](https://github.com/yomihon/yomihon/tree/b915c978035657c5c7d1365a42f51ea1204e4269/domain/src/main/java/mihon/domain/dictionary) | ✅ [source](https://github.com/sohilsayed/chimahon/tree/9561d44132371564451f252dd1e434803872a782/chimahon/src/main/java/chimahon/dictionary) | ✅ [product page](https://poplingo.net/) |
| Dictionary-grounded pitch · frequency | ✅ | ✅ [source](https://github.com/yomihon/yomihon/tree/b915c978035657c5c7d1365a42f51ea1204e4269/app/src/main/java/eu/kanade/presentation/dictionary/components) | ✅ [source](https://github.com/sohilsayed/chimahon/tree/9561d44132371564451f252dd1e434803872a782/chimahon/src/main/java/chimahon/dictionary) | ⚠️ dictionary details, no pitch/frequency claim verified |
| One-action Anki card with contextual media | ✅ desktop Anki | ✅ [AnkiDroid + cropped image](https://github.com/yomihon/yomihon/blob/b915c978035657c5c7d1365a42f51ea1204e4269/CHANGELOG.md) | ✅ [AnkiDroid](https://github.com/sohilsayed/chimahon/tree/9561d44132371564451f252dd1e434803872a782/chimahon/src/main/java/chimahon/anki) | ⚠️ [no Anki integration advertised](https://poplingo.net/) |
| Recursive definition lookup | ✅ | ✅ [dictionary-authored links](https://github.com/yomihon/yomihon/blob/b915c978035657c5c7d1365a42f51ea1204e4269/app/src/main/java/eu/kanade/presentation/dictionary/components/DictionaryComponents.kt) | ✅ [three navigation modes](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/CHANGELOG.md) | ⚠️ [not advertised](https://poplingo.net/) |
| Video subtitle lookup/mining | ✅ | ❌ manga-only | ✅ | ⚠️ screen OCR, not subtitle-aware |
| Live Anki/FSRS state drives visible words | ✅ | ❌ | ❌ | ⚠️ no Anki/FSRS mechanism verified |
| Source / license | Apache-2.0 core | [Apache-2.0](https://github.com/yomihon/yomihon/blob/b915c978035657c5c7d1365a42f51ea1204e4269/LICENSE) | [GPL-3.0](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/LICENSE) | Google Play distribution; no public source/license verified |

<sub>Source and current product material checked on **2026-08-13**. A hard negative is used only where
the product architecture or inspected source establishes the absence; ⚠️ marks a different mechanism or
a claim that current first-party material does not establish.</sub>

### Transferable lessons for Saitenka

| Mechanism | Verified signal | Saitenka action |
|---|---|---|
| Mining continues without blocking lookup/player interaction; optional media is prepared on demand | [Chimahon lifecycle](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/CHANGELOG.md) · [marker gate](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/chimahon/src/main/java/chimahon/anki/AnkiSentenceAudio.kt) | [Defer marker-required media and Anki work](https://github.com/serjflint/saitenka/issues/338) |
| Configurable subtitle cleanup removes study-irrelevant labels and markup before parsing | [Chimahon's tested subtitle filters](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/app/src/main/java/eu/kanade/tachiyomi/ui/player/utils/SubtitleRegexFilters.kt) | [Derive semantic text without rewriting the displayed cue](https://github.com/serjflint/saitenka/issues/335) |
| Explicit spatial OCR handles signs, hardsubs, and missing subtitle lines | [Yomihon spatial OCR](https://github.com/yomihon/yomihon/tree/b915c978035657c5c7d1365a42f51ea1204e4269/domain/src/main/java/mihon/domain/ocr) · [Chimahon video OCR](https://github.com/sohilsayed/chimahon/tree/9561d44132371564451f252dd1e434803872a782/chimahon/src/main/java/chimahon/ocr) | [Route current-frame OCR into the existing tooltip/miner](https://github.com/serjflint/saitenka/issues/337) |
| Card templates expose every value the lookup already grounds | [Chimahon Anki markers](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/chimahon/src/main/java/chimahon/anki/AnkiCardCreator.kt) | [Complete word-audio and pitch-graph markers](https://github.com/serjflint/saitenka/issues/339) |
| Per-title session/today/all-time views turn event rows into feedback | [Chimahon statistics](https://github.com/sohilsayed/chimahon/blob/9561d44132371564451f252dd1e434803872a782/CHANGELOG.md) | [Aggregate local trends by series and episode](https://github.com/serjflint/saitenka/issues/336) |

OCR is deliberately a fallback here, not a bid to turn Saitenka into a manga reader or continuously scan
video. Readings and pitch remain dictionary-grounded, and an OCR provider must stay optional and
license-compatible with the Apache core.

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

- Reach for **JitenMPV** if you already study on Jiten, want the same colored-subtitle loop with
  nothing to install locally and your state on every machine, and want to grade words from the popup —
  accepting an account, an API key, no offline mode, and no Anki.
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
