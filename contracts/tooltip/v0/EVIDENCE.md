# Tooltip compatibility evidence

Tooltip v0 owns the visible geometry laws for scale, cache raster buckets, viewport caps, nested-popup
height, safe-area clamping, choosing the roomier side of a subtitle word, and resolving an interactive token
under a frame-space pointer. Font rasterization, cross-engine hit-map agreement, hover concurrency, and the
interaction state machine stay native boundaries.

Ignoring a positive user scale override makes `tooltip-scale-and-placement` fail at both 1080p and 4K.
Ignoring the subtitle origin makes `tooltip-token-hit-testing` resolve no token for an offset cue.
Dropping word copy, link navigation, or pause ownership makes `tooltip-core-interaction` fail.
