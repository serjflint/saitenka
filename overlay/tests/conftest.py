"""Make ``tests/util.py`` importable as ``util``, put ``src/`` on the path, and keep the dict
cache HERMETIC: tests must never write into the user's real ~/.cache/saitenka-overlay/dicts."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import overlay.app.dictionary as _dictionary  # noqa: E402

_dictionary._REAL_CACHE_DIR = _dictionary.CACHE_DIR  # test_compare opts back into the real one
_TEST_CACHE = Path(tempfile.mkdtemp(prefix="saitenka-test-dicts-"))
_dictionary.CACHE_DIR = _TEST_CACHE
