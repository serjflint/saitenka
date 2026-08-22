# Architecture-review loop — process spec

Terse and agent-facing. The *how to judge* is the **`architecture-review` skill**
(`.agents/skills/architecture-review/`); this file owns only the parts that must survive between
runs, because a review that leaves nothing behind is a one-shot opinion and the skill's whole value
claim is the **delta between two runs**.

## Artifacts

| path | what | tracked |
| --- | --- | --- |
| `runs/YYYY-MM-DD-<scope>.md` | one run's report, verbatim from the reviewer | yes — the delta is diffing two of these |
| `census.json` | the durable claim inventory: what each module asserts, and whether anything enforces it | yes — ratcheted |
| `scripts/smoke.sh` | rot guard | yes |

Scratch during a run goes to `vibe/` as usual. Nothing in `vibe/` survives — that is the whole reason
this directory exists. A census written there is a census the next run cannot extend.

## The loop

    census.json ──argued rows──> isolated reviewer ──"could not verify"──> census.json
                                        │
                                        └── runs/<date>-<scope>.md

1. **Before the run** — read `census.json`. Its `argued` rows in the run's scope are the agenda; hand
   them over as *claims to attack*, never as findings to confirm.
2. **The run** — the skill. Report goes to `runs/YYYY-MM-DD-<scope>.md`, verbatim. Do not summarize
   it into the ledger; a summary is where the discriminators die.
3. **After the run** — write back:
   - every **"could not verify"** row becomes a census row with `status: "argued"` and its
     `settles` field filled from the report's "what would settle it" column;
   - every claim the reviewer **falsified** flips to `argued` (or disappears, if the claim was
     deleted with the defect);
   - every claim it **settled** flips to `gated` or `tested`, naming the task or test.

## `census.json`

    {
      "censused": ["src/saitenka/render/banded.py"],   // modules that have been looked at
      "claims": [
        {
          "id": "B1",
          "module": "src/saitenka/render/banded.py",
          "claim": "_render_band touches no shared state; the caller stores under the lock",
          "class": 1,                                   // references/claim-classes.md
          "status": "gated",                            // gated | tested | argued
          "evidence": "sgconfig/rules/no-raster-under-panel-lock.yml (poe invariants, in all)",
          "settles": null                               // set when status is "argued"
        }
      ]
    }

**`censused` is not decoration.** `argued = 0` and *never looked at* must not read the same — that is
class 7 of the skill's own taxonomy, applied to the skill. A module absent from `censused` has status
**unknown**, and a report that reads "no argued claims" without saying over what denominator is
overclaiming.

**Scope, so the census stays a signal.** Do not census every docstring. Modules that own a **shared
resource**: a lock, a thread, a cache, a socket, a file another process maps, a process lifetime.

**Ratchet direction.** `argued` in a censused module may fall freely and rises only with a stated
reason, the way `poe host-mass` is blessed. There is no `poe` task enforcing this yet — it is a
review discipline until one run demonstrates it needs to be a gate.

## Cadence

Whenever a large migration lands, and otherwise on a schedule so drift is caught by calendar rather
than by accident. Two runs is the minimum for the loop to have produced anything the skill alone
would not.
