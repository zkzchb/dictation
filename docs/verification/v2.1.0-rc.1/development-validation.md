# Dictation v2.1 development validation log

Maintainer: [@zkzchb](https://github.com/zkzchb)
Repository: [zkzchb/dictation](https://github.com/zkzchb/dictation)
Scope: program-side development validation for the v2.1 OSS candidate
Status: maintainer validation record; platform acceptance is tracked separately in Pull Request comments

## Reviewed inputs

| Item | Reference |
|---|---|
| Program candidate | `v2.1.0-rc.1` / `942e0a60247721e99c95122ec346c9ec9d2d0231` |
| Content release | `content-v1.0.0` / `6978ef6bd3fc44e0139180676460c84f14c79922` |
| Content pack | `chinese-3a` / `1.0.0` |
| Dataset digest | `b8ea48ecfb9302ba4fc05b6dfca32786360db111d76df90a865e937db6cfc43a` |
| Program license | GNU AGPL-3.0 |
| Content license | CC BY-NC 4.0, declared in the separate content repository |

The candidate uses the external content-pack v1 boundary. The program repository
contains a synthetic fixture for automated tests; the formal course materials and
published recordings are maintained in
[dictation-content](https://github.com/zkzchb/dictation-content).

## Content inventory

The reviewed `primary-3a` pack contains:

- 43 lessons, including the declared cold-start pool;
- 814 knowledge points;
- 250 character entries, 418 word entries, 108 easy-to-mistake entries and 38 polyphonic entries;
- 869 word recordings and 25 system prompt recordings, 894 audio files in total;
- a file-level checksum manifest and a dataset-level digest.

The inventory and rights metadata were checked before runtime staging. No student
records, learning history, unpublished recording ledgers, credentials or
deployment configuration were included in the program tree.

## Validation commands and results

| Check | Result | Maintainer observation |
|---|---|---|
| Pinned dependency consistency (`pip check`) | PASS | Runtime dependency set was internally consistent |
| Python compilation | PASS | `v2`, `v3`, `shared`, tools and tests compiled |
| Browser inline JavaScript parsing | PASS | All checked application pages parsed successfully |
| Deployment shell syntax | PASS | Every script under `deploy/` passed `bash -n` |
| YAML/JSON and release-file parsing | PASS | Workflow, configuration and content metadata parsed |
| Regression suite | PASS | 43 program/content regression tests passed in the reviewed baseline |
| Content-pack validation | PASS | Schema, stable IDs, paths, metadata and checksums validated |
| V2 audio staging | PASS | 894 files staged from the external pack |
| V3 public audio staging | PASS | 894 files staged with failure-safe replacement behavior |
| D1 seed/runtime export | PASS | Content runtime metadata and seed SQL generated from the same pack |
| V2 API smoke test | PASS | Health reported 43 lessons/814 knowledge points; a daily session returned 30 words and 2 polyphonic entries; an MP3 request returned HTTP 200 |
| Studio access guard | PASS | Disabled studio access returned HTTP 403 |
| Invalid-pack write protection | PASS | Validation failure left the existing target tree unchanged |

The regression count above is the count for this reviewed development baseline. A
later maintainer fix may increase the count; the CI result attached to the final
release takes precedence over this historical record.

## Design decisions covered by the validation

1. V2 (FastAPI/SQLite) and V3 (Cloudflare Workers/D1) consume the same
   content-pack contract and stable knowledge-point identities.
2. Program code, content releases and writable runtime state have separate
   ownership and update paths.
3. Content synchronization rejects destructive ID reuse, incompatible pack
   changes and undeclared audio paths.
4. Audio staging validates the complete inventory before making a replacement
   visible.
5. Runtime databases, user learning history, recording ledgers and credentials
   remain outside the public program tree.
6. The program repository keeps the V2.0 tags as rollback references while the
   current product line is V2/V3.

## Platform verification boundary

This file records what the maintainer verified during development. It is not a
substitute for a fresh-install report on each supported platform.

The independent deployment record is intentionally collected in the conversation
of [Draft PR #30](https://github.com/zkzchb/dictation/pull/30):

- Ubuntu local;
- Ubuntu VPS;
- Cloudflare Workers + D1.

Each platform comment must state the exact public program/content refs used,
sanitized runtime details, the checks performed, the result, and any deviation.
A failed or partial run must remain marked as such; it must not be rewritten as a
pass.

## Data hygiene

Only sanitized conclusions belong in this repository or PR comments. Do not post
passwords, tokens, authorization headers, Basic Auth values, IP addresses,
private domains, provider account IDs, D1 UUIDs, home paths, databases, learning
history, recording ledgers or unpublished audio.
