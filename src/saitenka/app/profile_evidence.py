"""Text-free requested versus committed reading-profile evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.render_evidence import EvidenceRegistry
from saitenka.app.report_schema import count

if TYPE_CHECKING:
    from saitenka.app.profiles import Profile

registry = EvidenceRegistry()
_LANGUAGES = frozenset(
    {"jp", "ja", "en", "fr", "de", "es", "it", "pt", "ru", "uk", "el", "pl", "nl", "sv", "zh", "ko"}
)


def _fields(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    values = {}
    for key, allowed in (
        ("main_language", _LANGUAGES),
        ("second_language", _LANGUAGES),
        ("tokenizer", {"unidic", "latin"}),
    ):
        value = raw.get(key)
        values[key] = value if isinstance(value, str) and value in allowed else "unknown"
    return values


class ProfileEvidence:
    def __init__(self, profile: Profile) -> None:
        self._registry = registry
        self.owner = self._registry.allocate()
        self._state: dict = {
            "owner": self.owner,
            "revision": 0,
            "requested": {},
            "applied": {},
            "outcome": "constructed",
        }
        self.request(profile)
        self.finish("constructed")

    def request(self, profile: Profile) -> None:
        self._state["revision"] += 1
        self._state["requested"] = _fields(
            {
                "main_language": profile.langs.main,
                "second_language": profile.langs.second,
                "tokenizer": profile.tokenizer,
            }
        )
        self._state["outcome"] = "pending"
        self._registry.update(self.owner, self._state)

    def finish(self, outcome: str) -> None:
        self._state["outcome"] = outcome
        if outcome == "constructed":
            self._state["applied"] = self._state["requested"]
        self._registry.update(self.owner, self._state)

    def applied(self, profile: Profile, tokenizer: str) -> None:
        self._state["applied"] = _fields(
            {
                "main_language": profile.langs.main,
                "second_language": profile.langs.second,
                "tokenizer": tokenizer,
            }
        )
        self._registry.update(self.owner, self._state)


def safe_snapshot(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        return {"status": "unsupported-schema"}
    owners = raw.get("owners")
    if not isinstance(owners, list) or len(owners) > 4:
        return {"status": "invalid"}
    rows, seen = [], set()
    for row in owners:
        if not isinstance(row, dict) or not count(row.get("owner")) or row["owner"] in seen:
            return {"status": "invalid"}
        seen.add(row["owner"])
        outcome = row.get("outcome")
        rows.append(
            {
                "owner": row["owner"],
                "revision": count(row.get("revision")),
                "requested": _fields(row.get("requested")),
                "applied": _fields(row.get("applied")),
                "outcome": outcome
                if isinstance(outcome, str)
                and outcome in {"constructed", "pending", "committed", "degraded", "rejected"}
                else "unknown",
            }
        )
    return {
        "status": "collected" if rows else "unknown",
        "scope": "profile owner; language/tokenizer commit, not font resolution or rendered pixels",
        "origin": "producer-profile-owner",
        "owners": rows,
        "owners_evicted": count(raw.get("owners_evicted")),
    }
