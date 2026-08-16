# libasslite-bundle

`libasslite-bundle` is the optional native-runtime companion to `libasslite`. Platform wheels contain
libass 0.17.5 and its dynamic runtime closure; the Apache-2.0 Saitenka wheel contains none of them.

Install `saitenka[subtitle-geometry-bundle]` for the self-contained path. An explicit
`library_path=` or `LIBASSLITE_LIBRARY` always wins. Set `LIBASSLITE_BUNDLE=0` to skip this package and
return to system discovery without uninstalling it.

Release wheels are built from the vcpkg baseline in `NATIVE_SOURCES.json`, repaired for relative native
dependency lookup, and carry the installed ports' verbatim copyright files in `THIRD_PARTY_LICENSES`.
The sdist is rebuild metadata only and deliberately contains no native payload.
