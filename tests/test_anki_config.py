"""AnkiConnect endpoint is user-configurable via [anki] (url / host+port / api_key)."""

from __future__ import annotations

import json

import ankiconnect_client.transport as anki_transport
import pytest

from saitenka.app import anki


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, ("http://127.0.0.1:8765", None)),
        (
            {"anki": {"url": "http://127.0.0.1:9999", "api_key": "SECRET"}},
            ("http://127.0.0.1:9999", "SECRET"),
        ),
        (
            {"anki": {"host": "127.0.0.1", "port": 8766}},
            ("http://127.0.0.1:8766", None),
        ),
    ],
    ids=["stock", "url-and-key", "host-and-port"],
)
def test_resolve_anki_connection(config, expected):
    assert anki.resolve_anki(config) == expected


def test_anki_client_reads_config_and_injects_key(monkeypatch, tmp_path):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('[anki]\nurl = "http://127.0.0.1:9001"\napi_key = "K"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    sent = {}

    class _Resp:
        def read(self):
            return json.dumps({"result": ["Expression"]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, **_kwargs):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(anki_transport.urllib.request, "urlopen", _fake_urlopen)
    client = anki.Anki()
    assert client.host == "http://127.0.0.1:9001"
    assert client.model_field_names("Mining") == ["Expression"]
    assert sent["url"] == "http://127.0.0.1:9001"
    assert sent["body"]["key"] == "K"  # apiKey injected into the request body


def test_explicit_kiku_merge_configuration_reaches_mining_contract():
    from saitenka_card.merge import supports_kiku

    from saitenka.app.mining_config import mine_config_from

    assert supports_kiku(mine_config_from({"preset": "Kiku", "merge_adapter": "kiku"}))


def test_app_field_update_does_not_retry_after_response_loss(monkeypatch):
    from saitenka.app.anki import Anki, AnkiError

    class LostResponse:
        def __init__(self):
            self.payloads = []

        def send(self, payload, *, timeout):
            assert timeout > 0
            self.payloads.append(payload)
            raise OSError("response lost")

    anki, transport = Anki(), LostResponse()
    monkeypatch.setattr(anki._client, "transport", transport)
    with pytest.raises(AnkiError, match="response lost"):
        anki.update_note_fields(42, {"Sentence": "new scene"})
    assert transport.payloads == [
        {
            "action": "updateNoteFields",
            "version": 6,
            "params": {"note": {"id": 42, "fields": {"Sentence": "new scene"}}},
        }
    ]
