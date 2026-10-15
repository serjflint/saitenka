# Subtitle compatibility evidence

The v0 corpus owns the externally reproducible relationship between mpv render settings and the
geometry profile Saitenka may safely use. The adapter exposes named refusals and normalized geometry;
it does not expose Python classes, private state, or cache implementation.

Representative fault checks:

- accepting `sub-scale=1.5` under the unscaled authored profile fails the refusal matrix;
- retaining the window margins while `blend-subtitles=yes` fails the normalized frame and origin;
- reading `sub-scale-signs` without inversion fails the renderer-state matrix.
- classifying a known English SID as Japanese fails the selection-policy matrix.
- treating a cue's end timestamp as inclusive fails the navigation-policy matrix.
- admitting every registered provider regardless of language fails the provider-policy matrix.
