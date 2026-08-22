"""Planted controls for the cluster map — the two things it is trusted for.

Its answer decides a migration's shape, and both halves have a cheap wrong version. Classification
by *name* would report seven names over one owner slice as seven facts, which is the number that
makes a port look too big to be worth building. And a call-site list gathered as text sweeps in a
same-named parameter and a mention in a comment, which is what the greps it replaces did.
"""

from __future__ import annotations

import cluster_map


def _classify(source, tmp_path, monkeypatch) -> dict[str, cluster_map.Member]:
    controller = tmp_path / "controller.py"
    controller.write_text(source, encoding="utf-8")
    monkeypatch.setattr(cluster_map, "CONTROLLER", controller)
    return cluster_map.classify_host()


def test_two_names_over_one_owner_slice_are_one_fact(tmp_path, monkeypatch):
    """`jp_sid` and `en_sid` are one port with two fields, not two members to pass separately."""
    members = _classify(
        "class Reader:\n"
        "    @property\n"
        "    def jp_sid(self):\n"
        "        return self._tracks.current.jp_sid\n"
        "    @property\n"
        "    def en_sid(self):\n"
        "        return self._tracks.current.en_sid\n",
        tmp_path,
        monkeypatch,
    )

    assert {m.fact.split(".")[0] for m in members.values()} == {"_tracks"}


def test_a_delegated_descriptor_resolves_to_the_context_field_it_forwards_to(tmp_path, monkeypatch):
    """The flat-name compatibility layer: its own name says nothing about what it is."""
    members = _classify(
        'class Reader:\n    _sub_index = Delegated[CueIndex | None]("episode", "sub_index")\n',
        tmp_path,
        monkeypatch,
    )

    assert members["_sub_index"].kind == "delegated"
    assert members["_sub_index"].fact == "episode.sub_index"


def test_a_property_that_does_more_than_read_is_not_reported_as_a_slice(tmp_path, monkeypatch):
    """An unresolved member is a prompt to look, not a fact to design against — so a body the tool
    cannot reduce must not be flattened into the owner it happens to mention."""
    members = _classify(
        "class Reader:\n"
        "    @property\n"
        "    def jp_sid(self):\n"
        "        if self._paused:\n"
        "            return None\n"
        "        return self._tracks.current.jp_sid\n",
        tmp_path,
        monkeypatch,
    )

    assert members["jp_sid"].kind == "derived"


def test_a_same_named_parameter_is_not_a_call_site(tmp_path, monkeypatch):
    """The worklist is an attribute match. A text sweep reports this file and the codemod edits it."""
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "a.py").write_text("def render(tip_width):\n    return tip_width\n", encoding="utf-8")
    (tree / "b.py").write_text("def go(r):\n    return r.tip_width\n", encoding="utf-8")
    monkeypatch.setattr(cluster_map, "ROOT", tmp_path)
    monkeypatch.setattr(cluster_map, "_SWEPT", ("src",))

    assert set(cluster_map.sites("tip_width")) == {"src/b.py"}


def test_the_live_classification_is_not_vacuous():
    """A map that resolves nothing answers every shape question with `?` and passes silently."""
    members = cluster_map.classify_host()

    assert len(members) > 0
    assert {m.kind for m in members.values()} > {"method"}
