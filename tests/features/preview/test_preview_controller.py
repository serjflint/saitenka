from types import SimpleNamespace

from util import FakeIPC

from saitenka.app.features.preview import miner_ui
from saitenka.app.features.preview.preview_controller import PreviewController


def test_disabled_mining_preview_toasts_instead(monkeypatch):
    preview = PreviewController(FakeIPC())
    shown, toasts = [], []
    resolved = []
    monkeypatch.setattr(miner_ui, "preview_mined", lambda *args: shown.append(args))
    monkeypatch.setattr(miner_ui, "preview_existing", lambda *args: shown.append(args))

    preview.present_mined(
        lambda: resolved.append("ports"),
        lambda: resolved.append("source"),
        lambda text, *_args: toasts.append(text),
        SimpleNamespace(expression="本命"),
        None,
        None,
        enabled=False,
    )
    preview.present_existing(
        lambda: resolved.append("ports"),
        lambda: resolved.append("source"),
        lambda text, *_args: toasts.append(text),
        42,
        SimpleNamespace(expression="読む"),
        "exists",
        enabled=False,
    )

    assert shown == []
    assert resolved == []
    assert toasts == ["mined 本命", "already have 読む"]


def test_enabled_mining_preview_delegates_to_renderer(monkeypatch):
    preview = PreviewController(FakeIPC())
    calls = []
    monkeypatch.setattr(miner_ui, "preview_mined", lambda *args: calls.append(args))

    preview.present_mined(
        lambda: None,
        lambda: SimpleNamespace(toast=lambda *_args: None),
        lambda *_args: None,
        SimpleNamespace(expression="本命"),
        None,
        None,
        enabled=True,
    )

    assert len(calls) == 1
