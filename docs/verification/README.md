# Deployment verification records

This directory stores sanitized, reproducible acceptance reports for released
Dictation program/content combinations. It does not store raw machine logs.

For `v2.1.0-rc.1`, create one report for each supported target:

```text
docs/verification/v2.1.0-rc.1/
├── ubuntu-local.md
├── ubuntu-vps-bce.md
└── cloudflare-workers.md
```

Copy `TEMPLATE.md` for each target. Record exact program and content refs,
dataset digest, operating-system/runtime versions, commands, observable results,
update or rollback outcomes, and unresolved limitations.

Raw logs remain on the target under a timestamped private directory. Before a
report is committed or linked from a Pull Request, remove:

- credentials, tokens, cookies, authorization headers and environment values;
- public/private IP addresses, personal domains, usernames and home paths;
- databases, learning history, recording ledgers and unpublished recordings;
- provider account IDs, D1 UUIDs, Cloudflare zones and other tenant identifiers.

Use placeholders such as `<redacted-host>` when a command needs context. A PR
comment should contain only the target, pass/fail conclusion, program/content
refs, and a link to the committed report.
