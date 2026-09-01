"""Pure construction of mining configuration from the application config table."""

from __future__ import annotations

import os
from pathlib import Path

from saitenka_card import AnimatedClip, MineConfig

from saitenka.app.config import WordAudioOptions


def mine_config_from(config: dict) -> MineConfig:
    """Build the run/attach mining configuration from a ``[mine]`` table."""
    preset = config.get("preset")
    base = MineConfig.from_preset(str(preset)) if preset else MineConfig()
    raw_fields = config.get("fields")
    fields = dict(raw_fields) if isinstance(raw_fields, dict) and raw_fields else base.fields
    raw_format = config.get("card_format")
    card_format = dict(raw_format) if isinstance(raw_format, dict) else {}
    word_audio_defaults = WordAudioOptions()
    word_audio_pack = None
    if bool(config.get("word_audio_enabled", word_audio_defaults.word_audio_enabled)):
        raw_pack = config.get("word_audio_pack_dir", word_audio_defaults.word_audio_pack_dir)
        if raw_pack:
            word_audio_pack = Path(os.path.expandvars(str(Path(str(raw_pack)).expanduser())))
    return MineConfig(
        deck=config.get("deck", base.deck),
        model=config.get("model", base.model),
        normalize_audio=bool(config.get("normalize_audio")),
        animated=AnimatedClip(
            enabled=bool(config.get("animated_screenshot")),
            height=int(config.get("animated_height", 480)),
            fps=int(config.get("animated_fps", 12)),
            quality=int(config.get("animated_quality", 75)),
            max_secs=float(config.get("animated_max_secs", 4.0)),
            fmt=str(config.get("animated_format", "webp")).lower(),
        ),
        card_kind=str(config.get("card_kind", base.card_kind)),
        fields=fields,
        card_format=card_format,
        word_audio_pack=word_audio_pack,
        word_audio_field=str(config.get("word_audio_field", word_audio_defaults.word_audio_field)),
    )
