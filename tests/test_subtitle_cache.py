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
