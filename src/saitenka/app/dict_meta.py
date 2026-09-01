"""What the application decides *about* the dictionary DB's ``term_meta`` rows.

The tables themselves are `saitenka-dict`'s (:class:`saitenka_dict.FreqDict` /
:class:`saitenka_dict.JlptDict`, loaded once for the per-token colouring hot path). What stays here
is policy the package has no business holding: which JLPT dictionary ships with the tool, and where
its asset lives.

The two per-lookup tooltip sources that used to live here are gone. They were a second reader of
``term_meta`` beside the store, with their own matching rules — the shape that put two schemas in
one file (#472) — and the rules genuinely differed: a row keyed by the kana reading was found here
and missed there, so a word could be scored by the blend while its pill stayed blank. Widening the
store's selection (#476) made the two agree, which turned the deletion into a no-op rather than a
swap that has to be argued.
"""

from __future__ import annotations

import zipfile
from datetime import UTC
from typing import TYPE_CHECKING

from saitenka.resources import asset

if TYPE_CHECKING:
    from pathlib import Path

    from saitenka_dict import JlptDict

    from saitenka.app.dictdb import DictionaryDb


def bundled_jlpt_zip() -> Path:
    """Where the bundled JLPT dictionary ships. A function, not an import-time constant: resolving the
    asset root when this module is *imported* is the application's layout decided by a library."""
    return asset("wordlists") / "jlpt.zip"


def ensure_bundled_jlpt(db: DictionaryDb, jlpt_zip: Path | None = None) -> int:
    """Import the bundled JLPT-level dictionary into ``db`` once, returning its ``dict_id``.

    JLPT levels ship with the tool (a small bundled asset, not a user import), so — unlike every other
    dictionary — the runtime imports it on first use. Idempotent: if a dictionary with the bundled
    title already exists it is reused (no rebuild). This is the one build the runtime performs; every
    other dictionary is built only by an explicit ``import`` command.

    ``jlpt_zip`` defaults to the bundled asset; passing one lets a caller supply the archive instead of
    inheriting this module's idea of where assets live."""
    from datetime import datetime

    from saitenka.app.bankreader import _title_of

    archive = jlpt_zip if jlpt_zip is not None else bundled_jlpt_zip()
    with zipfile.ZipFile(archive) as zf:
        title = _title_of(zf, "JLPT")
    found, _missing = db.resolve([title])
    if found:
        return found[0].id
    row = db.import_zip(archive, imported_at=datetime.now(UTC).isoformat(), import_order=-1)
    return row.id


def load_jlpt(db: DictionaryDb) -> JlptDict:
    """The JLPT levels table, importing the bundled dictionary on first use."""
    from saitenka_dict import JlptDict as _JlptDict

    return _JlptDict.from_connection(db.connection(), ensure_bundled_jlpt(db))
