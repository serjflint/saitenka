"""Keep scratch paths alive until workers and the player stop using them."""

from __future__ import annotations

import logging
import shutil
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

log = logging.getLogger(__name__)


class ScratchArtifacts:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._users = 0
        self._closed = False

    def retain(self) -> Callable[[], None]:
        with self._lock:
            if self._closed:
                raise OSError("mining scratch retired")
            self._users += 1
        return self.release

    def release(self) -> None:
        with self._lock:
            self._users -= 1
            remove = self._closed and self._users == 0
        if remove:
            shutil.rmtree(self.path, ignore_errors=True)

    def retire(self, delegate: Callable[[str], bool] | None = None) -> None:
        with self._lock:
            self._closed = True
            pending = self._users > 0
        if pending:
            log.warning("retaining mining scratch for unsettled media work: %s", self.path)
        elif delegate is None or not delegate(str(self.path)):
            shutil.rmtree(self.path, ignore_errors=True)
