---
name: write-test
description: >-
  Author a new pytest test for the Saitenka overlay the house way — pick the test
  tier, decide real-vs-fake at each boundary, construct-vs-fixture, and the assertion
  ladder, then write it against the real fakes. Use when adding or rewriting a test
  under overlay/tests: "write a test for", "add a test", "cover this", "test build_note
  / the IPC path / mining / the tokenizer". Encodes the AGENTS.md "Testing" invariants
  as a step procedure and points at the canonical example tests. NOT for running the
  suite or the pre-push gate (use `poe test` / the dev-gate skill); NOT for
  mutation/fuzz/crosshair adequacy (AGENTS.md "Fuzzing & symbolic checks"); NOT for
  other repos.
metadata:
  project: saitenka
---

# write-test

Procedure for writing a **new** test in `overlay/tests/`. The *invariants* live in
`saitenka/AGENTS.md` → "## Testing"; this skill is the *how*. Match the surrounding
file's style — read a sibling test first.

## Decision tree (walk it before typing)

1. **What am I testing?**
   - Pure core (`sub_index`, `tokenize`, `scoring`, `render.flow`, `anki.build_note`,
     `fsrs`, deinflect behind the chokepoint) → fast default tier, construct real objects.
   - Controller / orchestration talking to a port → `integration`, drive the `Fake*`.
   - Real `mpv` / display → `live` only (`SAITENKA_LIVE=1`, `poe smoke-live`).
   - Trivial glue / pure delegation → **don't test** (see below).
2. **Does it cross a subprocess / socket / display?**
   - Yes → add the marker (`integration` or `live`) **and** `@pytest.mark.timeout(5)`
     (30 for `live`). Use `FakeMpvServer` / `FakeTransport` / `FakeAnki` — never real mpv.
   - No → default tier, no marker.
3. **Fake or real?** Construct the real collaborator. For an out-of-process boundary
   (mpv, AnkiConnect) use the existing `Fake*`. `monkeypatch.setattr` to inject a fake or
   repoint an extracted seam is correct here; do **not** reach for `unittest.mock` to fake
   internal behaviour.
4. **Fixture or construct?** Construct in the test body (or a tiny local `Fake*`). Reserve
   `@pytest.fixture` for resource lifecycle/override/parametrised clients. Never
   `scope="module"|"session"` — shared mutable state races under free-threading.
5. **Assertion tier?** plain `assert` → `dirty_equals` (`IsPartialDict`, `IsStr(regex=…)`,
   `Contains`) for payloads with keys you don't care about or volatile ids/timestamps → an
   existing **golden** for large structured output. For a **rendering / cache / config /
   interaction** behaviour, assert a metamorphic **oracle** (agreement round-trip, warm==cold,
   scale-invariance, feature-toggle consistency), never raw pixels — they're platform-dependent;
   when the gap is a *family* not a point, a Hypothesis `@given` property beats N examples. Menu +
   canonical examples: [`references/oracle-catalog.md`](references/oracle-catalog.md).
6. **One act, scenario name.** One trigger + one assertion chain; split multi-act tests.
7. **Extend or add?** Prefer appending a `PROFILES` row (`tests/util.py`), a `parametrize` case, or
   an `@example` to an existing test over a near-duplicate new file — one row, every property inherits
   the corner. A *new* test is for a genuinely new scenario. (Adding an assertion is fine; *changing*
   an existing one is the Sharpen loop's job, not yours.)

## Recipes (canonical tests in-tree — read them, don't reinvent)

- **Pure core** → `tests/test_mining.py::test_card_data_from_token`: construct via
  `tokenize(...)`, `card_for(...)`, plain `assert` on observable fields.
- **Payload with volatile/uninteresting keys** → prefer `dirty_equals` over N sequential
  asserts:

  ```python
  from dirty_equals import Contains, IsPartialDict, IsStr

  note = build_note(MineConfig(), card_for(tok), "本を<b>読む</b>", picture="p.jpg")
  assert note == IsPartialDict(
      modelName="Lapis",
      fields=IsPartialDict(Expression="読む", MiscInfo=IsStr(regex=r"ep\d+ · \d+:\d+")),
      tags=Contains("saitenka"),
  )
  ```

- **IPC / socket boundary** → `tests/test_ipc_chaos.py`: real `socket.socketpair()`, faults
  injected through the `Transport` port via `_FlakyWriteTransport` (deterministic, not raced).
  Carries a timeout.
- **Free-threading** → not a per-test assert. The suite runs free-threaded under
  `poe test-ft` (`PYTHON_GIL=0`); the only GIL assert lives in `tests/test_ft_gil.py`, gated
  on the FT build. Don't add `sys._is_gil_enabled()` asserts to ordinary tests — cosmic-ray
  re-enables the GIL in its harness and they'd flake.

## When NOT to write a test

- Pure refactor with no observable-behaviour change (the suite is the net).
- Trivial getter / one-line delegation.
- A test that would only assert a private attribute or a mock call-count.
- Chasing a coverage line on thin I/O glue (glue is excluded from mutation for the same
  reason — it floods equivalent mutants).

## Verify

`uv run poe test` (fast tier) green, and if the change touches free-threading,
`uv run poe test-ft`. Coverage floor is 85% (`poe cov`).

Prove a new oracle/assert is load-bearing, not vacuous:
`uv run poe test-live tests/test_x.py --test <name>` — it negates each assert in turn, and every one
must flip the test red (a `pytest.raises`/`warns` block counts as live). This is arm-2 of the Grow gate,
exposed for the coding loop.
