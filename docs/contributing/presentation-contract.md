# Subtitle presentation contract

Prepared timed color must enter and leave the same GPU composition as its native cue. An IPC
acknowledgment proves acceptance, not composition or physical presentation. Stock mpv retains a
reactive contract and is qualified separately.

## Regression matrix

| Contract | Maintained evidence |
| --- | --- |
| First and last composition agree | `frame_presentation` and `tool_tests/test_report_frame_presentation.py`; installed `test_live_presentation.py` |
| No expired, duplicate or retired color | Half-open producer interval tests; frame owner/slot/payload/interval joins; retirement and missing-cutover negative controls |
| Warm arrival constructs no ASS or underline payload | `test_native_subtitles.py` startup/resume/arrival assembly and `test_whole_cue.py` scan-versus-paint invariant |
| Every demanded appearance remains in the denominator | Independent synthetic fixture census; unstaged, all-late and repeated-visit controls |
| Authored identity differs from display clock and visit identity | `test_timed_osd.py`, `test_prepared_navigation.py`, runtime clock history and repeated-text fixture |
| Bounded admission and callback lifetimes | `test_timed_osd.py`: pending capacity, failed removal, reconnect, closed owner, late capability and backward eviction |
| Input changes revoke old preparation; ordinary cues preserve it | `test_native_subtitles.py`, `test_timed_osd.py`, prepared-artifact input matrix |
| Navigation traverses distinct authored starts | Generated multiplicity property in `test_subnav_policy.py`, production command assembly and installed navigation scenario |
| Scanning and paint eligibility stay independent | `test_whole_cue.py` observed-cue mode matrix and `test_native_subtitles.py` source-policy tests |
| Incomplete evidence cannot qualify | Missing GPU outcome, capture starting mid-cue, trace loss, wrong source/session, absent owner and retired acknowledgment controls |

Run `poe presentation-live` with `SAITENKA_LAYOUT_MPV` and `SAITENKA_STOCK_MPV` set. The installed
entrypoint must load the current source. The default test tier excludes real displays; explicitly
running this qualification requires both binaries and treats missing evidence as failure.
Build inputs and isolated-run usage are owned by [the producer recipe](../../tools/presentation/README.md).

## Report semantics

Ordinary reports retain bounded timed publication history, eviction counts, runtime clock epochs,
payload hashes and stage/removal outcomes. Display counters are unavailable without composition
evidence. Detailed traces retain queue/write/request correlation. Report analysis verifies trace
health and producer/consumer provenance before applying the independent appearance manifest.

Source fingerprints describe Python files captured at startup, not a later checkout state or a hash
of loaded machine code. Native build receipts identify the source patches and compiled binary;
they do not promise a bit-for-bit reproducible host toolchain.

## Qualification limits

An input invalidation is not itself a producer cutover. The analyzer refuses a run with invalidation
or reconnection after staging: the producer does not yet identify native input transitions. A manifest
composition number or asynchronous removal ACK cannot establish that boundary. Native track/viewport/font changes remain
**unqualified**, even when acceptance tests pass. The current natural-playback and navigation runner
does not certify atomic mid-cue input changes. This is a failing evidence requirement, not a green
skip or a claim that those transitions are instantaneous.

The producer CI job runs native API/interval tests. Presentation changes additionally require an
exact-source installed-run receipt from a working display. Physical presentation and cross-platform
display qualification remain separate from GPU submission.
