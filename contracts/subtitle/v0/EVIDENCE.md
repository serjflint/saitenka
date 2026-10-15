# Subtitle compatibility evidence

The v0 corpus owns the externally reproducible relationship between mpv render settings and the
geometry profile Saitenka may safely use. The adapter exposes named refusals and normalized geometry;
it does not expose Python classes, private state, or cache implementation.

Representative fault checks:

- accepting `sub-scale=1.5` under the unscaled authored profile fails the refusal matrix;
- retaining the window margins while `blend-subtitles=yes` fails the normalized frame and origin;
- reading `sub-scale-signs` without inversion fails the renderer-state matrix.
