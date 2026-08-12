"""Configuration-backed defaults shared by command signatures."""

from saitenka.app.config import load_config
from saitenka.app.launch.run import default_mine_target

_cfg = load_config()
_mine_cfg = _cfg.get("mine", {}) if isinstance(_cfg.get("mine"), dict) else {}


def resolve_mine_model(mine_cfg: dict) -> str:
    return default_mine_target(mine_cfg)[1]


_MINE_MODEL_DEFAULT = resolve_mine_model(_mine_cfg)
