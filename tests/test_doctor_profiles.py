"""Doctor profile checks (#254 W5) — per-profile tokenizer/language/dict validation + dangling selector.

Drives the real :func:`saitenka.app.doctor.check_profiles` with a monkeypatched ``load_config`` (the
suite's ``_hermetic_config`` already isolates the real file). Dict-title resolution is stubbed via
``_profile_dict_misses`` so the check's logic is tested without a populated DB.
"""

from __future__ import annotations

from saitenka.app import doctor


def _statuses(checks) -> dict[str, str]:
    """name+detail keyed → status, for asserting the specific line that fired."""
    return {c.detail: c.status for c in checks}


def test_single_default_profile_is_one_info_line(monkeypatch):
    monkeypatch.setattr(doctor, "load_config", lambda *_: {})
    checks = doctor.check_profiles()
    assert len(checks) == 1
    assert checks[0].status == "ok" and checks[0].info
    assert "single default profile" in checks[0].detail


def test_unknown_tokenizer_fails(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "load_config",
        lambda *_: {"profiles": {"broken": {"language": "de", "tokenizer": "nope"}}},
    )
    monkeypatch.setattr(doctor, "_profile_dict_misses", lambda *_a, **_k: [])
    fails = [c for c in doctor.check_profiles() if c.status == "fail"]
    assert any("tokenizer 'nope' not registered" in c.detail for c in fails)


def test_dangling_active_profile_warns(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "load_config",
        lambda *_: {"active_profile": "spanish", "profiles": {"french": {"language": "fr"}}},
    )
    monkeypatch.setattr(doctor, "_profile_dict_misses", lambda *_a, **_k: [])
    warns = [c for c in doctor.check_profiles() if c.status == "warn"]
    assert any(
        "active_profile='spanish'" in c.detail and "no [profiles.*]" in c.detail for c in warns
    )


def test_unimported_dict_title_warns_on_the_profile(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "load_config",
        lambda *_: {"profiles": {"french": {"language": "fr", "dicts": ["Missing FR"]}}},
    )
    monkeypatch.setattr(
        doctor,
        "_profile_dict_misses",
        lambda _db, _cfg, override: ["Missing FR"] if override == "french" else [],
    )
    checks = doctor.check_profiles()
    french = next(c for c in checks if c.detail.startswith("french"))
    assert french.status == "warn"
    assert "Missing FR" in french.detail


def test_healthy_named_profile_is_info(monkeypatch):
    monkeypatch.setattr(
        doctor, "load_config", lambda *_: {"profiles": {"french": {"language": "fr"}}}
    )
    monkeypatch.setattr(doctor, "_profile_dict_misses", lambda *_a, **_k: [])
    french = next(c for c in doctor.check_profiles() if c.detail.startswith("french"))
    assert french.status == "ok" and french.info
    assert "fr→en [latin]" in french.detail
