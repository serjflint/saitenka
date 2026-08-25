# Repository-tool tests

This suite tests development and release tools, not the shipped application. `poe loop-tools-test`
collects it separately; application `testpaths` remains `tests/`.

The tools are path-run scripts rather than an importable package. The suite-local `conftest.py` adds
`tools/` only for this collection.
