# Verification records

This directory stores sanitized maintainer-side development validation for each
Dictation program/content candidate. It does not store raw machine logs or
independent users' deployment logs.

For `v2.1.0-rc.1`, the repository archive is:

```text
docs/verification/v2.1.0-rc.1/
├── README.md
└── development-validation.md
```

Fresh Ubuntu local, Ubuntu VPS and Cloudflare Workers + D1 deployments are
recorded as separate comments in the candidate Pull Request by the account that
actually performed each deployment. Copy `TEMPLATE.md` into a comment and record
the exact program and content refs, dataset digest, sanitized platform summary,
observable results, update or rollback outcomes, and unresolved limitations.

Raw logs remain on the machine where the deployment ran. Before copying any
excerpt into a Pull Request comment, remove:

- credentials, tokens, cookies, authorization headers and environment values;
- public/private IP addresses, personal domains, usernames and home paths;
- databases, learning history, recording ledgers and unpublished recordings;
- provider account IDs, D1 UUIDs, Cloudflare zones and other tenant identifiers.

Use placeholders such as `<redacted-host>` when a command needs context. A
comment must use `PASS`, `PARTIAL` or `FAIL`; failed and partial runs remain valid
evidence and must not be rewritten as successful. The maintainer may summarize
the comments after review but must not author an independent user's result.
