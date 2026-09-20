"""Payload identities shared with the opt-in mpv frame probe; never emit subtitle text."""

from __future__ import annotations


def command_identity(args: tuple) -> dict:
    command = str(args[0]) if args else "unknown"
    result: dict = {"command": command}
    index = {"osd-overlay": 3, "osd-overlay-timed": 4}.get(command)
    if index is not None and len(args) > index and isinstance(args[index], str):
        value = 14695981039346656037
        for byte in args[index].encode():
            value = ((value ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        result.update(overlay_id=str(args[1]), payload_hash=f"{value:016x}")
    return result
