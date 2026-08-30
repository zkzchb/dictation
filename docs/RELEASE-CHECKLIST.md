# Release checklist

## Tree and rights

- [ ] `v1/` and formal content directories are absent from the release tree.
- [ ] program fixture is synthetic and its license remains present.
- [ ] no credentials, personal infrastructure, databases, ledgers or generated audio are tracked.
- [ ] program and content licenses/notices are internally consistent.
- [ ] the compatible content tag exists and its pack validates against the reviewed program commit.

## Compatibility

- [ ] content-pack schema and stable-ID rules are unchanged or have an explicit migration.
- [ ] V2 SQLite and V3 D1 selectors pass parity tests.
- [ ] an additive content update preserves existing V2 learning history.
- [ ] a destructive content update is rejected.

## Verification

- [ ] CI passes on supported Python versions.
- [ ] dependency audit has no unresolved known vulnerability.
- [ ] fresh Ubuntu local installation succeeds.
- [ ] fresh Ubuntu VPS installation, authentication, backup and restore succeed.
- [ ] Cloudflare Worker, D1 migration, static audio and API checks succeed.
- [ ] sanitized reports are committed under `docs/verification/<release>/`; raw logs remain private.

## Artifacts

- [ ] tag points to the reviewed commit.
- [ ] source archive contains program files only.
- [ ] SHA256SUMS and CycloneDX SBOM are attached.
- [ ] GitHub artifact attestation is generated.
- [ ] release notes name compatibility changes and rollback steps.
