"""Payload identities shared with the opt-in mpv frame probe; never emit subtitle text."""

from __future__ import annotations


def command_identity(args: tuple) -> dict:
    command = str(args[0]) if args else "unknown"
    result: dict = {"command": command}
    index = {"osd-overlay": 3, "osd-overlay-timed": 4}.get(command)
    if index is not None and len(args) > index and isinstance(args[index], str):
        result.update(overlay_id=str(args[1]), payload_hash=payload_hash(args[index]))
    return result


def payload_hash(payload: str) -> str:
    """Match the diagnostic producer's FNV-1a hash, including UTF-8 encoding."""
    value = 0xCBF29CE484222325
    for byte in payload.encode():
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"
