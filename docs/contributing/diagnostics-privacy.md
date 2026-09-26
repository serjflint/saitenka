# Diagnostics privacy policy

Saitenka is a local media tool, not a security product. It never uploads anything: a report leaves
the machine only when its user attaches it to an issue, usually a public one. The policy protects
that user from posting something they would not choose to post, at the lowest cost to
diagnosability. It does not defend against a determined analyst.

## What the default report removes

| Data | Why | How |
|---|---|---|
| Secrets: API keys, tokens, passwords | Account compromise | `redact()` at report time; config keys by name |
| The home directory and username | Identifies the person | `redact()` at report time |
| Media and subtitle file names, folder names, release names | Reveal what the user watches and where the file came from | Registered where resolved, replaced at write time in the file log and exported spans |

That list is closed. Anything else ships as logged.

## What the default report keeps

Subtitle and cue text, hovered words and readings, lookup queries, cue timings and digests,
Anki deck, note-type, profile and dictionary names, and paths outside the home directory. They are
low-sensitivity, and they are what a bug report is diagnosed from.

## Rules

- **One mechanism per class.** Removal happens where the table says, once. No second pass over an
  artifact that the first mechanism already covers.
- **Best effort.** A missed spelling is a bug to fix when a real log line shows it, not a reason to
  add machinery in advance. Do not add scrubbing for a class that is not in the removal table.
- **Blocking bar.** A privacy finding blocks a change only when it is a class in the removal table,
  reachable through a production path. Anything else is reported, not queued.
- **One test.** A canary test drives the main entry points (`run` and `attach`) and asserts that no
  removed class reaches the default bundle.
- **`--diagnostic-detail`** ships raw config, `mpv.conf`/`input.conf`, crash reports, `mpv.log` and
  other sessions, with secrets and the home directory removed. It is for a user who has read the
  manifest.
