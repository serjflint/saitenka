"""Provider-agnostic subtitle cache — real-filename-extension storage (#237).

The cache stores a finished sub under the real download's extension (not a synthetic ``.srt``), so an
ASS body no longer masquerades under a ``.srt`` name; lookup globs ``<slot>.*`` since the extension
isn't known before the file lands. One slot per (video, mode): a re-download under a different
extension evicts the stale sibling so the glob never returns two files for one logical slot.
"""

from __future__ import annotations

from saitenka.app import subtitle_cache as cache


def _video(tmp_path, name: str = "Show - 01.mkv", size: int = 100):
    v = tmp_path / name
    v.write_bytes(b"x" * size)
    return v


def test_stores_under_the_real_extension(monkeypatch, tmp_path):
    """An ASS download is cached as ``.ass`` (its real body), not relabelled ``.srt`` — so alass, which
    keys the format off the extension, no longer has to be rescued by the content-sniff at the seam."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    src = tmp_path / "[NanakoRaws] Show - 01.ass"
    src.write_text("[Script Info]\n[Events]\n", encoding="utf-8")

    dest = cache.store_subs(video, "Show", 1, src)

    assert dest.suffix == ".ass"
    assert cache.cached_subs(video, "Show", 1) == dest


def test_reextension_evicts_the_stale_sibling(monkeypatch, tmp_path):
    """Re-downloading the same slot under a different extension must not leave two files behind — the
    old ``.srt`` is evicted when the new ``.ass`` is written, so the lookup stays single-valued."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    srt = tmp_path / "old.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nold\n", encoding="utf-8")
    ass = tmp_path / "new.ass"
    ass.write_text("[Script Info]\n[Events]\n", encoding="utf-8")

    first = cache.store_subs(video, "Show", 1, srt)
    second = cache.store_subs(video, "Show", 1, ass)

    assert not first.exists()  # the `.srt` sibling was evicted
    assert second.suffix == ".ass"
    assert list(cache.subs_cache_dir().glob("*")) == [second]  # exactly one file for the slot
    assert cache.cached_subs(video, "Show", 1) == second


def test_a_slot_holding_both_formats_serves_the_one_a_fetch_would_have_picked(
    monkeypatch, tmp_path
):
    """Eviction is best-effort (a sibling mpv still holds open survives the write), so a slot CAN
    end up with both. Newest-mtime alone would then serve the `.srt` a fresh jimaku fetch has
    rejected since it started preferring `.ass` — and native geometry cannot use it."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    ass = tmp_path / "chosen.ass"
    ass.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    stored = cache.store_subs(video, "Show", 1, ass)
    # The stale sibling eviction could not remove: written after, so mtime favours it.
    lingering = stored.with_suffix(".srt")
    lingering.write_text("1\n00:00:01,000 --> 00:00:02,000\nold\n", encoding="utf-8")

    assert cache.cached_subs(video, "Show", 1) == stored


def test_a_hand_picked_sub_survives_the_next_launch(monkeypatch, tmp_path):
    """The source picker stores unsynced (the `-raw` slot); startup looks up the resyncing slot. A
    lookup that saw only its own slot made a deliberate pick last exactly one session — the next
    launch loaded whatever the auto-fetch had left, so a chosen `.ass` lost to an older `.srt`."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    auto = tmp_path / "auto.srt"
    auto.write_text("1\n00:00:01,000 --> 00:00:02,000\nauto\n", encoding="utf-8")
    chosen = tmp_path / "chosen.ass"
    chosen.write_text("[Script Info]\n[Events]\n", encoding="utf-8")

    cache.store_subs(video, "Show", 1, auto)  # the auto fetch, resynced slot
    picked = cache.store_subs(video, "Show", 1, chosen, resync=False)  # the picker

    assert cache.cached_subs(video, "Show", 1) == picked


def test_the_picker_slot_does_not_leak_into_a_lookup_that_wants_it_raw(monkeypatch, tmp_path):
    """The negative control for the direction: a `resync=False` lookup stays in its own slot, so
    widening the resyncing one did not merge the two."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    auto = tmp_path / "auto.ass"
    auto.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    cache.store_subs(video, "Show", 1, auto)  # resyncing slot only

    assert cache.cached_subs(video, "Show", 1, resync=False) is None


def test_a_retime_gets_its_own_slot_and_destroys_neither_other(monkeypatch, tmp_path):
    """ "Sync from here" pressed once should hold across launches. All three slots mean different
    things — fetched, hand-picked, re-timed — so the re-time gets its own rather than overwriting a
    file the user may want back, and wins the lookup by being newest at equal format."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    auto = tmp_path / "auto.ass"
    auto.write_text("[Script Info]\n[Events]\nauto\n", encoding="utf-8")
    chosen = tmp_path / "chosen.ass"
    chosen.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    fetched = cache.store_subs(video, "Show", 1, auto)
    raw = cache.store_subs(video, "Show", 1, chosen, resync=False)
    retimed = tmp_path / "chosen.win.ass"
    retimed.write_text("[Script Info]\n[Events]\nretimed\n", encoding="utf-8")

    published = cache.publish_retimed(raw, retimed)

    assert published is not None
    assert published.stem.endswith("-retimed")
    assert raw.exists() and fetched.exists()  # neither artifact was destroyed
    assert cache.cached_subs(video, "Show", 1) == published
    assert published.read_text(encoding="utf-8").endswith("retimed\n")


def test_pressing_sync_from_here_twice_refines_rather_than_accumulates(monkeypatch, tmp_path):
    """A drifting source takes a press per drift point, so the slot has to be overwritten — a second
    press must not leave `-retimed-retimed` behind, nor two files racing on mtime."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    chosen = tmp_path / "chosen.ass"
    chosen.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    raw = cache.store_subs(video, "Show", 1, chosen, resync=False)
    first_src = tmp_path / "first.ass"
    first_src.write_text("[Script Info]\n[Events]\nfirst\n", encoding="utf-8")
    second_src = tmp_path / "second.ass"
    second_src.write_text("[Script Info]\n[Events]\nsecond\n", encoding="utf-8")

    first = cache.publish_retimed(raw, first_src)
    assert first is not None
    second = cache.publish_retimed(first, second_src)  # re-timing the re-time

    assert second == first  # same slot, overwritten
    assert second is not None
    assert second.read_text(encoding="utf-8").endswith("second\n")


def test_a_retime_outside_the_cache_has_no_slot_to_publish_into(monkeypatch, tmp_path):
    """The negative control: a `--sub-file` or a sibling next to the video is not a cache entry, so
    a re-time of it must stay where it is rather than inventing a slot."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    loose = tmp_path / "beside-the-video.ass"
    loose.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    retimed = tmp_path / "beside-the-video.win.ass"
    retimed.write_text("[Script Info]\n[Events]\nretimed\n", encoding="utf-8")

    assert cache.publish_retimed(loose, retimed) is None


def test_glob_metacharacters_in_the_name_round_trip(monkeypatch, tmp_path):
    """A release group like ``[Erai]`` in the video stem lands in the slot name; the lookup escapes it
    so it isn't read as a glob character class (which would silently miss the cached file)."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path, name="[Erai-raws] Show - 01 [1080p].mkv")
    src = tmp_path / "dl.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\nねこ\n", encoding="utf-8")

    dest = cache.store_subs(video, "Show", 1, src)

    assert cache.cached_subs(video, "Show", 1) == dest


def test_resync_bookkeeping_siblings_do_not_shadow_the_real_sub(monkeypatch, tmp_path):
    """An in-place Ctrl+Shift+T / windowed resync leaves `<slot>.synced.srt`, its `.synced` marker, and
    `<slot>.win.srt` next to the cached `<slot>.srt`. The empty marker is newest (copy2 preserves the real
    file's older mtime), so a naive `<slot>.*` glob would return it — the lookup must keep only the
    single-extension slot file."""
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    src = tmp_path / "dl.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\nねこ\n", encoding="utf-8")
    real = cache.store_subs(video, "Show", 1, src)

    # Mimic resync's in-place artefacts written into the cache dir AFTER the real file.
    slot_stem = real.stem
    for sibling in (
        f"{slot_stem}.synced.srt",
        f"{slot_stem}.synced.srt.synced",
        f"{slot_stem}.win.srt",
    ):
        (cache.subs_cache_dir() / sibling).write_text("", encoding="utf-8")

    assert cache.cached_subs(video, "Show", 1) == real  # the marker/siblings never win


def test_extensionless_source_defaults_to_srt(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = _video(tmp_path)
    src = tmp_path / "subtitle_no_ext"
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")

    dest = cache.store_subs(video, "Show", 1, src)

    assert dest.suffix == ".srt"
