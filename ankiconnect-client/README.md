# ankiconnect-client

A small synchronous AnkiConnect client. It owns JSON-RPC framing, response validation,
transport failures, retries, and typed convenience methods; applications own launch policy,
configuration, telemetry, and note construction.

`AnkiConnectClient` accepts an injectable `Transport` and optional `Observer`, exposes the raw `call`
and batched `multi` seams, and supplies typed helpers for the note/media operations Saitenka uses.
