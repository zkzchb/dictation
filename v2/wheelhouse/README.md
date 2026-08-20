# V2 offline wheelhouse

This directory is the frozen V2 runtime dependency set. The VPS and local V2
installers verify `sha256`, then install from this directory with `--no-index`.
They never contact PyPI or a regional Python package mirror.

Supported deployment baseline:

- CPython 3.12
- Linux with glibc (Ubuntu 24.04 is the production baseline)
- x86_64 or aarch64

All files are unmodified upstream wheels. Pure-Python wheels are shared by both
architectures; the two `pydantic-core` wheels cover x86_64 and aarch64. Package
license files are preserved inside each wheel's `.dist-info` directory.

`audioop-lts` is intentionally absent because its requirement marker applies
only to Python 3.13 and later. Supporting Python 3.13 requires a separately
generated and tested wheelhouse instead of silently downloading extra packages.

Maintainers must regenerate the complete directory and its hashes whenever
`v2/requirements.txt` changes, then prove a fresh `--no-index` installation in
CI before publishing the change.
