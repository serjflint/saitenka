"""AnkiConnect endpoint is user-configurable via [anki] (url / host+port / api_key)."""

from __future__ import annotations

import json

from overlay.app import anki


def test_resolve_anki_defaults_to_stock():
    assert anki.resolve_anki({}) == ("http://127.0.0.1:8765", None)


def test_resolve_anki_from_url_and_key():
    cfg = {"anki": {"url": "http://127.0.0.1:9999", "api_key": "SECRET"}}
    assert anki.resolve_anki(cfg) == ("http://127.0.0.1:9999", "SECRET")


def test_resolve_anki_from_host_and_port():
    cfg = {"anki": {"host": "127.0.0.1", "port": 8766}}
    url, key = anki.resolve_anki(cfg)
    assert url == "http://127.0.0.1:8766" and key is None


def test_anki_client_reads_config_and_injects_key(monkeypatch, tmp_path):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('[anki]\nurl = "http://127.0.0.1:9001"\napi_key = "K"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    sent = {}

    class _Resp:
        def read(self):
            return json.dumps({"result": 6}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=0):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(anki.urllib.request, "urlopen", _fake_urlopen)
    client = anki.Anki()
    assert client.host == "http://127.0.0.1:9001"
    client._call("version")
    assert sent["url"] == "http://127.0.0.1:9001"
    assert sent["body"]["key"] == "K"  # apiKey injected into the request body
