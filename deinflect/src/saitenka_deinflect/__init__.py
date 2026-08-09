"""saitenka-deinflect — multi-language deinflection chains, derived from Yomitan (GPL-3.0).

Optional add-on for saitenka: when installed, the overlay shows Yomitan's inflection chain
(🧩 ``-て « -いる « -た``) under a headword — Japanese and French today. The Apache-2.0 core runs
without it (no chain shown). See ``LICENSE`` (GPL-3.0-or-later) and ``NOTICE`` (Yomitan attribution).
"""

from saitenka_deinflect.engine import (
    Deinflection,
    Deinflector,
    condition_flags,
    conditions_match,
    deinflect,
    get_deinflector,
    inflection_chain,
)

__all__ = [
    "Deinflection",
    "Deinflector",
    "condition_flags",
    "conditions_match",
    "deinflect",
    "get_deinflector",
    "inflection_chain",
]
