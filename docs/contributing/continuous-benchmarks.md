# Continuous benchmark portfolio

This is the implementation plan and operating contract for Saitenka's hosted performance history.
The dated measurements and profiler evidence remain in
[`BENCHMARKS.md`](https://github.com/serjflint/saitenka/blob/main/BENCHMARKS.md); this page defines what
CI measures and how a result becomes a chart point.

## Goal

Detect large performance regressions in the user-visible path without pretending that shared GitHub
runners are stable laboratory hardware. The existing local `poe perf-check` remains the deterministic
hard guard. Hosted results are advisory evidence for review and diagnosis.

Each published point is built from **at least three independent benchmark processes**. CI runs them as
separate matrix jobs, then one aggregation job validates the metric set and publishes the median. The
chart also records the replica range and median absolute deviation (MAD); tail/jank metrics additionally
show the worst replica. Samples inside one process are useful for a latency distribution, but they do
not count as independent replicas.

Only the aggregator may write `gh-pages`. Intermediate JSON artifacts expire after one day. All
benchmark writers share one concurrency group, so the render and live-player histories cannot race.

## Portfolio

| Tier | Trigger | Surface | Why it belongs there |
| --- | --- | --- | --- |
| Core | pull request, push to `main`, manual | deterministic synthetic panel layout and viewport rendering | Primary hover-to-paint CPU path; dictionary- and display-independent |
| Core | pull request, push to `main`, manual | subtitle parse, cue indexing, and tokenization | Bounds the scanning work before dictionary lookup |
| Core | pull request, push to `main`, manual | generated Yomitan archive import and exact SQLite lookup | Exercises the offline-cache build and decoded-entry query boundary without private dictionaries |
| Core | pull request, push to `main`, manual | click/store actions against temporary SQLite and fake IPC | Covers synchronous mining/bookkeeping work without Anki or network noise |
| Lifecycle | weekly, release tag, manual | bounded-cache stress | Longer-lived eviction and memory behavior is useful, but too noisy and costly for every PR |
| Live | weekly, release tag, manual | real mpv/Xvfb frame counters plus hover and scroll latency | Only this tier sees compositor and player interaction |
| Local | developer-invoked | installed-dictionary pathological/vocabulary, render-cache prewarm, trace replay, profiler runs | Requires private corpora, true disk-cold state, or diagnostic interpretation |

The continuous core suite publishes render, subtitle, generated-dictionary, and click/store metrics.
The weekly suite publishes bounded-cache lifecycle and real-player metrics. The live charts retain
mpv's decoder-late and VO/compositor-delayed counters as playback sentinels and add non-zero latency for two directly observable paths:
opening a tooltip and applying four scroll updates. Dwell-based nested/switch/dismiss steps remain in
the frame-counter workload but are not mislabeled as latency measurements. The result schema and
workflow are shared so a future synthetic prefetch keep-ahead corpus can be added without inventing
another statistics or storage path.

## Statistical contract

One replica emits github-action-benchmark's `customSmallerIsBetter` JSON. Aggregation requires the same
metric names and units in every replica and rejects fewer than three files, duplicate names, non-finite
values, and schema drift. For each metric it publishes:

- the median replica value as the chart value;
- the replica minimum, maximum, and MAD in the `range` annotation;
- the worst replica in the annotation for tail/jank metrics.

No wall-clock threshold blocks a pull request. `github-action-benchmark` performs the historical
comparison, while one short bot comment reports the current medians and replica spread. The comment is
skipped for fork pull requests because their read-only token cannot update the discussion. A human uses
the range, adjacent commits, profiler output, and the local hard guard to decide whether movement is
causal. Daily runs are deliberately absent: without a code change they mostly measure runner drift and
spend storage/compute without increasing release confidence.

### Selected-commit backfill

The manual Perf and E2E workflows accept one full 40-character `revision` SHA. A preflight job rejects
mutable or abbreviated refs. Measurement jobs install the selected revision's production package and
dependencies, but load the measurement scripts separately from the workflow revision. The publisher
labels the point with the selected SHA, so adding a harness later does not prevent measuring compatible
earlier code or make the point link to the dispatch commit. E2E backfills run only the live and lifecycle
replicas; the unrelated cross-platform and installer matrices stay skipped.

This is a fixed-workload comparison, not unlimited backwards compatibility. The selected production
revision must implement the API consumed by the current harness, and the core metric names and units
must match the committed series manifest. The comparable full-portfolio epoch begins at the consolidated
`src/saitenka` layout; older `overlay`-namespace releases retain their original render history rather
than being mixed into the new charts through a behavior-changing adapter.

Backfill several commits by dispatching both workflows once per SHA. Manual Perf runs queue instead of
replacing a pending result. E2E measurement runs use the immutable revision in their concurrency key,
so different revisions may measure concurrently without canceling one another:

```console
gh workflow run perf.yml --ref main -f revision=<full-commit-sha>
gh workflow run e2e.yml --ref main -f revision=<full-commit-sha>
```

Replica workflows may run concurrently, but their `gh-pages` publishers are serialized and retain up
to 100 pending writes through GitHub's `queue: max` concurrency policy.

Backfills append in publication order. The tooltip and link retain the selected commit's identity and
original timestamp; the chart does not reorder already-published points chronologically.

## Rollout

1. Introduce and test the validator/aggregator independently of workflow YAML.
2. Convert the synthetic history workflow to a three-job replica matrix plus one PR comparer or main
   writer.
3. Move live jank out of the cross-platform E2E job into three Linux replicas and aggregate before the
   single Pages write.
4. Extend the core replica with deterministic subtitle, generated-dictionary, and click/store metrics;
   keep private dictionaries and external services out of CI.
5. Publish weekly lifecycle metrics only after the generated workload demonstrably crosses the forced
   cache bound; a cache test that never evicts is not a lifecycle benchmark.

Success means a PR cannot publish history, fewer than three valid replicas cannot create a point, live
and core writers cannot overlap, existing CLI/examples/benchmarks keep working, and `poe all` remains
green.
