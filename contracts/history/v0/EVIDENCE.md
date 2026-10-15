# History compatibility evidence

History v0 owns backlog identity, persistence, relocation and ambiguity policy, status history, privacy-safe
summaries, session event aggregation, cue deduplication, and persisted analysis summaries. SQLite schema
migration, the asynchronous writer thread, assembled bookmark capture, and owner retirement stay native.

Removing Unicode normalization, disabling unique-basename matching, counting a repeated cue twice, or
charging second-language playback to Japanese each makes the corresponding history scenario fail.
