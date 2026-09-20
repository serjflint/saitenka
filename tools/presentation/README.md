# Presentation qualification

`producer.json` pins upstream revisions and patch hashes. `build.py` exports those revisions from
local clones, applies the patches, installs static libass into an isolated prefix, links mpv against it,
and runs both projects' C tests. It never changes the input clones or user installation. Native
dependencies come from the host; this reproduces source/API provenance, not identical binary bytes.
The receipt binds the libass archive and verified link command to the mpv binary hash. The patches
retain their upstream projects' licenses.

```sh
uv run tools/presentation/build.py /tmp/presentation-build \
  --mpv-repo /path/to/mpv-clone --libass-repo /path/to/libass-clone
uv run tools/presentation/run.py /tmp/presentation-run \
  --mpv /tmp/presentation-build/mpv/build/mpv
```

The runner uses the installed `saitenka` entrypoint (`--cli` overrides it), an isolated config,
generated video, bundled Noto Sans JP, and explicit known words. It starts at zero without taking
focus. No renderer/scorer is monkeypatched. A source or binary mismatch fails qualification.
`--source shadow` and `--delay -1` exercise alternate geometry and clock inputs. `--stock --mpv ...`
checks the reactive consumer separately; its acceptance result makes no frame-alignment claim.
All scenarios require installed scanning boxes for each authored visit. `--input-changes` exercises
clock, visibility, viewport and track transitions as an explicit negative qualification control:
the current producer cannot certify their native cutover, so the report must remain unqualified.

`manifest.json` declares each expected appearance independently of uploads. The analyzer in
`saitenka.app.frame_presentation` is shared by this runner and `tools/report_color_latency.py`
(`--presentation-manifest`). The GPU endpoint requires complete producer and consumer evidence,
matching IPC owner/slot/payload, all prescribed warm preparations ready, aligned first/last
compositions, and no stale/duplicate color. An absent display or truncated trace fails qualification.

The producer CI job exercises native contracts. Presentation changes also require an exact-source
installed-run receipt from a working display; passing producer CI alone is insufficient. The receipt
records source, binary, font, fixture and config hashes. Physical display latency and other platforms
remain separate qualification work.
