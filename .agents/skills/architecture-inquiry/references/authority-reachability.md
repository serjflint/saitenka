# Authority reachability

Use this lens when ownership is disputed or a migration claims that mutable authority or policy moved.
It complements control/data flow: a layered call graph can still forward the former authority unchanged.

## Trace

For each disputed fact or policy, record:

- the authoritative owner and writer;
- the objects and public values that can reach it;
- the seam each path crosses;
- the supported execution placements in which the path exists;
- whether the path observes an immutable fact, requests an intent, performs a physical apply, or preserves
  policy/write authority.

Existing arity, host-mass, and cluster-map outputs are mechanical evidence. They do not see
every Protocol, callback, endpoint, wrapper, or nested value and cannot prove authority retirement alone.
Use symbol references and a focused AST/source guard when the claimed boundary warrants enforcement.

## Proportionate attack

Try only shapes present or plausible in the slice: direct access, aliases, forwarding wrappers, nested
containers, type aliases or Protocols, captured callbacks, endpoint/capability fields, test/tool/benchmark
writers, headless constructors, and alternate runtime placement. Record why an otherwise plausible shape is
irrelevant; do not turn the list into boilerplate.

Only demand recursive immutability when a seam promises an immutable view and mutation could recreate a
second authority. A copied narrow value is often simpler than recursively freezing a large graph. An
owner-thread physical apply bridge is healthy when its scope and reason are visible.

## Evidence without brittleness

Prefer behavioral tests through supported seams. A structural guard is justified when it protects a retired
authority path or a deliberately narrow physical bridge; match semantic access shapes, not private method
counts, field layout, or test construction that can safely evolve. Tests and benchmarks participate in the
retirement census only when they preserve a second writer or force production compatibility authority.
