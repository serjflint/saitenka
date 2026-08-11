"""Exact-byte restoration for tools that temporarily rewrite tracked files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class RestorationError(RuntimeError):
    """A temporary edit could not be restored exactly."""


@dataclass(frozen=True)
class ByteSnapshot:
    path: Path
    data: bytes

    @classmethod
    def capture(cls, path: Path) -> ByteSnapshot:
        return cls(path, path.read_bytes())

    def restore(self) -> None:
        self.path.write_bytes(self.data)
        if self.path.read_bytes() != self.data:
            raise RestorationError(f"failed to restore exact bytes: {self.path}")
