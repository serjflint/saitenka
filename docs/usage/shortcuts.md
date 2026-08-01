# Keyboard shortcuts

The canonical shortcut reference for Saitenka. Press keys **inside the mpv window** (it must have
focus). What each action does is described in [Features](features.md); to rebind a key, see
[Configuration](configuration.md).

## In-player keys

| Key | Action |
|---|---|
| Hover (mouse over a word) | Show the multi-dictionary tooltip |
| Mouse wheel (over the tooltip) | Scroll to the lower dictionary sections |
| Left-click the tooltip | Speak the hovered word |
| Alt+← | Jump to the previous subtitle line (sub-seek −1) |
| Alt+→ | Jump to the next subtitle line (sub-seek +1) |
| Alt+↓ | Replay the current subtitle line from its start (sub-seek 0) |
| z / Z | Sub-delay −0.1 s / +0.1 s (nudge timing for out-of-sync subs) |
| x | Reset sub-delay to 0 |
| a | Speak the hovered word (Japanese TTS) |
| c | Copy the hovered word + reading to the clipboard |
| Ctrl+M | Mine the hovered word → Anki card + preview (auto-plays the clip) |
| Shift+M | Bulk-mine every unknown word in the current line |
| t | Toggle the English translation of the current line |
| Alt+T | Switch the primary subtitle between Japanese-only and English-only |
| Alt+O | Hide/show Saitenka; hidden mode restores mpv's native subtitles and OSD |
| j / Shift+J | mpv: cycle primary subtitle tracks forward / backward |
| v / Alt+V | mpv: toggle primary / secondary native subtitle visibility |
| Ctrl+Shift+T | Retry enabled Japanese subtitle providers for the current media |
| Alt+P | Toggle whether opening a tooltip auto-pauses mpv |
| Alt+B | Bookmark the active cue for later review (no pause or seek) |
| `\` | Toggle the whole-track subtitle and deferred-capture sidebar |
| `` ` `` (backquote) | Toggle whole-track subtitle analysis and difficulty statistics |
| Alt+A | Toggle full and hover-only learning annotations |
| F1 | Toggle the compact in-player shortcut reference (`Esc` closes it) |
| p | Replay the last card preview + its audio |

## Rebindable keys

A subset of the keys above are configurable in `overlay.toml` — set the config key to any mpv key
name. Defaults match the table above. See [Configuration](configuration.md) for the full file.

| Config key | Default | Action |
|---|---|---|
| `hover_pause_key` | `Alt+p` | Toggle hover auto-pause |
| `overlay_toggle_key` | `Alt+o` | Hide/show Saitenka |
| `subtitle_language_key` | `Alt+t` | Switch primary subtitle between Japanese and English |
| `bookmark_key` | `Alt+b` | Toggle the active cue in the deferred-capture backlog |
| `sidebar_key` | `\` | Toggle the sidebar |
| `analysis_key` | `` ` `` | Toggle whole-track subtitle analysis |
| `annotation_key` | `Alt+a` | Toggle full and hover-only annotations |
| `help_key` | `F1` | Toggle the in-player shortcut reference |
| `subtitle_retry_key` | `Ctrl+Shift+T` | Retry Japanese subtitle providers |
| `translate_key` | `t` | Toggle the English translation |
| `sub_prev_key` | `Alt+LEFT` | Previous subtitle line (sub-seek −1) |
| `sub_next_key` | `Alt+RIGHT` | Next subtitle line (sub-seek +1) |
| `sub_replay_key` | `Alt+DOWN` | Replay current subtitle line (sub-seek 0) |
| `[mine] key` | `Ctrl+m` | Mine the hovered word |
| `[mine] all_key` | `Shift+m` | Bulk-mine the current line |
| `[mine] preview_key` | `p` | Replay the last card preview |

## mpv passthrough & the F1 reference

Saitenka only intercepts the keys above; every other mpv binding still works — ++space++ pauses,
++f++ toggles fullscreen, ++←++ / ++→++ seek, and ++q++ quits. The `z` / `Z` / `x` sub-delay nudges
are mpv's own defaults, passed straight through.

Press ++F1++ inside the player at any time for a compact on-screen reference showing the
**effective** (post-rebind) shortcuts; ++Esc++ closes it.
