from __future__ import annotations

import os
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_support_spec = spec_from_file_location(
    "libasslite_bundle_build_support",
    Path(__file__).with_name("build_support.py"),
)
if _support_spec is None or _support_spec.loader is None:
    raise RuntimeError("could not load bundle build validation")
_support = module_from_spec(_support_spec)
_support_spec.loader.exec_module(_support)
validate_payload = _support.validate_payload


class CustomBuildHook(BuildHookInterface):
    def initialize(self, _version: str, build_data: dict[str, object]) -> None:
        if self.target_name != "wheel":
            return
        validate_payload(Path(self.root))
        tag = os.environ.get("LIBASSLITE_BUNDLE_WHEEL_TAG")
        if not tag:
            raise RuntimeError("LIBASSLITE_BUNDLE_WHEEL_TAG is required for bundle wheels")
        build_data["pure_python"] = False
        build_data["tag"] = tag
