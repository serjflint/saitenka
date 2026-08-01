# Security Policy

## Supported versions

Security fixes land on `main` and ship in the next release. Only the **latest release** (and `main`)
are supported — please reproduce on the latest version before reporting.

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.** Report it privately via a
[GitHub security advisory](https://github.com/serjflint/saitenka/security/advisories/new). Only the
maintainer sees the report, and it is handled confidentially. You'll get an acknowledgement, a fix
worked out privately with you, a release, and a public advisory once users can upgrade.

Scope is **Saitenka itself**. Vulnerabilities in third-party dependencies (mpv, Anki/AnkiConnect,
dictionaries, Python packages) should go to their own maintainers; if a saitenka default or integration
*exposes* such a flaw, that part is in scope. There is no bug bounty — this is a volunteer project.

If you used an AI/LLM tool to prepare a report, please read the [AI Policy](.github/AI_POLICY.md) first
and describe the issue in your own words.

## Attack surface worth noting

Saitenka runs locally and touches a few boundaries a reporter may care about: it spawns and shares an
**mpv IPC socket/subprocess**, makes **localhost** calls to **AnkiConnect**, fetches subtitles over the
**network** (Jimaku), imports untrusted **Yomitan dictionary archives**, and can install an mpv
**user-script** into your config. Findings around those paths are especially welcome.

## What we do

- CVE scanning of the resolved lockfile in the pre-push gate (`uv run poe audit`, `uv audit`), with a
  second-opinion `uv run poe pip-audit` (PyPA Advisory DB + OSV) available.
- Static security linting via ruff's flake8-bandit (`S`) rules; each suppression is justified in-tree.
- A copyleft-license gate (`uv run poe licenses`) — only our own GPL `deinflect` add-on is permitted.
- Subprocess calls avoid `shell=True`; untrusted input (dictionary archives, subtitle files, IPC) is
  parsed defensively (`parse_cues` never raises).
