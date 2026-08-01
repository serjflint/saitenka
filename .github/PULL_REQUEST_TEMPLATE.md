<!--
Thanks for the pull request! Keep the body lean and why-focused (see AGENTS.md + the
pr-ticket-describe skill) — a one-line change gets one sentence. Fill in what applies; delete
what doesn't.
-->

## Summary

<!-- 1–3 bullets: the resulting behavior. -->

## Why

<!-- The concrete before/after, and why the existing seam/config/workaround is insufficient. -->

## AI Policy

- [ ] I have read the [AI Policy](AI_POLICY.md), stripped any agent-attribution trailers
  (`Co-Authored-By:` / "Generated with …"), and manually reviewed and understood the final result

## Checklist

- [ ] No unrelated changes (stray patches, config files, reformatting)
- [ ] `uv run poe all` passes locally (the 14-task pre-push gate)
- [ ] Tests added or updated for the change (see the `write-test` skill)
- [ ] `CHANGELOG.md` updated for user-visible changes (`uv run poe changelog`, hand-reviewed)
- [ ] Docs updated and `uv run poe docs` builds, if docs/README/ARCHITECTURE changed
- [ ] Tokenizer/rendering goldens re-blessed deliberately, if a `unidic-lite`/Pillow/font bump moved them

## Related issues

<!--
- Closes #issue-number
- Refs #issue-number
-->
