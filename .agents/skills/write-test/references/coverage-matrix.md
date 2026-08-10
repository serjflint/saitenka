# Coverage matrix — subsystem × oracle kind (point-in-time audit)

**Snapshot: 2026-08-10** (branch `test/transform-differential`, atop post-2.4.0 `main`). A *dated audit*,
not a gate — it rots the moment a test lands, so treat a cell as a lead to verify, never ground truth.
Its job is to make the **menu completeness** in [`oracle-catalog.md`](oracle-catalog.md) legible: which
subsystem has which kind, and where the ranked gaps are. Re-scan by reading the tree (the Grow loop can
consume it); do not hand-maintain cell-by-cell.

Cells: **✔** have · **gap** missing & worth it · **n/a** kind doesn't apply · **partial** discipline
under-applied (see gap-type i) · **unbuilt** a kind researched but realized nowhere (gap-type ii).

**Active twin.** The subsystem → applicable-kind mapping has a machine-readable form in
`.agents/hooks/test_kinds.py`; the `test-kinds-advisory.py` commit hook reads it to nudge which non-unit
kinds a diff warrants (AGENTS.md Testing, "test-kind is a decision"). This table is the human rationale,
that map is what fires — keep them in sync.

| Subsystem | metamorphic | golden | property | stateful | stolen-corpus | differential | assembly | perf-gate | humble-obj |
|---|---|---|---|---|---|---|---|---|---|
| tokenize (JP unidic) | ✔ input-equiv | ✔ | ✔ | n/a | ✔ (UAX#14) | **gap** | ✔ (pipeline) | n/a | n/a |
| tokenize (Latin/FR) | ✔ | n/a | gap | n/a | n/a | **gap** (see C) | ✔ (pipeline) | n/a | n/a |
| deinflect · JP | n/a | n/a | ✔ | n/a | ✔ | ✔ | ✔ (pipeline) | n/a | n/a |
| deinflect · FR | n/a | n/a | gap | n/a | **n/a¹** | **✔²** | ✔ (pipeline) | n/a | n/a |
| sub_index | ✔ | n/a | ✔ | n/a | n/a | n/a | n/a | n/a | n/a |
| scoring / fsrs | n/a | n/a | ✔ | n/a | n/a | ✔ (fsrs vectors) | n/a | n/a | n/a |
| render.flow | ✔ sub-pixel | ✔ | ✔ | n/a | n/a | n/a | n/a | n/a | n/a |
| panel / windowed | ✔ agreement | ✔ | partial | ✔ | n/a | n/a | n/a | ✔ | ✔ |
| anki.build_note | n/a | n/a | gap | n/a | n/a | n/a | gap | n/a | ✔ (FakeAnki) |
| dictionary lookup | n/a | n/a | gap | n/a | n/a | n/a | ✔ (pipeline) | n/a | ✔ |
| cache layers | ✔ warm==cold | n/a | ✔ | ✔ | n/a | n/a | n/a | ✔ | ✔ (race) |
| **cli assembly** | n/a | n/a | n/a | n/a | n/a | n/a | **✔³** | n/a | ✔ |
| profiles | ✔ commutativity | n/a | gap | n/a | n/a | n/a | ✔ (pipeline) | n/a | n/a |
| subtitle providers | ✔ input-equiv | n/a | gap | n/a | n/a | n/a | partial | n/a | ✔ |
| sc.walk | n/a | ✔ (sc goldens) | gap | n/a | n/a | n/a | n/a | n/a | n/a |

¹ FR has no upstream conformance suite to steal — *why* the differential exists. ² Closed this session
(`test_transform_differential_corpus.py`). ³ Closed this session (`test_pipeline_oracle.py`). The two ³/²
cells are the audit's proof: both were **gap** before 2026-08-10 and each shipped a bug there.

## The two gap-TYPES (a binary have/gap would read healthier than reality)

- **(i) discipline under-applied.** The catalog mandates a `*_has_teeth` negative control per oracle;
  only **4 files** carry one (`test_tooltip_statemachine`, `test_subtitle_metamorphic`, `test_replay_sessions`,
  and the new `test_transform_differential_corpus`). The rest lean on `poe test-live` (negate-each-assert)
  to prove load-bearing — adequate in the coding loop, but not a *permanent* control. Cells above are
  `partial` where the oracle exists but ships no committed teeth.
- **(ii) researched-but-never-realized.** The integration/e2e research named a **coverage-CONTEXTS
  dead-config detector** (assert every config context is exercised); it is realized in **0 files**. This is
  a whole kind on the research menu with no in-tree instance — the strongest form of "the menu drifted from
  reality." Left as `unbuilt`, ROI-deferred (below), not silently dropped.

## Ranked gap backlog (leads, not a mandate — most stay ROI-deferred)

1. **deinflect-FR / tokenize-FR differential breadth** — the differential corpus (Workstream A) covers the
   seeded conjugation classes; broaden seeds as FR usage grows. *Low, demand-gated.*
2. **property-based on the lookup/anki/profile pure cores** — several `gap` cells share one cause: no
   Hypothesis strategy over the note-build / lookup / profile-merge invariants. *Medium.*
3. **has-teeth backfill** — promote the highest-value oracles from `test-live`-proven to a committed
   `*_has_teeth`. *Low, opportunistic (touch-it-migrate-it).*
4. **coverage-CONTEXTS dead-config detector** — build once, apply to the config matrix. *Medium, deferred.*
5. **13-language transform sweep** — rides the same `gen_transform_differential.mjs` (add `LANGS` rows);
   explicitly **YAGNI** until a real second non-FR language ships. *Deferred.*

## A/B/C re-validation against this matrix

- **A (differential generator + FR corpus)** lands in the *differential* column as a **catalogued** instance
  (canonical mechanism registered in the catalog), not a rediscovery — the audit's whole purpose.
- **B (FR seed corpus)** is A's input; the two rule-data live-bug pins sit in deinflect-FR/differential, the
  three preprocessor pins (decap/elision) in tokenize-FR/assembly (pipeline oracle) — the split the matrix
  makes visible.
- **C (preprocessor provenance)** targets the tokenize-FR **gap** under *differential*: the tokenizer
  hand-does decap/elision, ungrounded in Yomitan's descriptor. C ties it to upstream; no new column.
