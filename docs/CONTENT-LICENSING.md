# Content and audio distribution policy

This policy defines the release boundary for Dictation v2.1 OSS. It is a
maintainer policy, not legal advice.

## Required metadata

Every distributable content pack must include:

- a stable pack identifier and content-pack schema version;
- the author or source of the structured data;
- an SPDX license identifier or the complete redistribution terms;
- a statement covering any adapted or third-party material;
- separate provenance and redistribution terms for bundled audio;
- no student, teacher, account, deployment, or learning-history data.

A pack without clear rights is local-only: users may load it on their own
installation, but maintainers must not publish it in a repository, release,
container image, demo site, or CDN.

## Software and content are separate

The root AGPL-3.0 license covers the application and its source code. It does
not relicense textbooks, question banks, TTS output, or recordings. Public
release artifacts include only the synthetic test fixture under
`tests/fixtures/demo-content-pack`. Production content and recordings are
versioned in the separate `dictation-content` repository. Its current
`primary-3a` release declares structured content, recordings, and content tools
under CC BY-NC 4.0; that license is not inherited by the application.

## Audio layers

Dictation supports four audio arrangements:

1. no bundled audio, with browser or deployment-time generation;
2. a content-pack audio baseline whose provider terms permit redistribution;
3. recordings made and licensed by the content-pack author;
4. unpublished runtime recordings that override the installed baseline.

Generated audio is publishable only when the maintainer has verified the
provider's current redistribution terms and recorded that basis in the pack.
Author-owned recordings may be published when the pack states their author and
license. Runtime recording ledgers, drafts, review state, and recordings made
by other users remain private unless those contributors explicitly authorize a
separate content release.

## Release review

Before a public release, a maintainer verifies the exported tree and artifacts
against this policy, scans for credentials and personal infrastructure, and
records the result in the release checklist. Any uncertainty blocks
redistribution of the affected content without blocking publication of the
software itself.
