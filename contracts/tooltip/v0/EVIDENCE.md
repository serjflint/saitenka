# Tooltip compatibility evidence

Tooltip v0 owns the visible geometry laws for scale, cache raster buckets, viewport caps, nested-popup
height, safe-area clamping, and choosing the roomier side of a subtitle word. Font rasterization, hit-map
agreement, hover concurrency, and the interaction state machine stay native boundaries.

Ignoring a positive user scale override makes `tooltip-scale-and-placement` fail at both 1080p and 4K.
