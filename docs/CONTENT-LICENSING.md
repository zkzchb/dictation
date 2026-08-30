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
release artifacts include only the original content/demo-zh-cn pack. Private
compatibility packs for existing v2.0 deployments stay outside the public
history and release assets.

## Audio layers

Dictation supports three audio layers:

1. no bundled audio, with browser or deployment-time generation;
2. a content-pack audio baseline whose provider terms permit redistribution;
3. local teacher or family recordings that override the baseline.

Generated audio is publishable only when the maintainer has verified the
provider's current redistribution terms and recorded that basis in the pack.
User recordings and recording ledgers are always excluded from source and
release artifacts.

## Release review

Before a public release, a maintainer verifies the exported tree and artifacts
against this policy, scans for credentials and personal infrastructure, and
records the result in the release checklist. Any uncertainty blocks
redistribution of the affected content without blocking publication of the
software itself.
