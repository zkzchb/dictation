# Deployment verification: <target>

## Identity

| Field | Value |
|---|---|
| Date (UTC) | `<YYYY-MM-DD>` |
| Target | `<Ubuntu local / Ubuntu VPS / Cloudflare Workers>` |
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
| Content and audio validation | `<pass/fail>` | `<counts/digest>` |
| Application health | `<pass/fail>` | `<safe response fields>` |
| Lesson and word selection | `<pass/fail>` | `<counts>` |
| Static audio | `<pass/fail>` | `<HTTP status/content type>` |
| Persistence after restart | `<pass/fail>` | `<safe observation>` |
| Access control | `<pass/fail/not applicable>` | `<safe observation>` |
| Backup and restore | `<pass/fail/not applicable>` | `<safe observation>` |
| Update and rollback | `<pass/fail/not applicable>` | `<safe observation>` |

## Deviations and limitations

Record every manual step, warning, unresolved issue, and difference from the
published deployment guide. Do not turn a partial result into a pass.

## Raw-log custody

State where the private raw logs were retained and who reviewed the sanitized
report. Do not include the private path or host identity in this public file.
