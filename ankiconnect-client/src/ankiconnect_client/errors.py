class AnkiConnectError(RuntimeError):
    """AnkiConnect returned an application error."""


class AnkiConnectProtocolError(AnkiConnectError):
    """AnkiConnect returned a malformed response."""


class AnkiConnectUnavailable(AnkiConnectError):
    """The local AnkiConnect endpoint could not be reached."""
