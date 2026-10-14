# Playback v0 detection evidence

The corpus was run against reversible source faults before retiring the point tests:

| Scenario | Reversible fault | Detection |
|---|---|---|
| `same-sid-role-change-is-new-track` | omit cue retirement on a role change | the cue remained installed and the retirement event disappeared |
| `older-epoch-observation-is-inert` | admit an observation from the previous connection epoch | stale text replaced the current identity and emitted cue events |
| `old-text-is-observable-after-local-clear` | stop updating the observed-value projection on a local cue replacement | the returning mpv text was swallowed and the cleared cue remained authoritative |
| `render-space-observation-revises-geometry` | keep the previous render-space revision | both the emitted revision and final revision remained zero |

The byte-restoring witness runner and raw outputs remain under ignored `vibe/`.
