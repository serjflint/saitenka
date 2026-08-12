"""Windows overlapped named-pipe state machine, exercised without a Windows kernel."""

import threading

from saitenka.mpvio.transport import NamedPipeTransport


class FakeOverlapped:
    def __init__(self, *, buffer: bytes = b"", count: int = 0):
        self._buffer = buffer
        self._count = count
        self._error = 0
        self._done = threading.Event()
        if buffer or count:
            self._done.set()

    def complete_read(self, data: bytes) -> None:
        self._buffer = data
        self._count = len(data)
        self._done.set()

    def GetOverlappedResult(self, _wait):  # noqa: N802  # mirrors _winapi's API
        assert self._done.wait(timeout=1)
        return self._count, self._error

    def getbuffer(self) -> bytes:
        return self._buffer

    def cancel(self) -> None:
        self._error = FakeWinApi.ERROR_OPERATION_ABORTED
        self._done.set()


class FakeWinApi:
    ERROR_IO_PENDING = 997
    ERROR_BROKEN_PIPE = 109
    ERROR_OPERATION_ABORTED = 995
    ERROR_MORE_DATA = 234
    ERROR_NO_DATA = 232

    def __init__(self, *, write_limit: int | None = None):
        self.read_started = threading.Event()
        self.pending_read: FakeOverlapped | None = None
        self.written = bytearray()
        self.write_limit = write_limit
        self.closed = False

    def ReadFile(self, _handle, _size, *, overlapped):  # noqa: N802  # mirrors _winapi's API
        assert overlapped is True
        self.pending_read = FakeOverlapped()
        self.read_started.set()
        return self.pending_read, self.ERROR_IO_PENDING

    def WriteFile(self, _handle, data, *, overlapped):  # noqa: N802  # mirrors _winapi's API
        assert overlapped is True
        count = min(len(data), self.write_limit or len(data))
        self.written.extend(data[:count])
        operation = FakeOverlapped(count=count)
        if self.pending_read is not None and not self.pending_read._done.is_set():
            self.pending_read.complete_read(b"reply\n")
        return operation, 0

    def CloseHandle(self, _handle) -> None:  # noqa: N802  # mirrors _winapi's API
        self.closed = True


def test_blocked_overlapped_read_does_not_block_write():
    api = FakeWinApi()
    transport = NamedPipeTransport(1, api)
    received = []
    thread = threading.Thread(target=lambda: received.append(transport.read(65536)))
    thread.start()
    assert api.read_started.wait(timeout=1)

    transport.write(b"command\n")

    thread.join(timeout=1)
    assert bytes(api.written) == b"command\n" and received == [b"reply\n"]


def test_overlapped_write_retries_partial_completion():
    api = FakeWinApi(write_limit=2)
    transport = NamedPipeTransport(1, api)

    transport.write(b"abcdef")

    assert bytes(api.written) == b"abcdef"


def test_close_cancels_pending_read():
    api = FakeWinApi()
    transport = NamedPipeTransport(1, api)
    received = []
    thread = threading.Thread(target=lambda: received.append(transport.read(65536)))
    thread.start()
    assert api.read_started.wait(timeout=1)

    transport.close()

    thread.join(timeout=1)
    assert received == [b""] and api.closed is True
