"""saitenka-overlay-deinflect — Japanese deinflection chain, derived from Yomitan (GPL-3.0).

Optional add-on for saitenka-overlay: when installed, the overlay shows Yomitan's inflection chain
(🧩 ``-て « -いる « -た``) under a headword. The Apache-2.0 core runs without it (no chain shown).
See ``LICENSE`` (GPL-3.0-or-later) and ``NOTICE`` (Yomitan attribution).
"""

from saitenka_deinflect.engine import Deinflection, deinflect, inflection_chain

__all__ = ["Deinflection", "deinflect", "inflection_chain"]
