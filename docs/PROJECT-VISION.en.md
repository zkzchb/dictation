# Dictation: Infrastructure for Runnable Learning Materials

Dictation starts with Chinese dictation, but its boundary is not a fixed textbook or a single exercise. The project aims to be a self-hosted learning runtime: the program presents exercises, selects items, plays or records audio, stores results, and supports review; independently released learning packs provide courses, terms, recordings, and rules; learner records stay with the operator and produce traceable, explainable feedback.

> **Stateless runtime, versioned materials, owned records, explainable feedback.**

“Stateless runtime” does not mean that a running deployment stores no data. It means that a program release does not bundle content or records specific to a family, student, or class. The program, materials, and learning state should be independently upgradeable, portable, and auditable.

## Why this design

Learning tools for children and classrooms are often tied to fixed curricula, standardized voices, and centralized accounts. Real teaching is more local: the same term may need a different order, prompt, pronunciation, or review rule for a region, school, family, or teacher. A parent’s or teacher’s voice can provide something a standardized voice cannot. Recordings and learning histories should not become centrally collected behavioral data by default.

Dictation therefore leaves important choices with the people closest to the learner:

- families and teachers decide what to teach, how to organize it, and whose voice to use;
- operators decide where the program, materials, recordings, and records live;
- the open-source community can audit the runtime, reuse deployment patterns, and add new learning modules;
- automation and AI may assist maintenance, quality checks, and feedback, but should not become an opaque sole judge.

## Four-layer model

| Layer | Responsibility | Independently evolving parts |
| --- | --- | --- |
| Learning Runtime | Present tasks, schedule items, play audio, record answers, and support review | Web app, API, deployment adapters |
| Learning Pack | Describe runnable learning materials | Courses, terms, stable IDs, audio, sequence, and rules |
| Learning Ledger | Keep the process and results that belong to learners | Progress, errors, review history, deployment-side identity mapping |
| Studio | Let families and teachers create, validate, and maintain learning packs | Recording, re-recording, human review, import, and release workflows |

The layers connect through explicit formats and stable identifiers. The runtime repository should not copy formal curriculum content back into the codebase, and a learning pack should not contain real learner records or deployment credentials. These boundaries let code, content, and data use different versions, licenses, and release cadences.

## Current reference implementation

As of v2.1.0, three deployment paths have been implemented and accepted:

- local Ubuntu deployment for home networks and development verification;
- VPS deployment for a teacher-managed studio or self-managed service;
- Cloudflare Workers + D1 deployment for a low-operations personal edition.

The `/dictation/` subpath was verified behind a real Caddy listener on port 80 with both automated checks and manual browser acceptance. The tested flow covers the home page, course loading, dictation, system and term audio, Studio, microphone permission, recording upload, playback, and persistence after service restart.

The companion `dictation-content` repository publishes the reference learning pack independently. `content-v1.0.0` contains 43 lessons, 814 knowledge points, and 894 MP3 files. Manifests, checksums, and a dataset digest fix its content identity. It is a runnable reference pack, not the only curriculum that the runtime may use.

## Product editions and intended users

V2/V3 are technical runtimes. The Editions below are user-facing product profiles. The two naming systems operate at different levels and do not map one-to-one.

| Product edition | Status | Primary users | Current deployment and boundary |
| --- | --- | --- | --- |
| Personal Edition | Available | Individuals and families | V3 on Workers/D1 is the low-operations reference, while V2 local/VPS is also usable. It is not yet equivalent to a fully authenticated multi-user service |
| Teacher Studio Edition | Core workflow available | Teachers and content maintainers | Currently based on V2 local/VPS, with recording, re-recording, and human quality review. A fuller material-import, editing, and publishing experience remains future work |
| Classroom Edition | Planned | Classes, schools, and self-hosting organizations | Intended for a self-hosted VPS or school server. Classes, students, assignments, multiple materials, and teaching feedback are not current v2.1.0 capabilities |

Developers and researchers can also reuse the auditable runtime, content format, and deployment baseline directly. The current release covers the core paths for personal practice and teacher-led content work; full multi-user authentication and classroom collaboration must remain roadmap claims.

## Current capabilities and roadmap boundary

Capabilities backed by current release evidence include versioned course packs, stable IDs, system and term audio, dictation and review flows, a recording Studio, human review pages, local/VPS/Workers deployments, and reproducible releases with checksums, SBOMs, and attestations.

Possible next steps include:

- a safer and easier studio for importing, recording, validating, and publishing materials;
- multiple packs, multiple users, and a self-hosted classroom model;
- a portable learning ledger and more detailed review strategies;
- pronunciation, pinyin, or material-quality assistance using speech or language models only with clear authorization and human review;
- additional pack-driven modules such as literacy and English dictation.

These are roadmap directions, not shipped claims. New capabilities should continue to follow data minimization, explainability, and the ability to opt out.

## Open source, self-hosting, and license boundaries

The runtime code is licensed under AGPL-3.0 to preserve reciprocity for server-side changes. The reference content pack is licensed separately under CC BY-NC 4.0, with sources and rights documented in the content repository. The code license does not automatically cover curricula, recordings, or third-party assets, and the content license does not alter the program’s license.

Self-hosting does not require every user to become an operations expert. It preserves choice: the software can run on a family device, a teacher-managed server, or a low-cost edge platform; materials and records can be exported and moved; and users can inspect what the system actually does.

## Maintenance and the AI boundary

The project may use tools such as Codex to assist maintainers with cross-language code review, regression tests, deployment documentation, dependency and security checks, learning-pack validation, and release evidence. These maintainer workflows do not imply that student-facing AI features already exist.

Learner-facing judgments should prefer deterministic rules and inspectable evidence. If model-assisted features are added later, they should preserve human review, clear disclosure, minimal data scope, and a usable non-AI path.

## Verifiable baseline

- Runtime release: [dictation v2.1.0](https://github.com/zkzchb/dictation/releases/tag/v2.1.0)
- Reference learning pack: [dictation-content content-v1.0.0](https://github.com/zkzchb/dictation-content/releases/tag/content-v1.0.0)
- Three-target deployment and release acceptance: [PR #30](https://github.com/zkzchb/dictation/pull/30)
- Repository boundaries: [Repository Architecture](REPOSITORY-ARCHITECTURE.md)
- Learning-pack contract: [Content Pack Spec v1](CONTENT-PACK-SPEC.md)

Chinese dictation is the first fully implemented and verified module. The longer-term problem is to make learning materials not merely readable or downloadable, but reliably runnable, validated, versioned, and portable—while leaving teaching decisions and data ownership with learners, families, and teachers.
