# v2.1.0-rc.1 verification archive

This directory separates maintainer development validation from independent
platform acceptance.

- [Development validation](development-validation.md) is written by the core
  maintainer account `@zkzchb` and records program-side checks.
- Ubuntu local, Ubuntu VPS and Cloudflare Workers + D1 deployment logs are
  submitted by the independent general user `@GucasWen` as comments on
  [Draft PR #30](https://github.com/zkzchb/dictation/pull/30).
- Comments are the source record for the three platform runs. They must include
  sanitized commands, public refs, observable results, failures and deviations.
- Raw logs remain private on the machine where each run took place.

A comment is not required to claim success. `PASS`, `PARTIAL` or `FAIL` are all
valid evidence states. The maintainer will only mark the release acceptance
checklist complete after all three comments have been reviewed.
