from __future__ import annotations

from typing import Any


def normalize_glossary(value: Any, media: dict[str, bytes]) -> Any:
    if isinstance(value, list):
        return [normalize_glossary(item, media) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_glossary(item, media) for key, item in value.items()}
    path = result.get("path")
    is_image = result.get("type") == "image" or result.get("tag") == "img"
    if not is_image or not isinstance(path, str) or path not in media:
        return result
    size = image_size(media[path])
    if size is None:
        return result
    preferred_width = result.get("width")
    preferred_height = result.get("height")
    result["width"], result["height"] = size
    if isinstance(preferred_width, (int, float)) and not isinstance(preferred_width, bool):
        result["preferredWidth"] = preferred_width
    if isinstance(preferred_height, (int, float)) and not isinstance(preferred_height, bool):
        result["preferredHeight"] = preferred_height
    return result


def image_size(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return _gif_size(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    return None


def _gif_size(data: bytes) -> tuple[int, int]:
    fallback = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if len(data) < 13:
        return fallback
    packed = data[10]
    offset = 13 + (3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0)
    while offset < len(data):
        marker = data[offset]
        if marker == 0x2C and offset + 9 < len(data):
            return (
                int.from_bytes(data[offset + 5 : offset + 7], "little"),
                int.from_bytes(data[offset + 7 : offset + 9], "little"),
            )
        if marker != 0x21 or offset + 2 >= len(data):
            break
        offset += 2
        while offset < len(data):
            block_size = data[offset]
            offset += 1
            if block_size == 0:
                break
            offset += block_size
    return fallback


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    offset = 2
    start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            return None
        if marker in start_of_frame and length >= 7:
            return (
                int.from_bytes(data[offset + 5 : offset + 7], "big"),
                int.from_bytes(data[offset + 3 : offset + 5], "big"),
            )
        offset += length
    return None
