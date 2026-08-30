# Independent deployment verification comment: <target>

## Identity

| Field | Value |
|---|---|
| Date (UTC) | `<YYYY-MM-DD>` |
| Target | `<Ubuntu local / Ubuntu VPS / Cloudflare Workers>` |
| Overall result | `<PASS / PARTIAL / FAIL>` |
| Program ref / commit | `<tag> / <sha>` |
| Content ref / commit | `<tag> / <sha>` |
| Pack / version | `<pack-id> / <version>` |
| Dataset digest | `<sha256>` |
| Platform | `<sanitized OS, architecture and runtime versions>` |

## Fresh-install procedure

List the public commands and configuration choices needed to reproduce the
installation. Replace private infrastructure values with placeholders.

## Acceptance results

| Check | Result | Evidence |
|---|---|---|
| Content and audio validation | `<PASS/PARTIAL/FAIL>` | `<counts/digest>` |
| Application health | `<PASS/PARTIAL/FAIL>` | `<safe response fields>` |
| Lesson and word selection | `<PASS/PARTIAL/FAIL>` | `<counts>` |
| Static audio | `<PASS/PARTIAL/FAIL>` | `<HTTP status/content type>` |
| Persistence after restart | `<PASS/PARTIAL/FAIL>` | `<safe observation>` |
| Access control | `<PASS/PARTIAL/FAIL/N/A>` | `<safe observation>` |
| Backup and restore | `<PASS/PARTIAL/FAIL/N/A>` | `<safe observation>` |
| Update and rollback | `<PASS/PARTIAL/FAIL/N/A>` | `<safe observation>` |

## Deviations and limitations

Record every manual step, warning, unresolved issue, and difference from the
published deployment guide. Do not turn a partial result into a pass.

## Raw-log custody

State that the private raw logs were retained by the executor. Do not include a
private path, host identity or credential in the public Pull Request comment.
