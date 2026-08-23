# Keyboard shortcuts

The canonical shortcut reference for Saitenka. Press keys **inside the mpv window** (it must have
focus). What each action does is described in [Features](features.md); to rebind a key, see
[Configuration](configuration.md). This table mirrors the in-player ++F1++ reference (both are
generated from the same binding catalog), grouped the same way.

## Essentials & language

| Key | Action |
|---|---|
| Hover (mouse over a word) | Show the multi-dictionary tooltip |
| F1 | Toggle the compact in-player shortcut reference (++Esc++ closes it) |
| t | Toggle the English translation of the current line |
| Alt+T | Switch the primary subtitle between Japanese-only and English-only |
| Alt+J | Mark the current track as Japanese (untagged / misdetected subs) |
| Alt+O | Hide / show Saitenka; hidden mode restores mpv's native subtitles and OSD |
| Alt+Shift+P | Cycle the active reading profile (no-op with a single profile) |
| Ctrl+Shift+T | Re-time subtitles from here (or fetch from the provider chain) |
| Ctrl+J | Download subtitles — open the jimaku source picker |
| Alt+P | Toggle whether opening a tooltip auto-pauses mpv |
| Alt+A | Toggle full and hover-only learning annotations |
| `\` | Toggle the whole-track subtitle and deferred-capture sidebar |
| `` ` `` (backquote) | Toggle whole-track subtitle analysis and difficulty statistics |

## Subtitle navigation

| Key | Action |
|---|---|
| Alt+← | Jump to the previous subtitle line |
| Alt+→ | Jump to the next subtitle line |
| Alt+↓ | Replay the current subtitle line from its start |
| Ctrl+Z | Anchor subtitles to the current time (re-sync from here) |

## Capture & mining

| Key | Action |
|---|---|
| Alt+B | Bookmark the active cue for later review (no pause or seek) |
| Ctrl+M | Mine the hovered word (still frame) → Anki card + preview |
| Ctrl+Shift+M | Mine the hovered word with an animated (motion) clip |
| Shift+M | Bulk-mine every unknown word in the current line |
| p | Replay the last card preview + its audio (while a tooltip is open) |

## Tooltip actions (while a tooltip is up)

| Key | Action |
|---|---|
| Mouse wheel | Scroll the tooltip / sidebar to the lower sections |
| ↑ / ↓ | Scroll the tooltip up / down from the keyboard |
| a | Speak the hovered word (Japanese TTS) |
| c | Copy the hovered word + reading to the clipboard |
| Shift+C | Copy the whole subtitle cue |
| k | Open the kanji panel, or cycle through the word's kanji |
| Left-click | Activate the control under the pointer (buttons, links) |
| Right-click | Copy the word under the pointer |
| Esc | Close the tooltip (also the card preview / shortcut reference) |

## Useful mpv controls (passthrough)

Saitenka only intercepts the keys above; every other mpv binding still works. These are mpv's own
defaults, passed straight through:

| Key | Action |
|---|---|
| Space | Pause / resume (SyncPlay-safe) |
| z / Z / x | Subtitle delay −0.1 / +0.1 s / reset (mpv builtin, hold to repeat) |
| f | Toggle fullscreen |
| ← / → | Seek backward / forward |
| j / Shift+J | Cycle primary subtitle tracks forward / backward |
| v / Alt+V | Toggle native primary / secondary subtitle visibility |
| q | Quit mpv |

## Rebindable keys

Every Saitenka key above is configurable under `[keys]` in `overlay.toml` — set the field to any mpv
key name. Defaults match the tables above. See [Configuration](configuration.md) for the full file.

| Config key (`[keys]`) | Default | Action |
|---|---|---|
| `mine_key` | `Ctrl+m` | Mine the hovered word (still frame) |
| `mine_video_key` | `Ctrl+Shift+m` | Mine the hovered word with a motion clip |
| `mine_all_key` | `Shift+m` | Bulk-mine the current line |
| `preview_key` | `p` | Replay the last card preview |
| `translate_key` | `t` | Toggle the English translation |
| `subtitle_language_key` | `Alt+t` | Switch primary subtitle between Japanese and English |
| `subtitle_mark_jp_key` | `Alt+j` | Mark the current track as Japanese |
| `subtitle_retry_key` | `Ctrl+Shift+T` | Re-time / fetch subtitles |
| `sub_picker_key` | `Ctrl+j` | Open the jimaku download picker |
| `legacy_renderer_key` | `Ctrl+Shift+L` | Draw subtitles with Saitenka's own renderer instead of mpv's |
| `profile_cycle_key` | `Alt+Shift+p` | Cycle the active reading profile |
| `overlay_toggle_key` | `Alt+o` | Hide / show Saitenka |
| `hover_pause_key` | `Alt+p` | Toggle hover auto-pause |
| `bookmark_key` | `Alt+b` | Bookmark the active cue |
| `sidebar_key` | `\` | Toggle the sidebar |
| `analysis_key` | `` ` `` | Toggle whole-track analysis |
| `annotation_key` | `Alt+a` | Toggle full and hover-only annotations |
| `help_key` | `F1` | Toggle the in-player shortcut reference |
| `sub_prev_key` | `Alt+LEFT` | Previous subtitle line |
| `sub_next_key` | `Alt+RIGHT` | Next subtitle line |
| `sub_replay_key` | `Alt+DOWN` | Replay the current subtitle line |

The remaining Saitenka keys are fixed (not rebindable): ++Ctrl+z++ (anchor subtitles), ++a++ (speak),
++c++ (copy word), ++shift+c++ (copy cue), ++k++ (kanji panel), the mouse buttons/wheel, and
++up++ / ++down++ / ++Esc++ inside the tooltip.

## The F1 reference

Press ++F1++ inside the player at any time for a compact on-screen reference showing the
**effective** (post-rebind) shortcuts; ++Esc++ closes it.
